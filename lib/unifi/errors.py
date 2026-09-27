"""Error types shared by the transport, API, and collectors."""


class UnifiError(Exception):
    """A failure talking to the console, tagged with a stable kind.

    The kind ends up in the snapshot so the panel can pick a useful message
    ("check the API key" vs "console unreachable") without parsing text.
    """

    KINDS = ("unconfigured", "unreachable", "tls", "auth", "http", "parse")

    def __init__(self, kind, message, status=None):
        super().__init__(message)
        self.kind = kind if kind in self.KINDS else "http"
        self.message = message
        self.status = status

    def to_json(self):
        data = {"kind": self.kind, "message": self.message}
        if self.status is not None:
            data["status"] = self.status
        return data
