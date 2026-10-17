"""Shared test fixtures for the HorizonView test suite."""

import pytest

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
