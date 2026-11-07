def grade(score: int) -> str:
    if score > 100:   # MUTANT 2: negative scores no longer raise
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
