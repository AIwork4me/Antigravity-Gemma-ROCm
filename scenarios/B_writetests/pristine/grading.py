"""Score-to-letter grading (scenario B). Believed correct; needs a test suite."""


def grade(score: int) -> str:
    """Map a 0..100 score to a letter grade.

    A >= 90, B >= 80, C >= 70, D >= 60, F below 60.
    Raises ValueError for scores outside 0..100.
    """
    if score < 0 or score > 100:
        raise ValueError("score must be within 0..100")
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"
