"""What this machine sees of the network, independent of the console.

This is what lets the panel say "you are on HomeNet via AP Salon" even when
the console is slow, and lets the snapshot find this machine among the
console's clients by MAC address.
"""

import json
import os
import re
import subprocess


def _run(command, timeout=3):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def split_terse(line):
    """Split an `nmcli -t` line on unescaped colons, unescaping fields."""
    fields = re.split(r"(?<!\\):", line)
    return [field.replace("\\:", ":").replace("\\\\", "\\") for field in fields]


def band_for_frequency(mhz):
    if not mhz:
        return ""
    if mhz >= 5925:
        return "6 GHz"
    if mhz >= 4900:
        return "5 GHz"
    if mhz >= 2400:
        return "2.4 GHz"
    return ""


def parse_default_route(raw):
    try:
        routes = json.loads(raw or "[]")
    except ValueError:
        return None
    routes = [r for r in routes if isinstance(r, dict) and r.get("dev")]
    if not routes:
        return None
    routes.sort(key=lambda r: r.get("metric", 0))
    return routes[0]


def parse_ipv4(raw):
    try:
        links = json.loads(raw or "[]")
    except ValueError:
        return ""
    for link in links:
        for addr in link.get("addr_info", []):
            if addr.get("family") == "inet" and addr.get("scope") == "global":
                return addr.get("local", "")
    return ""


def parse_active_wifi(raw):
    for line in (raw or "").splitlines():
        fields = split_terse(line)
        if len(fields) < 6 or fields[0] != "yes":
            continue
        freq = re.match(r"(\d+)", fields[3] or "")
        freq_mhz = int(freq.group(1)) if freq else 0
        return {
            "ssid": fields[1],
            "signal": int(fields[2]) if fields[2].isdigit() else None,
            "frequencyMhz": freq_mhz or None,
            "band": band_for_frequency(freq_mhz),
            "channel": int(fields[4]) if fields[4].isdigit() else None,
            "bssid": fields[5].lower(),
        }
    return None


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def collect_local():
    route = parse_default_route(_run(["ip", "-j", "route", "show", "default"]))
    if not route:
        return None
    iface = route["dev"]
    wireless = os.path.isdir("/sys/class/net/%s/wireless" % iface)
    info = {
        "iface": iface,
        "type": "wireless" if wireless else "wired",
        "ip": route.get("prefsrc") or parse_ipv4(_run(["ip", "-j", "-4", "addr", "show", "dev", iface])),
        "gateway": route.get("gateway", ""),
        "mac": _read("/sys/class/net/%s/address" % iface).lower(),
    }
    if wireless:
        wifi = parse_active_wifi(_run([
            "nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,FREQ,CHAN,BSSID",
            "dev", "wifi", "list", "ifname", iface, "--rescan", "no",
        ]))
        if wifi:
            info.update(wifi)
    return info
