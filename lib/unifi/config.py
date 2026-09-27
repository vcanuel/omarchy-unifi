"""Profile configuration and API key storage.

Non-secret settings live in $XDG_CONFIG_HOME/omarchy-unifi/config.json.
The API key lives in the desktop keyring (Secret Service via secret-tool),
never in that file or in the shell's shell.json, which every plugin can read.
"""

import json
import os
import subprocess

from .errors import UnifiError

CONFIG_VERSION = 1
SECRET_SERVICE = "omarchy-unifi"
DEFAULT_PREFIX = "/proxy/network"


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
    if not profile.get("url"):
        raise UnifiError("unconfigured", "profile '%s' has no console URL" % name)
    profile = dict(profile)
    profile.setdefault("site", "default")
    profile.setdefault("tls", "pin")
    profile.setdefault("apiPrefix", DEFAULT_PREFIX)
    return profile


def _secret_attrs(profile_name):
    return ["service", SECRET_SERVICE, "profile", profile_name]


def read_api_key(profile_name):
    """Return the API key: $UNIFI_API_KEY first, then the keyring."""
    env_key = os.environ.get("UNIFI_API_KEY", "").strip()
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
        raise UnifiError("unconfigured", "no API key in the keyring for profile '%s'" % profile_name)
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
