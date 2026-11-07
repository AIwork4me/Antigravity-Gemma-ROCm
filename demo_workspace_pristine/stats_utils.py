"""Small statistics helpers for the demo workspace."""


def mean(values):
    """Arithmetic mean of an iterable of numbers."""
    vals = list(values)
    if not vals:
        raise ValueError("mean() requires at least one data point")
    return sum(vals) / len(vals)


def median(values):
    """Median of an iterable of numbers.

    For an odd number of samples: the middle value of the sorted data.
    For an even number of samples: the average of the two middle values.
    """
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        raise ValueError("median() requires at least one data point")
    mid = n // 2
    if n % 2 == 1:
        return vals[mid]
    # BUG (planted): for even-length data the lower middle element is
    # returned instead of the average of the two middle elements.
    return vals[mid - 1]
