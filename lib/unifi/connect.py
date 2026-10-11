"""Pick the endpoint to talk to the console through.

Every endpoint of the profile is probed at once (TCP + TLS + pinned
certificate, no API key sent), and the first one in the profile's order that
answers wins. At home that is the LAN address; away, the LAN probe fails fast
("no route to host") or times out, and a remote address or the UniFi cloud
takes over. Each probe is recorded so the panel can say what was tried.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from . import config
from .errors import UnifiError
from .transport import DEFAULT_TIMEOUT, Transport, split_url

PROBE_TIMEOUT = 2.0
REMOTE_TIMEOUT = 10.0


def describe(endpoint):
    """Short human label: "192.168.1.1", "unifi.example.com:8443", "UniFi cloud"."""
    if endpoint["kind"] == "cloud":
        name = endpoint.get("consoleName")
        return "UniFi cloud" + (" (%s)" % name if name else "")
    try:
        host, port = split_url(endpoint["url"])
    except UnifiError:
        return endpoint["url"]
    return host if port == 443 else "%s:%d" % (host, port)


def transport_for(profile_name, endpoint, key_reader=config.read_api_key):
    timeout = DEFAULT_TIMEOUT if endpoint["name"] == "local" else REMOTE_TIMEOUT
    key = key_reader(config.key_slot(profile_name, endpoint["kind"]))
    return Transport(endpoint["url"], key, tls=endpoint["tls"], fingerprint=endpoint.get("fingerprint"),
                     timeout=timeout)


def _probe(transport_factory, endpoint):
    started = time.monotonic()
    attempt = {"name": endpoint["name"], "kind": endpoint["kind"], "url": endpoint["url"],
               "label": describe(endpoint), "ok": False, "error": None, "ms": None}
    transport = None
    try:
        transport = transport_factory(endpoint)
        transport.probe(PROBE_TIMEOUT)
        attempt["ok"] = True
    except UnifiError as exc:
        attempt["error"] = exc.to_json()
    attempt["ms"] = int((time.monotonic() - started) * 1000)
    return attempt, transport


def choose(profile_name, profile, transport_factory=None):
    """Return (endpoint, transport, attempts). Endpoint and transport are None
    when nothing answered; attempts lists every probe in profile order."""
    found = config.endpoints(profile)
    if transport_factory is None:
        transport_factory = lambda endpoint: transport_for(profile_name, endpoint)  # noqa: E731
    with ThreadPoolExecutor(max_workers=max(1, len(found))) as pool:
        results = list(pool.map(lambda e: _probe(transport_factory, e), found))
    attempts = [attempt for attempt, _ in results]
    for endpoint, (attempt, transport) in zip(found, results):
        if attempt["ok"]:
            return endpoint, transport, attempts
    return None, None, attempts


def summarize_failure(attempts):
    """One UnifiError standing for a set of failed probes.

    A missing key or bad config wins (it needs setup); otherwise the main
    endpoint's reason is the one worth showing, the rest is in `attempts`.
    """
    errors = [a["error"] for a in attempts if a.get("error")]
    if not errors:
        return UnifiError("unconfigured", "no console address configured")
    for error in errors:
        if error["kind"] == "unconfigured" and len(errors) == 1:
            return UnifiError("unconfigured", error["message"])
    if len(attempts) == 1:
        return UnifiError(errors[0]["kind"], errors[0]["message"], errors[0].get("status"))
    reasons = "; ".join("%s: %s" % (a["label"], a["error"]["message"]) for a in attempts if a.get("error"))
    kinds = {e["kind"] for e in errors}
    return UnifiError("tls" if kinds == {"tls"} else "unreachable", "no endpoint answered (%s)" % reasons)
