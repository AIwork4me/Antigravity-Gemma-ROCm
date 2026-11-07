def grade(score: int) -> str:
    if score < 0 or score > 100:
        raise ValueError("score must be within 0..100")
    if score >= 90:
        return "A"
    if score >= 75:   # MUTANT 3: 75..79 must be C, mutant says B
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"
