"""Pure functions that turn raw API payloads into snapshot pieces.

Everything here is deterministic and free of I/O so it can be tested against
recorded fixtures. Field names on the raw side vary between Network versions,
so every lookup tolerates a missing key.
"""

GATEWAY_MODEL_PREFIXES = ("UDM", "UDR", "UDW", "UCG", "UXG", "USG", "UX", "EFG")
CONSOLE_MODEL_PREFIXES = ("UCK", "UNVR", "ENVR")
AP_MODEL_PREFIXES = ("U6", "U7", "UAP", "UAL", "UWB", "E7", "UK-", "UBB")
SWITCH_MODEL_PREFIXES = ("USW", "USL", "US-", "US8", "US16", "US24", "US48", "USF")

HIGH_LATENCY_MS = 100


def _lower(value):
    return str(value or "").strip().lower()


def _num(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value))
    except ValueError:
        return None


def _features(raw):
    features = raw.get("features") or []
    if isinstance(features, dict):
        features = list(features.keys())
    return {_lower(f).replace("_", "") for f in features if f}


def device_kind(raw):
    model = str(raw.get("model") or "").upper().replace(" ", "")
    features = _features(raw)
    if "gateway" in features or model.startswith(GATEWAY_MODEL_PREFIXES):
        return "gateway"
    if model.startswith(CONSOLE_MODEL_PREFIXES):
        return "console"
    if "accesspoint" in features or model.startswith(AP_MODEL_PREFIXES):
        return "ap"
    if "switching" in features or model.startswith(SWITCH_MODEL_PREFIXES):
        return "switch"
    return "other"


def normalize_device(raw, stats=None):
    stats = stats or {}
    uplink = stats.get("uplink") or {}
    return {
        "id": str(raw.get("id") or ""),
        "name": raw.get("name") or raw.get("model") or raw.get("macAddress") or "Unknown device",
        "model": raw.get("model") or "",
        "kind": device_kind(raw),
        "state": _lower(raw.get("state")) or "unknown",
        "ip": raw.get("ipAddress") or "",
        "mac": _lower(raw.get("macAddress")),
        "firmware": raw.get("firmwareVersion") or "",
        "updateAvailable": raw.get("firmwareUpdatable") is True,
        "uptimeSec": _num(stats.get("uptimeSec")),
        "cpuPct": _num(stats.get("cpuUtilizationPct")),
        "memPct": _num(stats.get("memoryUtilizationPct")),
        "uplinkRxBps": _num(uplink.get("rxRateBps")),
        "uplinkTxBps": _num(uplink.get("txRateBps")),
        "clients": 0,
    }


def client_display_name(raw, station=None):
    station = station or {}
    return (raw.get("name") or station.get("name") or station.get("hostname")
            or raw.get("ipAddress") or raw.get("macAddress") or "Unknown client")


def normalize_client(raw, devices_by_id, station=None):
    station = station or {}
    uplink = devices_by_id.get(str(raw.get("uplinkDeviceId") or ""))
    access = raw.get("access") or {}
    kind = _lower(raw.get("type")) or ("wired" if station.get("is_wired") else "unknown")
    client = {
        "id": str(raw.get("id") or ""),
        "name": client_display_name(raw, station),
        "kind": kind,
        "ip": raw.get("ipAddress") or station.get("ip") or "",
        "mac": _lower(raw.get("macAddress")),
        "uplink": uplink["name"] if uplink else "",
        "uplinkId": uplink["id"] if uplink else "",
        "connectedAt": raw.get("connectedAt") or "",
        "guest": _lower(access.get("type")) == "guest",
        "ssid": station.get("essid") or "",
        "signalDbm": _num(station.get("signal")),
        "self": False,
    }
    return client


def index_stations(stations):
    return {_lower(s.get("mac")): s for s in stations or [] if s.get("mac")}


def _subsystem(health, name):
    for entry in health or []:
        if entry.get("subsystem") == name:
            return entry
    return None


def _bytes_rate_to_bps(value):
    rate = _num(value)
    return rate * 8 if rate is not None else None


def parse_wan(health):
    """Build the WAN block from legacy stat/health, or None if it is absent."""
    wan = _subsystem(health, "wan")
    www = _subsystem(health, "www") or {}
    if not wan:
        return None
    uptime_stats = (wan.get("uptime_stats") or {}).get("WAN") or {}
    latency = _num(www.get("latency"))
    if latency is None:
        latency = _num(uptime_stats.get("latency_average"))
    result = {
        "status": _lower(wan.get("status")) or "unknown",
        "internet": _lower(www.get("status")) or "",
        "ip": wan.get("wan_ip") or "",
        "isp": wan.get("isp_name") or wan.get("isp_organization") or "",
        "gatewayName": wan.get("gw_name") or "",
        "gatewayVersion": wan.get("gw_version") or "",
        "latencyMs": latency,
        "availabilityPct": _num(uptime_stats.get("availability")),
        "rxBps": _bytes_rate_to_bps(wan.get("rx_bytes-r")),
        "txBps": _bytes_rate_to_bps(wan.get("tx_bytes-r")),
        "uptimeSec": _num(www.get("uptime")),
        "speedtest": None,
        "secondary": None,
    }
    if _num(www.get("xput_down")) or _num(www.get("xput_up")):
        result["speedtest"] = {
            "downMbps": _num(www.get("xput_down")),
            "upMbps": _num(www.get("xput_up")),
            "pingMs": _num(www.get("speedtest_ping")),
            "at": _num(www.get("speedtest_lastrun")),
        }
    wan2 = _subsystem(health, "wan2")
    if wan2:
        result["secondary"] = {
            "status": _lower(wan2.get("status")) or "unknown",
            "ip": wan2.get("wan_ip") or "",
            "isp": wan2.get("isp_name") or wan2.get("isp_organization") or "",
        }
    return result


def build_self(local, clients, stations_by_mac, devices):
    """Merge what this machine sees with what the console says about it."""
    if not local:
        return None
    result = dict(local)
    result.update({"known": False, "clientName": "", "uplink": None, "signalDbm": None, "satisfaction": None})
    mac = _lower(local.get("mac"))
    client = next((c for c in clients if c["mac"] and c["mac"] == mac), None)
    station = stations_by_mac.get(mac) or {}
    devices_by_mac = {d["mac"]: d for d in devices if d["mac"]}
    devices_by_id = {d["id"]: d for d in devices}

    uplink = None
    if client:
        client["self"] = True
        result["known"] = True
        result["clientName"] = client["name"]
        uplink = devices_by_id.get(client["uplinkId"])
    if not uplink and station.get("ap_mac"):
        uplink = devices_by_mac.get(_lower(station.get("ap_mac")))
    if uplink:
        result["uplink"] = {"id": uplink["id"], "name": uplink["name"], "kind": uplink["kind"]}
    if station:
        result["known"] = True
        result["signalDbm"] = _num(station.get("signal"))
        result["satisfaction"] = _num(station.get("satisfaction"))
        if not result.get("ssid") and station.get("essid"):
            result["ssid"] = station["essid"]
        if not result.get("channel") and station.get("channel"):
            result["channel"] = _num(station["channel"])
    return result


def summarize(devices, clients):
    return {
        "devicesTotal": len(devices),
        "devicesOnline": sum(1 for d in devices if d["state"] == "online"),
        "updatesAvailable": sum(1 for d in devices if d["updateAvailable"]),
        "clientsTotal": len(clients),
        "clientsWired": sum(1 for c in clients if c["kind"] == "wired"),
        "clientsWireless": sum(1 for c in clients if c["kind"] == "wireless"),
        "clientsGuest": sum(1 for c in clients if c["guest"]),
    }


INFO_STATES = {"updating", "pending_adoption", "adopting", "getting_ready", "provisioning"}


def evaluate(devices, wan, self_info):
    """Return (status, issues) where status is ok, warning, or error."""
    issues = []
    for device in devices:
        state = device["state"]
        if state == "online":
            continue
        severity = "info" if state in INFO_STATES else "warning"
        label = state.replace("_", " ")
        issues.append({"severity": severity, "source": "device", "ref": device["id"],
                       "message": "%s is %s" % (device["name"], label)})
    if wan:
        if wan["status"] not in ("ok", "unknown"):
            issues.append({"severity": "error", "source": "wan", "message": "WAN is %s" % wan["status"]})
        elif wan["internet"] not in ("", "ok", "unknown"):
            issues.append({"severity": "error", "source": "wan", "message": "No internet access"})
        if wan["latencyMs"] is not None and wan["latencyMs"] > HIGH_LATENCY_MS:
            issues.append({"severity": "warning", "source": "wan",
                           "message": "High WAN latency (%d ms)" % wan["latencyMs"]})
        secondary = wan.get("secondary")
        if secondary and secondary["status"] not in ("ok", "unknown"):
            issues.append({"severity": "warning", "source": "wan", "message": "Backup WAN is %s" % secondary["status"]})
    updates = [d for d in devices if d["updateAvailable"]]
    if updates:
        issues.append({"severity": "info", "source": "firmware",
                       "message": "Firmware update available for %d device%s" % (len(updates), "" if len(updates) == 1 else "s")})
    if self_info and not self_info["known"] and self_info.get("gateway"):
        issues.append({"severity": "info", "source": "self", "message": "This machine is not a client of this site"})

    order = {"error": 2, "warning": 1}
    worst = max((order.get(i["severity"], 0) for i in issues), default=0)
    status = {2: "error", 1: "warning", 0: "ok"}[worst]
    issues.sort(key=lambda i: -order.get(i["severity"], 0))
    return status, issues
