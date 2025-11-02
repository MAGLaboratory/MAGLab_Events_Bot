"""HTTP utilities for external integrations."""

from __future__ import annotations

from typing import Any, Callable, Optional

import requests
from urllib3.util.retry import Retry


def build_session(timeout: Optional[int] = 10) -> requests.Session:
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        max_retries=Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"),
        )
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    if timeout is not None:
        wrapped = _wrap_request_with_timeout(session.request, timeout)
        session.request = wrapped  # type: ignore[method-assign]

    return session


def _wrap_request_with_timeout(
    request_fn: Callable[..., requests.Response], timeout: int
) -> Callable[..., requests.Response]:
    def _request(method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", timeout)
        return request_fn(method, url, **kwargs)

    return _request
