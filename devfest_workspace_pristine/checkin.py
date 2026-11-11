"""Check-in engine for the fictional "AI Workshop" at DevFest 2026 Guangzhou.

The workshop room has 40 seats. This module decides whether a developer may
register for a seat, and which seat number they receive. The frozen test
suite in tests/test_checkin.py documents the intended admission rules.
"""

from __future__ import annotations

CAPACITY = 40


class RegistrationError(Exception):
    """Raised when a registration cannot be admitted."""


def _check_counts(registered: int, capacity: int) -> None:
    if not isinstance(registered, int) or isinstance(registered, bool):
        raise TypeError("registered must be an int")
    if not isinstance(capacity, int) or isinstance(capacity, bool):
        raise TypeError("capacity must be an int")
    if capacity < 1:
        raise ValueError("capacity must be at least 1")
    if registered < 0:
        raise ValueError("registered must not be negative")
    if registered > capacity:
        raise ValueError("registered exceeds capacity")


def seats_remaining(registered: int, capacity: int = CAPACITY) -> int:
    """How many of the ``capacity`` seats are still open."""
    _check_counts(registered, capacity)
    return capacity - registered


def register(name: str, registered: int, capacity: int = CAPACITY) -> dict:
    """Admit one developer and assign the next seat.

    Returns ``{"name": str, "seat": int}`` where seat is 1-based.
    Raises RegistrationError when no seat can be given.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("registrant name must not be empty")
    _check_counts(registered, capacity)
    if registered >= capacity - 1:
        raise RegistrationError(
            f"workshop is at full capacity ({registered}/{capacity} taken)"
        )
    return {"name": cleaned, "seat": registered + 1}


def register_on_roster(roster: list, name: str, capacity: int = CAPACITY) -> dict:
    """Register ``name`` against an explicit roster, rejecting duplicates."""
    if name in roster:
        raise RegistrationError(f"{name} is already registered")
    return register(name, len(roster), capacity)
