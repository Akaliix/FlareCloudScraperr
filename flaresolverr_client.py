from __future__ import annotations

import importlib
import os
import sys
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping, Tuple

import certifi
from requests.cookies import RequestsCookieJar


_ROOT_DIR = Path(__file__).resolve().parent
_FLARESOLVER_DIR = _ROOT_DIR / "flaresolver"
if str(_FLARESOLVER_DIR) not in sys.path:
    sys.path.insert(0, str(_FLARESOLVER_DIR))

dtos = importlib.import_module("dtos")
STATUS_OK = getattr(dtos, "STATUS_OK")
V1RequestBaseCls = getattr(dtos, "V1RequestBase")
V1ResponseBaseCls = getattr(dtos, "V1ResponseBase")
flaresolverr_service = importlib.import_module("flaresolverr_service")
utils = importlib.import_module("utils")

V1RequestBaseType = Any
V1ResponseBaseType = Any

_LOCAL_PREPARED = False
_LOCAL_PREPARED_LOCK = threading.Lock()


def _prepare_local_environment() -> None:
    global _LOCAL_PREPARED
    if _LOCAL_PREPARED:
        return

    with _LOCAL_PREPARED_LOCK:
        if _LOCAL_PREPARED:
            return

        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())

        utils.get_current_platform()
        flaresolverr_service.test_browser_installation()

        _LOCAL_PREPARED = True


class FlareSolverrError(RuntimeError):
    """Raised when FlareSolverr returns an error response."""


class FlareSolverrClient:
    def __init__(self) -> None:
        _prepare_local_environment()

    def close(self) -> None:
        """Retained for API compatibility; no resources to release."""

    def request_get(
        self,
        url: str,
        *,
        proxy_url: str | None = None,
        max_timeout: int = 60000,
        return_only_cookies: bool = True,
        extra_payload: Mapping[str, object] | None = None,
    ) -> V1ResponseBaseType:
        payload: dict[str, object] = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": max_timeout,
            "returnOnlyCookies": return_only_cookies,
        }
        if proxy_url:
            payload["proxy"] = {"url": proxy_url}
        if extra_payload:
            payload.update(extra_payload)

        req = V1RequestBaseCls(payload)
        result = flaresolverr_service.controller_v1_endpoint(req)
        data = result.__dict__.copy()
        if result.solution is not None:
            data["solution"] = result.solution.__dict__

        if data.get("status") != STATUS_OK:
            # raise FlareSolverrError(data.get("message", "Unknown FlareSolverr error"))
            print("FlareSolverr returned non-OK status:", data)
            print(result)
            raise FlareSolverrError(data.get("message", "Unknown FlareSolverr error"))

        return V1ResponseBaseCls(data)

    def get_cookies(
        self,
        *,
        proxy_url: str,
        target_url: str,
    ) -> Tuple[RequestsCookieJar, str]:
        result = self.request_get(target_url, proxy_url=proxy_url)
        if not result.solution:
            raise FlareSolverrError("FlareSolverr response missing solution block")
        cookies = result.solution.cookies or []
        cookie_jar = cookies_to_jar(cookies)
        user_agent = result.solution.userAgent
        return cookie_jar, user_agent


def cookies_to_jar(cookies: Iterable[Mapping[str, object]]) -> RequestsCookieJar:
    jar = RequestsCookieJar()
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        jar.set(
            name,
            value,
            domain=cookie.get("domain"),
            path=cookie.get("path"),
        )
    return jar
