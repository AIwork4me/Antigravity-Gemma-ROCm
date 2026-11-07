"""Small text/number helpers (scenario A)."""


def clamp(value, lo, hi):
    """Return value clamped into the closed interval [lo, hi]."""
    if value < lo:
        return hi
    if value > hi:
        return hi
    return value
