"""Profile configuration and API key storage.

Non-secret settings live in $XDG_CONFIG_HOME/omarchy-unifi/config.json.
API keys live in the desktop keyring (Secret Service via secret-tool),
never in that file or in the shell's shell.json, which every plugin can read.

A profile is one console, reachable through one or more endpoints tried in
order: its main address (`url`, usually on the LAN), then any `remotes`,
either another address for the same console (VPN, reverse proxy) or the
UniFi cloud connector. Direct endpoints share the console's API key; the
cloud uses a separate Site Manager key.
"""

import json
import os
import subprocess

from .errors import UnifiError

CONFIG_VERSION = 1
SECRET_SERVICE = "omarchy-unifi"
DEFAULT_PREFIX = "/proxy/network"
CLOUD_API = "https://api.ui.com"
CLOUD_CONSOLE_URL = "https://unifi.ui.com"


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "omarchy-unifi")


def config_path():
    return os.path.join(config_dir(), "config.json")


def load_config(path=None):
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"version": CONFIG_VERSION, "profiles": {}}
    except (OSError, ValueError) as exc:
        raise UnifiError("unconfigured", "cannot read %s (%s)" % (path, exc))
    if not isinstance(data, dict) or data.get("version") != CONFIG_VERSION:
        raise UnifiError("unconfigured", "unsupported config version in %s" % path)
    data.setdefault("profiles", {})
    return data


def save_config(data, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def get_profile(name, path=None):
    profile = load_config(path)["profiles"].get(name)
    if not profile:
        raise UnifiError("unconfigured", "profile '%s' is not set up" % name)
    if not profile.get("url") and not profile.get("remotes"):
        raise UnifiError("unconfigured", "profile '%s' has no console address" % name)
    profile = dict(profile)
    profile.setdefault("site", "default")
    profile.setdefault("tls", "pin")
    profile.setdefault("apiPrefix", DEFAULT_PREFIX)
    profile.setdefault("remotes", [])
    return profile


def endpoints(profile):
    """The profile's endpoints in the order they are tried."""
    found = []
    if profile.get("url"):
        found.append({"name": "local", "kind": "direct", "url": profile["url"],
                      "tls": profile.get("tls", "pin"), "fingerprint": profile.get("fingerprint"),
                      "apiPrefix": profile.get("apiPrefix", DEFAULT_PREFIX)})
    for index, remote in enumerate(profile.get("remotes") or []):
        if not isinstance(remote, dict) or not remote.get("url"):
            continue
        kind = "cloud" if remote.get("kind") == "cloud" else "direct"
        found.append({"name": remote.get("name") or ("cloud" if kind == "cloud" else "remote-%d" % (index + 1)),
                      "kind": kind, "url": remote["url"],
                      "tls": "system" if kind == "cloud" else remote.get("tls", "pin"),
                      "fingerprint": remote.get("fingerprint"),
                      "apiPrefix": remote.get("apiPrefix", DEFAULT_PREFIX),
                      "consoleName": remote.get("consoleName", "")})
    return found


def cloud_console_url(console_id):
    return "%s/v1/connector/consoles/%s" % (CLOUD_API, console_id)


def key_slot(profile_name, endpoint_kind="direct"):
    """Keyring name of the key an endpoint uses."""
    return profile_name + "@cloud" if endpoint_kind == "cloud" else profile_name


def _secret_attrs(profile_name):
    return ["service", SECRET_SERVICE, "profile", profile_name]


def read_api_key(profile_name):
    """Return an API key: $UNIFI_API_KEY (or $UNIFI_CLOUD_API_KEY) first, then the keyring.

    `profile_name` is a key slot from key_slot()."""
    env_key = os.environ.get("UNIFI_CLOUD_API_KEY" if profile_name.endswith("@cloud") else "UNIFI_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        result = subprocess.run(
            ["secret-tool", "lookup"] + _secret_attrs(profile_name),
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        raise UnifiError("unconfigured", "secret-tool is missing (install libsecret)")
    except subprocess.TimeoutExpired:
        raise UnifiError("unconfigured", "keyring did not answer; is it unlocked?")
    key = result.stdout.strip()
    if result.returncode != 0 or not key:
        what = "UniFi cloud key" if profile_name.endswith("@cloud") else "API key"
        raise UnifiError("unconfigured", "no %s in the keyring for profile '%s'" % (what, profile_name.split("@")[0]))
    return key


def store_api_key(profile_name, key):
    label = "UniFi API key (%s)" % profile_name
    result = subprocess.run(
        ["secret-tool", "store", "--label=" + label] + _secret_attrs(profile_name),
        input=key, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise UnifiError("unconfigured", "could not store the key: %s" % (result.stderr.strip() or "secret-tool failed"))


def clear_api_key(profile_name):
    subprocess.run(["secret-tool", "clear"] + _secret_attrs(profile_name),
                   capture_output=True, timeout=10)
