"""Collect everything the panel shows into one versioned JSON snapshot.

The snapshot is the contract between this helper and the QML side. Adding a
feature means adding a source here and a section in QML; bump SCHEMA only for
changes that would break an existing reader.
"""

import datetime
from concurrent.futures import ThreadPoolExecutor

from . import connect, normalize
from .api import IntegrationApi, LegacyApi
from .config import CLOUD_CONSOLE_URL, get_profile
from .errors import UnifiError
from .local import collect_local

SCHEMA = 1
SOURCES = ("stats", "legacy", "local")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def empty_snapshot(profile_name):
    return {
        "schema": SCHEMA,
        "generatedAt": _now(),
        "profile": profile_name,
        "status": "ok",
        "error": None,
        "connection": None,
        "console": None,
        "site": None,
        "summary": None,
        "wan": None,
        "self": None,
        "devices": [],
        "clients": [],
        "issues": [],
        "warnings": [],
    }


def pick_site(sites, wanted):
    wanted = str(wanted or "default")
    for site in sites:
        if wanted in (site.get("id"), site.get("internalReference"), site.get("name")):
            return site
    # A fresh setup stores the real reference; "default" is only the fallback
    # for configs written by hand, where the one site is what was meant.
    if wanted == "default" and len(sites) == 1:
        return sites[0]
    raise UnifiError("unconfigured", "site '%s' not found; available: %s" % (
        wanted, ", ".join(s.get("name") or s.get("internalReference") or "?" for s in sites)))


class Collector:
    """Gathers one snapshot. The transport and local probe are injectable so
    tests can replay recorded payloads."""

    def __init__(self, profile_name, profile, transport, skip=(), local_probe=collect_local):
        self.profile_name = profile_name
        self.profile = profile
        self.skip = set(skip)
        self.local_probe = local_probe
        self.integration = IntegrationApi(transport, profile["apiPrefix"])
        self.legacy = LegacyApi(transport, profile["apiPrefix"])

    def _optional(self, snapshot, source, fn, fallback):
        try:
            return fn()
        except UnifiError as exc:
            snapshot["warnings"].append(dict(exc.to_json(), source=source))
            return fallback

    def collect(self):
        snapshot = empty_snapshot(self.profile_name)
        with ThreadPoolExecutor(max_workers=8) as pool:
            local_future = pool.submit(self.local_probe) if "local" not in self.skip else None
            info_future = pool.submit(self.integration.info)
            sites = self.integration.sites()
            site = pick_site(sites, self.profile["site"])
            site_id = str(site.get("id"))
            site_ref = site.get("internalReference") or self.profile["site"]

            devices_future = pool.submit(self.integration.devices, site_id)
            clients_future = pool.submit(self.integration.clients, site_id)
            legacy_on = "legacy" not in self.skip
            health_future = pool.submit(self.legacy.health, site_ref) if legacy_on else None
            stations_future = pool.submit(self.legacy.stations, site_ref) if legacy_on else None

            raw_devices = devices_future.result()
            stats = {}
            if "stats" not in self.skip:
                futures = {d.get("id"): pool.submit(self.integration.device_statistics, site_id, d.get("id"))
                           for d in raw_devices if d.get("id") and str(d.get("state", "")).upper() == "ONLINE"}
                failed = None
                for device_id, future in futures.items():
                    try:
                        stats[device_id] = future.result()
                    except UnifiError as exc:
                        # Some models expose no statistics; only report real failures, once.
                        if exc.status != 404 and failed is None:
                            failed = exc
                if failed:
                    snapshot["warnings"].append(dict(failed.to_json(), source="stats"))

            raw_clients = clients_future.result()
            health = self._optional(snapshot, "health", health_future.result, []) if health_future else []
            stations = self._optional(snapshot, "stations", stations_future.result, []) if stations_future else []
            info = self._optional(snapshot, "info", info_future.result, {}) or {}
            local = local_future.result() if local_future else None

        devices = [normalize.normalize_device(d, stats.get(d.get("id"))) for d in raw_devices]
        devices_by_id = {d["id"]: d for d in devices}
        stations_by_mac = normalize.index_stations(stations)
        clients = [normalize.normalize_client(c, devices_by_id, stations_by_mac.get(str(c.get("macAddress") or "").lower()))
                   for c in raw_clients]
        for client in clients:
            if client["uplinkId"] in devices_by_id:
                devices_by_id[client["uplinkId"]]["clients"] += 1
        wan = normalize.parse_wan(health)
        normalize.mark_gateway(devices, (wan or {}).get("gatewayMac", ""), (local or {}).get("gateway", ""))
        self_info = normalize.build_self(local, clients, stations_by_mac, devices)

        kind_order = {"gateway": 0, "console": 1, "switch": 2, "ap": 3, "other": 4}
        devices.sort(key=lambda d: (kind_order.get(d["kind"], 9), d["name"].lower()))
        clients.sort(key=lambda c: (not c["self"], c["name"].lower()))

        status, issues = normalize.evaluate(devices, wan, self_info)
        snapshot.update({
            "status": status,
            "console": {"url": self.profile.get("consoleUrl") or self.profile.get("url") or "", "name": self.profile.get("name") or "",
                        "networkVersion": info.get("applicationVersion") or ""},
            "site": {"id": site_id, "name": site.get("name") or site_ref, "ref": site_ref},
            "summary": normalize.summarize(devices, clients),
            "wan": wan,
            "self": self_info,
            "devices": devices,
            "clients": clients,
            "issues": issues,
        })
        return snapshot


def connection_info(endpoint, attempts):
    return {
        "via": endpoint["kind"] if endpoint and endpoint["kind"] == "cloud" else (endpoint or {}).get("name"),
        "label": connect.describe(endpoint) if endpoint else "",
        "attempts": attempts,
    }


def endpoint_profile(profile, endpoint):
    """The profile as seen through one endpoint: its prefix and console link."""
    view = dict(profile, apiPrefix=endpoint["apiPrefix"])
    view["consoleUrl"] = CLOUD_CONSOLE_URL if endpoint["kind"] == "cloud" else endpoint["url"]
    return view


def drop_away_issues(snapshot):
    """Reached through a remote connection, this machine is elsewhere by
    definition; "not a client of this site" is expected, not news."""
    snapshot["issues"] = [i for i in snapshot["issues"] if i.get("source") != "self"]


def build_snapshot(profile_name, skip=(), chooser=connect.choose):
    """Return a snapshot; failures become status "down"/"unconfigured", never raise."""
    skip = set(skip)
    endpoint, attempts = None, []
    try:
        profile = get_profile(profile_name)
        endpoint, transport, attempts = chooser(profile_name, profile)
        if not endpoint:
            raise connect.summarize_failure(attempts)
        if endpoint["kind"] == "cloud":
            # The cloud connector is rate limited; one request per device
            # every refresh is not worth it.
            skip.add("stats")
        snapshot = Collector(profile_name, endpoint_profile(profile, endpoint), transport, skip).collect()
        if endpoint["name"] != "local":
            drop_away_issues(snapshot)
    except UnifiError as exc:
        snapshot = empty_snapshot(profile_name)
        snapshot["status"] = "unconfigured" if exc.kind == "unconfigured" else "down"
        snapshot["error"] = exc.to_json()
        if "local" not in skip:
            snapshot["self"] = normalize.build_self(collect_local(), [], {}, [])
    snapshot["connection"] = connection_info(endpoint, attempts)
    return snapshot


def build_demo_snapshot(skip=()):
    """A snapshot of the made-up network in tests/fixtures/sample."""
    from . import fixtures
    collector = Collector("demo", fixtures.DEMO_PROFILE, fixtures.FixtureTransport(), skip,
                          local_probe=fixtures.local_probe())
    snapshot = collector.collect()
    snapshot["connection"] = {"via": "local", "label": "192.168.1.1", "attempts": []}
    return snapshot
