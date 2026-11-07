"""Frozen tests defining correct parse_port behavior (scenario C)."""

import pytest

from settings_parser import parse_port


def test_plain_digits():
    assert parse_port("8080") == 8080


def test_assignment_form():
    assert parse_port("port=8080") == 8080


def test_assignment_form_with_whitespace():
    assert parse_port("  port = 8080  ") == 8080


def test_non_numeric_raises():
    with pytest.raises(ValueError):
        parse_port("https")
