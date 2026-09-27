"""Thin clients for the two UniFi Network API surfaces.

- IntegrationApi: the official, versioned API (Network 9+) authenticated by
  API key. It covers sites, devices, device statistics, and clients.
- LegacyApi: the controller's internal /api/s/<site> endpoints. They are not
  documented but accept the same API key on UniFi OS, and they are the only
  source for WAN health (public IP, ISP, latency, throughput) and for
  per-station Wi-Fi details (SSID, signal). Collectors treat them as optional.
"""

from urllib.parse import quote

PAGE_LIMIT = 200
MAX_PAGES = 50


class IntegrationApi:
    def __init__(self, transport, prefix):
        self.transport = transport
        self.base = prefix.rstrip("/") + "/integration/v1"

    def get(self, path):
        return self.transport.get_json(self.base + path)

    def paged(self, path):
        items = []
        offset = 0
        for _ in range(MAX_PAGES):
            sep = "&" if "?" in path else "?"
            page = self.get("%s%soffset=%d&limit=%d" % (path, sep, offset, PAGE_LIMIT))
            if isinstance(page, list):
                return page
            data = (page or {}).get("data") or []
            items.extend(data)
            total = (page or {}).get("totalCount")
            offset += len(data)
            if not data or total is None or offset >= total:
                break
        return items

    def info(self):
        return self.get("/info")

    def sites(self):
        return self.paged("/sites")

    def devices(self, site_id):
        return self.paged("/sites/%s/devices" % quote(site_id, safe=""))

    def device_statistics(self, site_id, device_id):
        return self.get("/sites/%s/devices/%s/statistics/latest" % (quote(site_id, safe=""), quote(device_id, safe="")))

    def clients(self, site_id):
        return self.paged("/sites/%s/clients" % quote(site_id, safe=""))


class LegacyApi:
    def __init__(self, transport, prefix):
        self.transport = transport
        self.prefix = prefix.rstrip("/") + "/api"

    def _data(self, path):
        payload = self.transport.get_json(self.prefix + path) or {}
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else []

    def health(self, site_ref):
        return self._data("/s/%s/stat/health" % quote(site_ref, safe=""))

    def stations(self, site_ref):
        return self._data("/s/%s/stat/sta" % quote(site_ref, safe=""))
