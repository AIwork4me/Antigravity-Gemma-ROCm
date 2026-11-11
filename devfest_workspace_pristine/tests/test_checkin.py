"""Frozen tests for the workshop check-in engine.

These tests define the INTENDED admission behavior and must not change:
  - any developer may register while at least one seat is open
  - the developer taking the LAST seat (39/40 taken) must be admitted
  - registration fails only once every seat is taken (40/40) or the roster
    already contains the name
"""

from checkin import CAPACITY, RegistrationError, register, register_on_roster, seats_remaining

import pytest


def test_admit_when_seats_available():
    assert register("Ada L.", 10) == {"name": "Ada L.", "seat": 11}


def test_first_registrant_gets_seat_one():
    assert register("Grace M.", 0)["seat"] == 1


def test_last_seat_is_admitted():
    """39/40 taken: the final developer MUST still get in (seat 40)."""
    assert register("Last Dev", CAPACITY - 1) == {"name": "Last Dev", "seat": CAPACITY}


def test_reject_at_full_capacity():
    with pytest.raises(RegistrationError):
        register("Overflow A", CAPACITY)


def test_reject_beyond_capacity():
    """A roster count above capacity is invalid input, not a normal rejection."""
    with pytest.raises(ValueError):
        register("Overflow B", CAPACITY + 5)


def test_reject_empty_name():
    with pytest.raises(ValueError):
        register("   ", 5)


def test_reject_duplicate_name_on_roster():
    roster = ["Ada L.", "Grace M."]
    with pytest.raises(RegistrationError):
        register_on_roster(roster, "Ada L.")


def test_duplicate_free_name_gets_next_seat():
    roster = ["Ada L.", "Grace M."]
    assert register_on_roster(roster, "Alan T.") == {"name": "Alan T.", "seat": 3}


def test_seats_remaining_counts_down():
    assert seats_remaining(0) == CAPACITY
    assert seats_remaining(39) == 1
    assert seats_remaining(40) == 0
