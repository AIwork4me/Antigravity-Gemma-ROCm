# Requirements — grading module

`grading.py` exposes `grade(score: int) -> str` with this specification:

1. `score >= 90` → `"A"`
2. `80 <= score < 90` → `"B"`
3. `70 <= score < 80` → `"C"`
4. `60 <= score < 70` → `"D"`
5. `score < 60` → `"F"`
6. `score < 0` or `score > 100` → raises `ValueError`

Boundary values are part of the contract (e.g. 89 → B, 90 → A, 59 → F, 60 → D).

The implementation is believed to be correct, but the module currently has
**no tests**. Your task: author a pytest suite at `tests/test_grading.py`
that verifies this contract — including the boundary values and the error
cases — then run it and make sure everything passes.
