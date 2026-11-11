"""Read today's calendar events through the existing Google connector.

The connector is optional and needs OAuth, so this module never raises: an
unconnected account is a normal state that the brief reports once, not an
error that breaks the machine checkup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, List, Optional


@dataclass
class CalendarSummary:
    connected: bool = False
    events: List[str] = field(default_factory=list)
    error: str = ""


def _connector() -> Any:
    """Build the Google Calendar connector (patchable in tests)."""
    from openjarvis.connectors.gcalendar import GCalendarConnector

    return GCalendarConnector()


def todays_events(*, limit: int = 8, now: Optional[datetime] = None) -> CalendarSummary:
    """Return today's events as ``"HH:MM  Title"`` lines, sorted by time."""
    moment = now or datetime.now()
    try:
        connector = _connector()
        if not connector.is_connected():
            return CalendarSummary(
                connected=False,
                error=(
                    "Google Calendar is not connected (run `jarvis connect gcalendar`)."
                ),
            )
        # sync() is a generator over every calendar; 12 hours of slack covers
        # all-day events whose start is midnight in another timezone.
        since = moment.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            hours=12
        )
        documents = []
        for index, document in enumerate(connector.sync(since=since)):
            if index >= 400:
                break
            documents.append(document)
    except Exception as exc:
        return CalendarSummary(connected=False, error=f"{type(exc).__name__}: {exc}")

    seen: set[tuple[str, str]] = set()
    events: List[str] = []
    for document in documents:
        if getattr(document, "doc_type", "") != "event":
            continue
        stamp = getattr(document, "timestamp", None)
        if not isinstance(stamp, datetime) or stamp.date() != moment.date():
            continue
        title = (getattr(document, "title", "") or "Untitled event").strip()
        key = (stamp.strftime("%H:%M"), title)
        if key in seen:
            continue
        seen.add(key)
        events.append(f"{stamp:%H:%M}  {title}")
    events.sort()
    return CalendarSummary(connected=True, events=events[:limit])


__all__ = ["CalendarSummary", "todays_events"]
