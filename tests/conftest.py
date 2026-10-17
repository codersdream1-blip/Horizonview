"""Shared test fixtures for the HorizonView test suite."""

import pytest
from fastapi.testclient import TestClient

import auth


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """Every test starts and ends with empty attempt windows.

    The rate limiter state is process-global by design (it must
    survive across requests), so tests need to clear it between runs.
    """
    auth.reset_rate_limits()
    yield
    auth.reset_rate_limits()


class CsrfClient(TestClient):
    """TestClient that behaves like the real page JavaScript.

    The browser's fetch code always echoes the csrf_token cookie back
    in the X-CSRF-Token header. This client does the same, reading the
    cookie from its own jar, so all API tests exercise the REAL
    security behaviour instead of bypassing it.
    """

    def request(self, method, url, **kwargs):
        token = self.cookies.get(auth.CSRF_COOKIE_NAME)
        if token and method.upper() not in ("GET", "HEAD"):
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("X-CSRF-Token", token)
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)
