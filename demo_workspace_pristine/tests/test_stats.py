"""Tests for stats_utils. These define the expected (correct) behavior."""

from stats_utils import mean, median


def test_mean_basic():
    assert mean([1, 2, 3, 4]) == 2.5


def test_mean_single():
    assert mean([7]) == 7


def test_median_odd():
    assert median([3, 1, 2]) == 2
    assert median([5]) == 5
    assert median([9, 7, 8, 1, 2]) == 7


def test_median_even():
    # Even-length data must average the two middle values.
    assert median([1, 2, 3, 4]) == 2.5
    assert median([10, 20]) == 15.0
    assert median([4, 1, 3, 2]) == 2.5


def test_median_unsorted_input():
    assert median([2, 1]) == 1.5
