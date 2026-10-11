"""Command line entry point: `unifi <command>`.

The panel only ever runs `unifi status`; the other commands are for humans:
first-time setup, troubleshooting, and capturing raw payloads for bug reports.
"""

import argparse
import datetime
import getpass
import json
import os
import ssl
import socket
import sys
import traceback

from . import config, connect
from .api import IntegrationApi
from .errors import UnifiError
from .local import collect_local
from .snapshot import SOURCES, build_demo_snapshot, build_snapshot, pick_site
from .transport import Transport, fetch_fingerprint, split_url

KEY_HELP = """\
Create an API key in the UniFi Network app:
  Settings → Control Plane → Integrations → Create API Key
The key is stored in your desktop keyring, not in a config file."""

CLOUD_KEY_HELP = """\
Create a Site Manager API key on unifi.ui.com (signed in as the console owner):
  unifi.ui.com → API → Create API Key
It is a different key from the console's, and is stored in your keyring too."""


def _dump(data, pretty):
    json.dump(data, sys.stdout, indent=2 if pretty else None, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_status(args):
    skip = [s for s in (args.skip or "").split(",") if s]
    unknown = set(skip) - set(SOURCES)
    if unknown:
        raise SystemExit("unknown source to skip: %s (known: %s)" % (", ".join(sorted(unknown)), ", ".join(SOURCES)))
    snapshot = build_demo_snapshot(skip) if args.demo else build_snapshot(args.profile, skip)
    _dump(snapshot, args.pretty)
    return 0


def _connect_or_fail(profile_name):
    profile = config.get_profile(profile_name)
    endpoint, transport, attempts = connect.choose(profile_name, profile)
    if not endpoint:
        raise connect.summarize_failure(attempts)
    return profile, endpoint, transport


def cmd_raw(args):
    _, endpoint, transport = _connect_or_fail(args.profile)
    path = args.path if args.path.startswith("/") else "/" + args.path
    print("via %s" % connect.describe(endpoint), file=sys.stderr)
    _dump(transport.get_json(endpoint["apiPrefix"].rstrip("/") + path), True)
    return 0


def _print_attempts(attempts, chosen=None):
    width = max([len(a["label"]) for a in attempts] + [0])
    for index, attempt in enumerate(attempts, 1):
        if attempt["ok"]:
            state = "✓ answers (%d ms)" % attempt["ms"] + ("  ← in use" if chosen and chosen["name"] == attempt["name"] else "")
        else:
            state = "✗ " + attempt["error"]["message"]
        print("  %d. %-*s  %s" % (index, width, attempt["label"], state))


def _where_am_i():
    local = collect_local() or {}
    if not local:
        return "this machine has no network connection"
    where = local.get("ssid") or local.get("iface") or "?"
    return "this machine is on %s (%s, gateway %s)" % (where, local.get("ip") or "no IP", local.get("gateway") or "none")


def cmd_check(args):
    """Explain, step by step, how the helper reaches the console."""
    profile = config.get_profile(args.profile)
    print("Profile '%s': %s\n" % (args.profile, _where_am_i()))
    print("Connections, tried in this order:")
    endpoint, transport, attempts = connect.choose(args.profile, profile)
    _print_attempts(attempts, endpoint)
    if not endpoint:
        error = connect.summarize_failure(attempts)
        print("\n✗ No connection answered.")
        print("  " + SETUP_HINTS.get(error.kind, "").replace("\n", "\n  "))
        return 2
    print("\nFetching a snapshot through %s…" % connect.describe(endpoint))
    snapshot = build_snapshot(args.profile)
    if snapshot["status"] in ("down", "unconfigured"):
        print("✗ %s" % snapshot["error"]["message"])
        hint = SETUP_HINTS.get(snapshot["error"]["kind"])
        if hint:
            print("  " + hint.replace("\n", "\n  "))
        return 2
    summary = snapshot.get("summary") or {}
    print("✓ Site '%s': %s/%s devices online, %s clients." % (
        (snapshot.get("site") or {}).get("name", "?"), summary.get("devicesOnline", "?"),
        summary.get("devicesTotal", "?"), summary.get("clientsTotal", "?")))
    for warning in snapshot.get("warnings", []):
        print("  note: %s unavailable (%s)" % (warning["source"], warning["message"]))
    return 0


def _ask(prompt, default=""):
    suffix = " [%s]" % default if default else ""
    answer = input("%s%s: " % (prompt, suffix)).strip()
    return answer or default


def _confirm(prompt, default=True):
    answer = input("%s [%s] " % (prompt, "Y/n" if default else "y/N")).strip().lower()
    return default if not answer else answer in ("y", "yes", "o", "oui")


def _choose(prompt, options, default):
    """options: [(key, label)]. Returns the chosen key."""
    for key, label in options:
        print("  %s. %s" % (key, label))
    keys = [key for key, _ in options]
    while True:
        answer = _ask(prompt, default).lower()
        if answer in keys:
            return answer
        print("  Pick one of: %s" % ", ".join(keys))


def _normalize_url(value):
    url = value.strip().rstrip("/")
    if "://" not in url:
        url = "https://" + url
    return url


def _has_valid_certificate(url):
    host, port = split_url(url)
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=host):
                return True
    except (OSError, ssl.SSLError):
        return False


def _detect_prefix(transport):
    """UniFi OS consoles proxy the Network app; a self-hosted one does not."""
    last = None
    for prefix in (config.DEFAULT_PREFIX, ""):
        try:
            IntegrationApi(transport, prefix).info()
            return prefix
        except UnifiError as exc:
            if exc.kind != "http" or exc.status != 404:
                raise
            last = exc
    raise last


SETUP_HINTS = {
    "auth": "The console rejected the key. Create it in the Network app on the console\n"
            "(Settings → Control Plane → Integrations), not on unifi.ui.com.",
    "unreachable": "The console did not answer from this network. Away from home, add a\n"
                   "remote connection (a VPN/public address, or the UniFi cloud) with `unifi setup`.",
    "tls": "Something answered with a different certificate than the one pinned at setup.\n"
           "On another network, that is usually a different router at the same address and\n"
           "is harmless. If you replaced or reset your console, run `unifi setup` again.",
    "http": "The Integration API was not found; UniFi Network 9 or later is required.",
    "unconfigured": "Run `unifi setup` to finish configuring this profile.",
}


def setup_log_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "omarchy-unifi", "setup.log")


class SetupLog:
    """Step log for the last setup run, so a failure that scrolled past (or
    closed with the terminal) can still be read. Never records the key."""

    def __init__(self):
        self.path = setup_log_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.handle = open(self.path, "w", encoding="utf-8")
        self.step("start")

    def step(self, name, detail=""):
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        self.handle.write("%s %s %s\n" % (stamp, name, detail))
        self.handle.flush()


def cmd_setup(args):
    log = SetupLog()
    try:
        code = _run_setup(args, log)
    except UnifiError as exc:
        log.step("failed", "%s: %s" % (exc.kind, exc.message))
        print("\n✗ Setup failed: %s" % exc.message)
        hint = getattr(exc, "hint", None) or SETUP_HINTS.get(exc.kind)
        if hint:
            print("  " + hint.replace("\n", "\n  "))
        return 2
    except (KeyboardInterrupt, EOFError):
        log.step("cancelled")
        raise
    except Exception:
        log.step("crashed", traceback.format_exc())
        print("\n✗ Setup crashed unexpectedly. Details: %s" % log.path)
        return 3
    log.step("finished", "exit %d" % code)
    if code != 0:
        print("\n✗ Setup did not complete; nothing was saved.")
    return code


def _run_setup(args, log):
    existing = config.load_config()["profiles"].get(args.profile)
    if not existing:
        return _setup_new(args, log)
    return _setup_menu(args, log, existing)


def _setup_new(args, log):
    print("UniFi setup for profile '%s'\n" % args.profile)
    print("How should this machine reach the console?")
    how = _choose("Choice", [
        ("1", "Directly at its address (on the LAN, or over a VPN)"),
        ("2", "Through the UniFi cloud (works from anywhere; needs a unifi.ui.com API key)"),
    ], "1")
    if how == "2":
        remote = _setup_cloud(args, log, {})
        if remote is None:
            return 1
        site = remote.pop("_site")
        _save_profile(args.profile, log, remotes=[remote], site=site)
        print("\nTip: run `unifi setup` again from home to add the console's local address;"
              "\nit is faster and is tried first when you are on that network.")
        return _finish(args, log)

    code = _setup_primary(args, log, {})
    if code != 0:
        return code
    print()
    if _confirm("Also reach the console when you are away from this network?", default=False):
        return _setup_add_remote(args, log)
    return 0


def _setup_menu(args, log, existing):
    profile = config.get_profile(args.profile)
    print("UniFi setup for profile '%s': %s\n" % (args.profile, _where_am_i()))
    print("Connections, tried in this order:")
    endpoint, _, attempts = connect.choose(args.profile, profile)
    _print_attempts(attempts, endpoint)
    has_remote = bool(profile.get("remotes"))
    options = [
        ("a", "Add a way to reach the console away from home (VPN/public address, or UniFi cloud)"),
        ("l", "Change the local console address or its API key" if profile.get("url")
              else "Add the console's local address (do this on the console's network)"),
    ]
    if has_remote:
        options.append(("r", "Remove a remote connection"))
    options += [("t", "Test the connection in detail"), ("q", "Quit")]
    print("\nWhat do you want to do?")
    default = "a" if not endpoint and not has_remote else ("t" if not endpoint else "q")
    choice = _choose("Choice", options, default)
    log.step("menu", choice)
    if choice == "a":
        return _setup_add_remote(args, log)
    if choice == "l":
        return _setup_primary(args, log, existing)
    if choice == "r":
        return _setup_remove_remote(args, log, existing)
    if choice == "t":
        print()
        return cmd_check(args)
    return 0


def _choose_site(sites, current=None):
    if not sites:
        raise UnifiError("http", "the console reports no sites")
    if current:
        try:
            return pick_site(sites, current)
        except UnifiError:
            pass
    if len(sites) == 1:
        return sites[0]
    print("\nSites:")
    for index, s in enumerate(sites, 1):
        print("  %d. %s" % (index, s.get("name") or s.get("internalReference")))
    choice = _ask("Site number", "1")
    return sites[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(sites) else sites[0]


def _site_ref(site):
    return site.get("internalReference") or site.get("id")


def _pin_certificate(url, log, previous=None):
    """Return (tls, fingerprint) for a direct address, asking to trust a self-signed one."""
    if _has_valid_certificate(url):
        print("Certificate is trusted by the system; using normal verification.")
        return "system", None
    fingerprint = fetch_fingerprint(url)
    print("\nThe console uses a self-signed certificate:\n  %s" % fingerprint)
    if previous and previous != fingerprint:
        print("  ⚠ This differs from the previously pinned certificate.")
    print("Compare it with Settings → Control Plane on the console if unsure.")
    log.step("certificate", fingerprint)
    if not _confirm("Trust and pin this certificate?"):
        return None, None
    return "pin", fingerprint


def _ask_console_key():
    print("\n" + KEY_HELP + "\n")
    return getpass.getpass("API key (hidden): ").strip()


def _setup_primary(args, log, existing):
    """Set (or replace) the console's main, usually local, address and its key."""
    local = collect_local() or {}
    default_url = existing.get("url") or ("https://" + local["gateway"] if local.get("gateway") else "")
    url = _normalize_url(_ask("Console address (on the LAN)", default_url))
    log.step("url", url)
    tls, fingerprint = _pin_certificate(url, log, existing.get("fingerprint"))
    if tls is None:
        return 1

    api_key = _ask_console_key()
    if not api_key:
        print("No key entered.")
        return 1
    log.step("key", "entered (%d chars)" % len(api_key))
    transport = Transport(url, api_key, tls=tls, fingerprint=fingerprint)
    print("Checking the key…")
    prefix = _detect_prefix(transport)
    log.step("prefix", prefix or "(none)")
    site = _choose_site(IntegrationApi(transport, prefix).sites(), existing.get("site"))

    config.store_api_key(config.key_slot(args.profile), api_key)
    log.step("keyring", "stored")
    _save_profile(args.profile, log, url=url, tls=tls, fingerprint=fingerprint, apiPrefix=prefix,
                  site=_site_ref(site))
    return _finish(args, log)


def _setup_add_remote(args, log):
    print("\nHow will you reach the console from elsewhere?")
    how = _choose("Choice", [
        ("1", "UniFi cloud: works anywhere the console is online (unifi.ui.com API key)"),
        ("2", "Another address: a VPN/Tailscale IP, a DNS name, or a port forward"),
    ], "1")
    existing = config.load_config()["profiles"].get(args.profile, {})
    remote = _setup_cloud(args, log, existing) if how == "1" else _setup_remote_direct(args, log, existing)
    if remote is None:
        return 1
    site = remote.pop("_site")
    remotes = [r for r in existing.get("remotes", []) if r.get("url") != remote["url"]
               and not (remote.get("kind") == "cloud" and r.get("kind") == "cloud")]
    remotes.append(remote)
    _save_profile(args.profile, log, remotes=remotes, site=existing.get("site") or site)
    return _finish(args, log)


def _setup_remote_direct(args, log, existing):
    print("\nThe address must lead to this same console, e.g. https://100.64.0.10 (Tailscale)"
          "\nor https://unifi.example.com:8443. The local address is still tried first.")
    url = _normalize_url(_ask("Remote address"))
    if url == "https://":
        print("No address entered.")
        return None
    log.step("remote-url", url)
    tls, fingerprint = _pin_certificate(url, log)
    if tls is None:
        return None
    try:
        api_key = config.read_api_key(config.key_slot(args.profile))
        print("Using the console API key already in the keyring.")
    except UnifiError:
        api_key = _ask_console_key()
        if not api_key:
            print("No key entered.")
            return None
        config.store_api_key(config.key_slot(args.profile), api_key)
    transport = Transport(url, api_key, tls=tls, fingerprint=fingerprint, timeout=connect.REMOTE_TIMEOUT)
    print("Checking that it is the same console…")
    prefix = _detect_prefix(transport)
    site = _choose_site(IntegrationApi(transport, prefix).sites(), existing.get("site"))
    log.step("remote-ok", prefix or "(none)")
    return {"name": "remote", "kind": "direct", "url": url, "tls": tls, "fingerprint": fingerprint,
            "apiPrefix": prefix, "_site": _site_ref(site)}


def _cloud_hosts(transport):
    payload = transport.get_json("/v1/hosts") or {}
    hosts = payload.get("data") if isinstance(payload, dict) else payload
    consoles = []
    for host in hosts or []:
        if not isinstance(host, dict) or not host.get("id"):
            continue
        state = host.get("reportedState") or {}
        name = state.get("name") or state.get("hostname") or host.get("hardwareId") or host["id"]
        model = ((state.get("hardware") or {}).get("name") or state.get("hardwareName") or "")
        if host.get("type") not in (None, "console", "network-server"):
            continue
        consoles.append({"id": host["id"], "name": name, "model": model,
                         "ip": host.get("ipAddress") or state.get("ip") or ""})
    return consoles


def _setup_cloud(args, log, existing):
    print("\n" + CLOUD_KEY_HELP + "\n")
    api_key = getpass.getpass("UniFi cloud API key (hidden): ").strip()
    if not api_key:
        print("No key entered.")
        return None
    log.step("cloud-key", "entered (%d chars)" % len(api_key))
    print("Listing your consoles…")
    try:
        consoles = _cloud_hosts(Transport(config.CLOUD_API, api_key, tls="system", timeout=connect.REMOTE_TIMEOUT))
    except UnifiError as exc:
        if exc.kind == "auth":
            exc.hint = ("unifi.ui.com rejected the key. Create it at unifi.ui.com → API while signed in\n"
                        "as the console's owner; a key from the console's Integrations page will not work here.")
        raise
    log.step("cloud-hosts", str(len(consoles)))
    if not consoles:
        raise UnifiError("http", "this UniFi account has no consoles")
    if len(consoles) == 1:
        console = consoles[0]
    else:
        print("\nConsoles on this account:")
        for index, c in enumerate(consoles, 1):
            print("  %d. %s%s" % (index, c["name"], " (%s)" % c["model"] if c["model"] else ""))
        choice = _ask("Console number", "1")
        console = consoles[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(consoles) else consoles[0]
    print("Using %s." % console["name"])

    url = config.cloud_console_url(console["id"])
    transport = Transport(url, api_key, tls="system", timeout=connect.REMOTE_TIMEOUT)
    print("Checking the Network API through the cloud…")
    try:
        sites = IntegrationApi(transport, config.DEFAULT_PREFIX).sites()
    except UnifiError as exc:
        if exc.kind == "http":
            exc.message += " (the console must be online in Site Manager and run UniFi OS 5.0.3 or later)"
        raise
    site = _choose_site(sites, existing.get("site"))
    log.step("cloud-ok", console["id"])
    config.store_api_key(config.key_slot(args.profile, "cloud"), api_key)
    return {"name": "cloud", "kind": "cloud", "url": url, "apiPrefix": config.DEFAULT_PREFIX,
            "consoleName": console["name"], "_site": _site_ref(site)}


def _setup_remove_remote(args, log, existing):
    remotes = existing.get("remotes") or []
    profile = dict(existing, url=None)
    labels = [connect.describe(e) for e in config.endpoints(dict(profile, remotes=remotes))]
    print("\nRemote connections:")
    for index, label in enumerate(labels, 1):
        print("  %d. %s" % (index, label))
    choice = _ask("Number to remove (empty to cancel)")
    if not choice.isdigit() or not 0 < int(choice) <= len(remotes):
        return 0
    removed = remotes[int(choice) - 1]
    rest = remotes[:int(choice) - 1] + remotes[int(choice):]
    if removed.get("kind") == "cloud":
        config.clear_api_key(config.key_slot(args.profile, "cloud"))
    _save_profile(args.profile, log, remotes=rest)
    print("Removed %s." % labels[int(choice) - 1])
    return 0


def _save_profile(profile_name, log, **fields):
    data = config.load_config()
    profile = data["profiles"].setdefault(profile_name, {})
    profile.update(fields)
    if not profile.get("remotes"):
        profile.pop("remotes", None)
    config.save_config(data)
    log.step("config", config.config_path())


def _finish(args, log):
    snapshot = build_snapshot(args.profile)
    log.step("snapshot", snapshot["status"])
    via = (snapshot.get("connection") or {}).get("label") or "?"
    if snapshot["status"] in ("down", "unconfigured"):
        print("\n✗ Saved, but the console did not answer: %s" % snapshot["error"]["message"])
        return 0
    summary = snapshot.get("summary") or {}
    print("\n✓ Setup complete. Connected to site '%s' via %s: %s/%s devices online, %s clients." % (
        (snapshot.get("site") or {}).get("name", "?"), via, summary.get("devicesOnline", "?"),
        summary.get("devicesTotal", "?"), summary.get("clientsTotal", "?")))
    for warning in snapshot.get("warnings", []):
        print("  note: %s unavailable (%s)" % (warning["source"], warning["message"]))
    return 0


def cmd_forget(args):
    data = config.load_config()
    data["profiles"].pop(args.profile, None)
    config.save_config(data)
    config.clear_api_key(config.key_slot(args.profile))
    config.clear_api_key(config.key_slot(args.profile, "cloud"))
    print("Removed profile '%s' and its API keys." % args.profile)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="unifi", description="UniFi status helper for the Omarchy bar")
    parser.add_argument("--profile", default="default", help="console profile (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="print a JSON snapshot of the network")
    status.add_argument("--pretty", action="store_true", help="indent the JSON")
    status.add_argument("--demo", action="store_true", help="made-up network from the sample fixtures")
    status.add_argument("--skip", help="comma-separated sources to skip: " + ", ".join(SOURCES))
    status.set_defaults(func=cmd_status)

    setup = sub.add_parser("setup", help="connect to a console, or change how it is reached")
    setup.set_defaults(func=cmd_setup)

    check = sub.add_parser("check", help="show which connections answer and why the others do not")
    check.set_defaults(func=cmd_check)

    raw = sub.add_parser("raw", help="GET a raw API path, e.g. integration/v1/sites")
    raw.add_argument("path")
    raw.set_defaults(func=cmd_raw)

    forget = sub.add_parser("forget", help="remove a profile and its API keys")
    forget.set_defaults(func=cmd_forget)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UnifiError as exc:
        print("unifi: %s" % exc.message, file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("", file=sys.stderr)
        return 130
