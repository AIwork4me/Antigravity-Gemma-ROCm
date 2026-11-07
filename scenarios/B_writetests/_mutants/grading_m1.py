def grade(score: int) -> str:
    if score < 0 or score > 100:
        raise ValueError("score must be within 0..100")
    if score >= 85:   # MUTANT 1: boundary shifted (85 must be B)
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"
