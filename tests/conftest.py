"""Test fixtures: all API calls must be mocked; never use the network."""

import pytest


@pytest.fixture(autouse=True)
def prohibit_live_http(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Unmocked HTTP request prohibited in unit tests")

    monkeypatch.setattr("requests.sessions.Session.request", denied)
    monkeypatch.setattr("curl_cffi.requests.Session.request", denied)
