"""Replay recorded API payloads through the collector.

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import errno  # noqa: E402

from unifi import config, connect, fixtures, local, normalize, transport  # noqa: E402
from unifi.errors import UnifiError  # noqa: E402
from unifi.snapshot import SCHEMA, Collector  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")

PROFILE = fixtures.DEMO_PROFILE
LOCAL = fixtures.load(os.path.join(FIXTURES, "sample"), "local.json")
FakeTransport = fixtures.FixtureTransport


def collect(fixture="sample", skip=(), fail=(), local_info=LOCAL):
    transport = FakeTransport(os.path.join(FIXTURES, fixture), fail)
    snapshot = Collector("default", PROFILE, transport, skip, local_probe=lambda: dict(local_info)).collect()
    return snapshot, transport


class SnapshotTest(unittest.TestCase):
    def test_shape_and_summary(self):
        snap, _ = collect()
        self.assertEqual(snap["schema"], SCHEMA)
        self.assertEqual(snap["site"]["ref"], "default")
        self.assertEqual(snap["console"]["networkVersion"], "9.4.19")
        self.assertEqual(snap["summary"], {
            "devicesTotal": 4, "devicesOnline": 3, "updatesAvailable": 1,
            "clientsTotal": 3, "clientsWired": 1, "clientsWireless": 2, "clientsGuest": 1,
        })
        json.dumps(snap)  # must stay serializable

    def test_devices_are_typed_and_ordered(self):
        snap, _ = collect()
        self.assertEqual([d["kind"] for d in snap["devices"]], ["gateway", "switch", "ap", "ap"])
        gateway = snap["devices"][0]
        self.assertEqual(gateway["cpuPct"], 12.5)
        self.assertEqual(gateway["uptimeSec"], 864000)
        ap = next(d for d in snap["devices"] if d["name"] == "AP Salon")
        self.assertEqual(ap["clients"], 2)

    def test_offline_device_degrades_status(self):
        snap, _ = collect()
        self.assertEqual(snap["status"], "warning")
        self.assertEqual(snap["issues"][0]["message"], "AP Garage is offline")
        self.assertTrue(any(i["source"] == "firmware" for i in snap["issues"]))

    def test_wan_from_health(self):
        wan, _ = collect()
        wan = wan["wan"]
        self.assertEqual(wan["ip"], "203.0.113.7")
        self.assertEqual(wan["isp"], "Orange")
        self.assertEqual(wan["latencyMs"], 12)
        self.assertEqual(wan["rxBps"], 18000000)
        self.assertEqual(wan["speedtest"]["downMbps"], 940.5)

    def test_self_is_matched_by_mac(self):
        snap, _ = collect()
        me = snap["self"]
        self.assertTrue(me["known"])
        self.assertEqual(me["uplink"]["name"], "AP Salon")
        self.assertEqual(me["signalDbm"], -58)
        self.assertEqual(me["satisfaction"], 96)
        self.assertTrue(snap["clients"][0]["self"])

    def test_unnamed_client_falls_back_to_hostname(self):
        snap, _ = collect()
        guest = next(c for c in snap["clients"] if c["id"] == "cl-3")
        self.assertEqual(guest["name"], "guest-phone")
        self.assertTrue(guest["guest"])
        self.assertEqual(guest["ssid"], "HomeNet-Guest")

    def test_legacy_failure_is_a_warning_not_an_outage(self):
        error = UnifiError("http", "stat/health returned HTTP 401", 401)
        snap, _ = collect(fail={"/stat/": error})
        self.assertIsNone(snap["wan"])
        self.assertEqual({w["source"] for w in snap["warnings"]}, {"health", "stations"})
        self.assertEqual(snap["summary"]["devicesTotal"], 4)

    def test_skip_avoids_requests(self):
        _, transport = collect(skip=("legacy", "stats"))
        self.assertFalse(any("/stat/" in p or "/statistics/" in p for p in transport.requests))

    def test_unknown_site_raises(self):
        profile = dict(PROFILE, site="office")
        transport = FakeTransport(os.path.join(FIXTURES, "sample"))
        with self.assertRaises(UnifiError):
            Collector("default", profile, transport, local_probe=lambda: None).collect()


class NormalizeTest(unittest.TestCase):
    def test_gateway_found_by_gw_mac_even_without_feature(self):
        devices = [normalize.normalize_device({"id": "a", "model": "Express 7", "macAddress": "94:2A:6F:00:00:01",
                                               "features": ["switching", "accessPoint"]}),
                   normalize.normalize_device({"id": "b", "model": "U7 Mesh", "features": ["accessPoint"]})]
        devices[0]["kind"] = "ap"  # as an older heuristic would have left it
        normalize.mark_gateway(devices, "94:2a:6f:00:00:01", "")
        self.assertEqual([d["kind"] for d in devices], ["gateway", "ap"])

    def test_gateway_falls_back_to_default_route(self):
        devices = [normalize.normalize_device({"id": "a", "model": "Mystery", "ipAddress": "10.0.0.1"})]
        normalize.mark_gateway(devices, "", "10.0.0.1")
        self.assertEqual(devices[0]["kind"], "gateway")

    def test_marketing_names(self):
        self.assertEqual(normalize.device_kind({"model": "Express 7", "features": ["switching", "accessPoint"]}), "gateway")
        self.assertEqual(normalize.device_kind({"model": "U7 Mesh"}), "ap")
        self.assertEqual(normalize.device_kind({"model": "USW Flex 2.5G 8 PoE"}), "switch")

    def test_device_kind_heuristics(self):
        self.assertEqual(normalize.device_kind({"model": "UCG-Ultra", "features": ["switching"]}), "gateway")
        self.assertEqual(normalize.device_kind({"model": "U7-Pro"}), "ap")
        self.assertEqual(normalize.device_kind({"model": "USW-Flex-Mini"}), "switch")
        self.assertEqual(normalize.device_kind({"model": "UCK-G2-Plus"}), "console")
        self.assertEqual(normalize.device_kind({"model": "X", "features": {"accessPoint": {}}}), "ap")

    def test_wan_down_is_error(self):
        health = [{"subsystem": "wan", "status": "error"}, {"subsystem": "www", "status": "error"}]
        status, issues = normalize.evaluate([], normalize.parse_wan(health), None)
        self.assertEqual(status, "error")
        self.assertEqual(issues[0]["message"], "WAN is error")

    def test_high_latency_is_warning(self):
        health = [{"subsystem": "wan", "status": "ok"}, {"subsystem": "www", "status": "ok", "latency": 240}]
        status, _ = normalize.evaluate([], normalize.parse_wan(health), None)
        self.assertEqual(status, "warning")


class LocalTest(unittest.TestCase):
    def test_nmcli_terse_parsing(self):
        wifi = local.parse_active_wifi("no:Other:40:2412 MHz:1:AA\\:BB\n"
                                       "yes:My\\:Net:73:5200 MHz:40:02\\:00\\:00\\:00\\:AA\\:01\n")
        self.assertEqual(wifi["ssid"], "My:Net")
        self.assertEqual(wifi["bssid"], "02:00:00:00:aa:01")
        self.assertEqual(wifi["band"], "5 GHz")

    def test_default_route_prefers_lowest_metric(self):
        route = local.parse_default_route(json.dumps([
            {"dev": "wlan0", "gateway": "10.0.0.1", "metric": 600},
            {"dev": "eth0", "gateway": "10.0.0.1", "metric": 100},
        ]))
        self.assertEqual(route["dev"], "eth0")


class FakeProbe:
    def __init__(self, error=None):
        self.error = error

    def probe(self, timeout=None):
        if self.error:
            raise self.error


TWO_WAYS = {"url": "https://192.168.1.1", "tls": "pin", "fingerprint": "sha256:00", "site": "default",
            "remotes": [{"kind": "cloud", "url": config.cloud_console_url("abc"), "consoleName": "UDM Pro"}]}


def choose(profile, failing):
    def factory(endpoint):
        if endpoint["name"] in failing:
            return FakeProbe(failing[endpoint["name"]])
        return FakeProbe()
    return connect.choose("default", profile, transport_factory=factory)


class ConnectTest(unittest.TestCase):
    def test_endpoints_keep_order_and_defaults(self):
        found = config.endpoints(TWO_WAYS)
        self.assertEqual([e["name"] for e in found], ["local", "cloud"])
        self.assertEqual(found[1]["tls"], "system")
        self.assertEqual(found[1]["apiPrefix"], "/proxy/network")
        self.assertEqual(connect.describe(found[0]), "192.168.1.1")
        self.assertEqual(connect.describe(found[1]), "UniFi cloud (UDM Pro)")

    def test_cloud_key_has_its_own_slot(self):
        self.assertEqual(config.key_slot("default"), "default")
        self.assertEqual(config.key_slot("default", "cloud"), "default@cloud")

    def test_local_wins_when_it_answers(self):
        endpoint, _, attempts = choose(TWO_WAYS, {})
        self.assertEqual(endpoint["name"], "local")
        self.assertTrue(all(a["ok"] for a in attempts))

    def test_falls_back_to_cloud_away_from_home(self):
        away = UnifiError("unreachable", "cannot reach 192.168.1.1 (no route to host)")
        endpoint, _, attempts = choose(TWO_WAYS, {"local": away})
        self.assertEqual(endpoint["kind"], "cloud")
        self.assertEqual(attempts[0]["error"]["message"], away.message)

    def test_someone_elses_router_is_skipped(self):
        other = UnifiError("tls", "192.168.1.1 answers with a different certificate than the pinned one")
        endpoint, _, _ = choose(TWO_WAYS, {"local": other})
        self.assertEqual(endpoint["kind"], "cloud")

    def test_single_endpoint_failure_keeps_its_kind(self):
        profile = dict(TWO_WAYS, remotes=[])
        endpoint, _, attempts = choose(profile, {"local": UnifiError("tls", "x")})
        self.assertIsNone(endpoint)
        self.assertEqual(connect.summarize_failure(attempts).kind, "tls")

    def test_all_failing_lists_every_reason(self):
        endpoint, _, attempts = choose(TWO_WAYS, {
            "local": UnifiError("unreachable", "cannot reach 192.168.1.1 (timed out)"),
            "cloud": UnifiError("unconfigured", "no UniFi cloud key in the keyring for profile 'default'"),
        })
        self.assertIsNone(endpoint)
        error = connect.summarize_failure(attempts)
        self.assertEqual(error.kind, "unreachable")
        self.assertIn("192.168.1.1: cannot reach", error.message)
        self.assertIn("UniFi cloud (UDM Pro): no UniFi cloud key", error.message)

    def test_cloud_url_carries_a_base_path(self):
        t = transport.Transport(config.cloud_console_url("abc"), "k", tls="system")
        self.assertEqual((t.host, t.base), ("api.ui.com", "/v1/connector/consoles/abc"))
        self.assertEqual(transport.Transport("https://10.0.0.1", "k", fingerprint="sha256:00").base, "")

    def test_network_errors_read_like_reasons(self):
        self.assertEqual(transport.describe_network_error(OSError(errno.EHOSTUNREACH, "x")), "no route to host")
        self.assertEqual(transport.describe_network_error(ConnectionRefusedError()), "connection refused")
        self.assertEqual(transport.describe_network_error(TimeoutError()), "timed out")


if __name__ == "__main__":
    unittest.main()
