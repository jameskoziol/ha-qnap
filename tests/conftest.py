"""Fixtures for QNAP tests."""

import pytest

from tests.fakes import FakeNas


@pytest.fixture
def nas(monkeypatch) -> FakeNas:
    """A fake NAS behind every requests.Session qnapstats creates."""
    import qnapstats.qnap_stats as qs

    fake = FakeNas()
    monkeypatch.setattr(qs.requests, "Session", fake.session)
    return fake
