"""Frozen tests defining correct clamp behavior (scenario A)."""

from textutils import clamp


def test_clamp_middle():
    assert clamp(5, 0, 10) == 5


def test_clamp_below_lower_bound():
    assert clamp(-3, 0, 10) == 0


def test_clamp_above_upper_bound():
    assert clamp(15, 0, 10) == 10


def test_clamp_on_boundaries():
    assert clamp(0, 0, 10) == 0
    assert clamp(10, 0, 10) == 10
