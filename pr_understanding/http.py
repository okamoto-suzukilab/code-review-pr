from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class AppError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Credentials must never follow a server-supplied redirect.
        return None


class JsonHttp:
    def __init__(self, base_url: str, headers=None, timeout=120):
        address = urlsplit(base_url)
        local = address.hostname in {"localhost", "127.0.0.1", "::1"}
        if (address.scheme != "https" and not (address.scheme == "http" and local)) or not address.hostname:
            raise AppError("API URL must use HTTPS (HTTP allowed only on localhost).")
        if address.username or address.password or address.query or address.fragment:
            raise AppError("API URL must not include credentials, query or fragment.")
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self.opener = build_opener(NoRedirect())

    def request(self, method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path, data=body, method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json", **self.headers},
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(16_000_001)
            if len(raw) > 16_000_000:
                raise AppError("API response exceeds the 16 MB limit.")
            return json.loads(raw)
        except HTTPError as exc:
            # Response bodies can contain source code or secrets; keep logs metadata-only.
            raise AppError(f"API HTTP {exc.code}: check credentials, permissions, model and quota.") from None
        except (URLError, TimeoutError, OSError):
            raise AppError("API connection failed or timed out; check endpoint and service availability.") from None
        except (ValueError, UnicodeError):
            raise AppError("API returned invalid JSON.") from None
