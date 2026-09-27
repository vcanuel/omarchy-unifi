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

from . import config
from .api import IntegrationApi
from .errors import UnifiError
from .local import collect_local
from .snapshot import SOURCES, build_snapshot
from .transport import Transport, fetch_fingerprint, split_url

KEY_HELP = """\
Create an API key in the UniFi Network app:
  Settings → Control Plane → Integrations → Create API Key
The key is stored in your desktop keyring, not in a config file."""


def _dump(data, pretty):
    json.dump(data, sys.stdout, indent=2 if pretty else None, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_status(args):
    skip = [s for s in (args.skip or "").split(",") if s]
    unknown = set(skip) - set(SOURCES)
    if unknown:
        raise SystemExit("unknown source to skip: %s (known: %s)" % (", ".join(sorted(unknown)), ", ".join(SOURCES)))
    _dump(build_snapshot(args.profile, skip), args.pretty)
    return 0


def cmd_raw(args):
    profile = config.get_profile(args.profile)
    transport = Transport(profile["url"], config.read_api_key(args.profile),
                          tls=profile["tls"], fingerprint=profile.get("fingerprint"))
    path = args.path if args.path.startswith("/") else "/" + args.path
    _dump(transport.get_json(profile["apiPrefix"].rstrip("/") + path), True)
    return 0


def _ask(prompt, default=""):
    suffix = " [%s]" % default if default else ""
    answer = input("%s%s: " % (prompt, suffix)).strip()
    return answer or default


def _confirm(prompt, default=True):
    answer = input("%s [%s] " % (prompt, "Y/n" if default else "y/N")).strip().lower()
    return default if not answer else answer in ("y", "yes", "o", "oui")


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
    "unreachable": "Check the console address and that this machine is on its network.",
    "tls": "The certificate changed while connecting; run setup again.",
    "http": "The Integration API was not found; UniFi Network 9 or later is required.",
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
        hint = SETUP_HINTS.get(exc.kind)
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
    print("UniFi setup for profile '%s'\n" % args.profile)
    existing = config.load_config()["profiles"].get(args.profile, {})
    local = collect_local() or {}
    default_url = existing.get("url") or ("https://" + local["gateway"] if local.get("gateway") else "")
    url = _normalize_url(_ask("Console URL", default_url))
    log.step("url", url)

    if _has_valid_certificate(url):
        tls, fingerprint = "system", None
        print("Certificate is trusted by the system; using normal verification.")
    else:
        tls = "pin"
        fingerprint = fetch_fingerprint(url)
        print("\nThe console uses a self-signed certificate:\n  %s" % fingerprint)
        if existing.get("fingerprint") and existing["fingerprint"] != fingerprint:
            print("  ⚠ This differs from the previously pinned certificate.")
        print("Compare it with Settings → Control Plane on the console if unsure.")
        log.step("certificate", fingerprint)
        if not _confirm("Trust and pin this certificate?"):
            return 1

    print("\n" + KEY_HELP + "\n")
    api_key = getpass.getpass("API key (hidden): ").strip()
    if not api_key:
        print("No key entered.")
        return 1

    log.step("key", "entered (%d chars)" % len(api_key))
    transport = Transport(url, api_key, tls=tls, fingerprint=fingerprint)
    print("Checking the key…")
    prefix = _detect_prefix(transport)
    log.step("prefix", prefix or "(none)")
    api = IntegrationApi(transport, prefix)
    sites = api.sites()
    log.step("sites", str(len(sites)))
    if not sites:
        raise UnifiError("http", "the console reports no sites")
    if len(sites) == 1:
        site = sites[0]
    else:
        print("\nSites:")
        for index, s in enumerate(sites, 1):
            print("  %d. %s" % (index, s.get("name") or s.get("internalReference")))
        choice = _ask("Site number", "1")
        site = sites[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(sites) else sites[0]

    config.store_api_key(args.profile, api_key)
    log.step("keyring", "stored")
    data = config.load_config()
    data["profiles"][args.profile] = {
        "url": url,
        "site": site.get("internalReference") or site.get("id"),
        "tls": tls,
        "fingerprint": fingerprint,
        "apiPrefix": prefix,
    }
    config.save_config(data)
    log.step("config", config.config_path())

    snapshot = build_snapshot(args.profile)
    log.step("snapshot", snapshot["status"])
    summary = snapshot.get("summary") or {}
    print("\n✓ Setup complete. Connected to site '%s': %s/%s devices online, %s clients." % (
        (snapshot.get("site") or {}).get("name", "?"), summary.get("devicesOnline", "?"),
        summary.get("devicesTotal", "?"), summary.get("clientsTotal", "?")))
    for warning in snapshot.get("warnings", []):
        print("  note: %s unavailable (%s)" % (warning["source"], warning["message"]))
    return 0


def cmd_forget(args):
    data = config.load_config()
    data["profiles"].pop(args.profile, None)
    config.save_config(data)
    config.clear_api_key(args.profile)
    print("Removed profile '%s' and its API key." % args.profile)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="unifi", description="UniFi status helper for the Omarchy bar")
    parser.add_argument("--profile", default="default", help="console profile (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="print a JSON snapshot of the network")
    status.add_argument("--pretty", action="store_true", help="indent the JSON")
    status.add_argument("--skip", help="comma-separated sources to skip: " + ", ".join(SOURCES))
    status.set_defaults(func=cmd_status)

    setup = sub.add_parser("setup", help="connect to a console and store its API key")
    setup.set_defaults(func=cmd_setup)

    raw = sub.add_parser("raw", help="GET a raw API path, e.g. integration/v1/sites")
    raw.add_argument("path")
    raw.set_defaults(func=cmd_raw)

    forget = sub.add_parser("forget", help="remove a profile and its API key")
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
