"""HTTPS transport with certificate pinning.

UniFi consoles serve a self-signed certificate, so the usual CA check cannot
work. Rather than turning verification off, setup records the SHA-256 of the
certificate the console presented, and every request refuses to send the API
key to a server presenting anything else. Consoles behind a real certificate
(a custom domain, a reverse proxy) can use normal CA verification instead.
"""

import hashlib
import http.client
import json
import socket
import ssl
from urllib.parse import urlsplit

from .errors import UnifiError

DEFAULT_TIMEOUT = 5.0


def normalize_fingerprint(value):
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:"):]
    return text.replace(":", "")


def format_fingerprint(hex_digest):
    digest = normalize_fingerprint(hex_digest)
    return "sha256:" + ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def split_url(url):
    parts = urlsplit(str(url or "").strip())
    if parts.scheme != "https" or not parts.hostname:
        raise UnifiError("unconfigured", "console URL must look like https://192.168.1.1")
    return parts.hostname, parts.port or 443


def fetch_fingerprint(url, timeout=DEFAULT_TIMEOUT):
    """Return the SHA-256 fingerprint of the certificate the console presents."""
    host, port = split_url(url)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as exc:
        raise UnifiError("unreachable", "cannot reach %s:%d (%s)" % (host, port, exc))
    return format_fingerprint(hashlib.sha256(der).hexdigest())


class Transport:
    """Issues JSON GET requests against one console.

    A new connection per request keeps it safe to share across the collector
    thread pool; consoles are on the LAN, so the handshake cost is small.
    """

    def __init__(self, url, api_key, tls="pin", fingerprint=None, timeout=DEFAULT_TIMEOUT):
        self.host, self.port = split_url(url)
        self.api_key = api_key
        self.tls = tls
        self.fingerprint = normalize_fingerprint(fingerprint)
        self.timeout = timeout
        if tls == "pin" and not self.fingerprint:
            raise UnifiError("unconfigured", "no pinned certificate fingerprint; run setup again")

    def _context(self):
        if self.tls == "system":
            return ssl.create_default_context()
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _connect(self):
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=self._context())
        try:
            conn.connect()
        except ssl.SSLCertVerificationError as exc:
            conn.close()
            raise UnifiError("tls", "certificate rejected: %s" % exc.verify_message)
        except (OSError, ssl.SSLError) as exc:
            conn.close()
            raise UnifiError("unreachable", "cannot reach %s (%s)" % (self.host, exc))
        if self.tls == "pin":
            der = conn.sock.getpeercert(binary_form=True)
            if hashlib.sha256(der).hexdigest() != self.fingerprint:
                conn.close()
                raise UnifiError("tls", "console certificate changed; run setup again if this is expected")
        return conn

    def get_json(self, path):
        conn = self._connect()
        try:
            conn.request("GET", path, headers={
                "X-API-KEY": self.api_key,
                "Accept": "application/json",
            })
            response = conn.getresponse()
            body = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise UnifiError("unreachable", "request to %s failed (%s)" % (path, exc))
        finally:
            conn.close()

        if response.status in (401, 403):
            raise UnifiError("auth", "API key rejected (HTTP %d)" % response.status, response.status)
        if response.status >= 400:
            raise UnifiError("http", "%s returned HTTP %d" % (path, response.status), response.status)
        try:
            return json.loads(body.decode("utf-8") or "null")
        except (UnicodeDecodeError, ValueError):
            raise UnifiError("parse", "%s did not return JSON" % path)
