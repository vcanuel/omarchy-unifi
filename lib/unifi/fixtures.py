"""Replay recorded API payloads instead of talking to a console.

Used by the test suite and by `unifi status --demo`, which renders the panel
from made-up data: handy for UI work without a console, and for screenshots
that do not leak anyone's network.
"""

import json
import os

from .errors import UnifiError

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "fixtures", "sample")

DEMO_PROFILE = {"url": "https://192.168.1.1", "site": "default", "tls": "pin",
                "fingerprint": "sha256:00", "apiPrefix": "/proxy/network"}


class FixtureTransport:
    """Serves fixture files by matching the tail of each request path."""

    ROUTES = [
        ("/integration/v1/info", "info.json"),
        ("/integration/v1/sites?", "sites.json"),
        ("/devices?", "devices.json"),
        ("/clients?", "clients.json"),
        ("/stat/health", "health.json"),
        ("/stat/sta", "stations.json"),
    ]

    def __init__(self, fixture_dir=SAMPLE_DIR, fail=()):
        self.dir = fixture_dir
        self.fail = dict(fail)
        self.requests = []

    def get_json(self, path):
        self.requests.append(path)
        for needle, error in self.fail.items():
            if needle in path:
                raise error
        if "/statistics/latest" in path:
            name = "stats-%s.json" % path.split("/devices/")[1].split("/")[0]
        else:
            name = next((f for needle, f in self.ROUTES if needle in path), None)
        if not name or not os.path.exists(os.path.join(self.dir, name)):
            raise UnifiError("http", "%s returned HTTP 404" % path, 404)
        return load(self.dir, name)


def load(fixture_dir, name):
    with open(os.path.join(fixture_dir, name), encoding="utf-8") as handle:
        return json.load(handle)


def local_probe(fixture_dir=SAMPLE_DIR):
    return lambda: load(fixture_dir, "local.json")
