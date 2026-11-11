from __future__ import annotations

from datetime import datetime

import openjarvis.pc.calendar as calendar_module
from openjarvis.connectors._stubs import Document


class _FakeConnector:
    def __init__(self, documents: list[Document]) -> None:
        self._documents = documents

    def is_connected(self) -> bool:
        return True

    def sync(self, *, since=None, cursor=None):
        return iter(self._documents)


class _Disconnected:
    def is_connected(self) -> bool:
        return False


def test_todays_events_filters_to_today_and_sorts(monkeypatch) -> None:
    documents = [
        Document(
            doc_id="1",
            source="gcalendar",
            doc_type="event",
            content="",
            title="Standup",
            timestamp=datetime(2026, 11, 11, 15, 0),
        ),
        Document(
            doc_id="2",
            source="gcalendar",
            doc_type="event",
            content="",
            title="Breakfast",
            timestamp=datetime(2026, 11, 11, 8, 30),
        ),
        Document(
            doc_id="3",
            source="gcalendar",
            doc_type="event",
            content="",
            title="Tomorrow",
            timestamp=datetime(2026, 11, 12, 9, 0),
        ),
        Document(
            doc_id="4",
            source="gmail",
            doc_type="message",
            content="",
            title="Not an event",
            timestamp=datetime(2026, 11, 11, 9, 0),
        ),
    ]
    monkeypatch.setattr(
        calendar_module, "_connector", lambda: _FakeConnector(documents)
    )

    summary = calendar_module.todays_events(now=datetime(2026, 11, 11, 7, 0))

    assert summary.connected
    assert summary.events == ["08:30  Breakfast", "15:00  Standup"]


def test_disconnected_calendar_is_not_an_error(monkeypatch) -> None:
    monkeypatch.setattr(calendar_module, "_connector", lambda: _Disconnected())
    summary = calendar_module.todays_events(now=datetime(2026, 11, 11, 7, 0))
    assert summary.connected is False
    assert "not connected" in summary.error


def test_connector_failure_is_swallowed(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("no credentials")

    monkeypatch.setattr(calendar_module, "_connector", _boom)
    summary = calendar_module.todays_events()
    assert summary.connected is False
    assert "no credentials" in summary.error
