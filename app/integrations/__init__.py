"""What every integration shares: the switch that turns a client off, and the one way
Keiko calls a service over HTTP, never waiting forever and always timed.

Every call waits at most `Dependencies.HTTP_CONNECT_TIMEOUT_SECONDS` to connect and
`Dependencies.HTTP_READ_TIMEOUT_SECONDS` between two reads, unless it gives its own
`timeout`, and is counted in `keiko_dependency_latency_seconds` under the dependency
it names (docs/analytics.md).
"""
from typing import Any, Dict

import requests

from app.constants import Dependencies
from app.services import metrics


def check_integration_enabled(func):
    def wrapper(self, *args, **kwargs):
        if not self.enabled:
            return None
        return func(self, *args, **kwargs)
    return wrapper


def http_get(dependency: str, url: str, **options: Any) -> requests.Response:
    """GET `url` from `dependency`."""
    with metrics.timed(dependency):
        return requests.get(url, **_bounded(options))


def http_post(dependency: str, url: str, **options: Any) -> requests.Response:
    """POST to `url` on `dependency`."""
    with metrics.timed(dependency):
        return requests.post(url, **_bounded(options))


def http_put(dependency: str, url: str, **options: Any) -> requests.Response:
    """PUT `url` on `dependency`."""
    with metrics.timed(dependency):
        return requests.put(url, **_bounded(options))


def http_delete(dependency: str, url: str, **options: Any) -> requests.Response:
    """DELETE `url` on `dependency`."""
    with metrics.timed(dependency):
        return requests.delete(url, **_bounded(options))


def _bounded(options: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "timeout": (
            Dependencies.HTTP_CONNECT_TIMEOUT_SECONDS,
            Dependencies.HTTP_READ_TIMEOUT_SECONDS,
        ),
        **options,
    }
