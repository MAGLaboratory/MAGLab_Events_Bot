"""Shared rules for events that imply in-person access to the lab."""

from __future__ import annotations

import re

CANCELLED_EVENT_MARKER = re.compile(r"cancel", re.IGNORECASE)
ONLINE_EVENT_MARKER = re.compile(r"(?<![A-Za-z])online(?![A-Za-z])", re.IGNORECASE)


def is_public_in_person_event(name: str) -> bool:
    """Whether an event should contribute to public opening hours."""
    normalized = name.strip()
    return (
        bool(normalized)
        and not ONLINE_EVENT_MARKER.search(normalized)
        and not CANCELLED_EVENT_MARKER.search(normalized)
        and "we are" not in normalized.casefold()
    )
