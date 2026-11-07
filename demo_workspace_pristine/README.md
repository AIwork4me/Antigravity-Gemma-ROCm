# Stats Utils — demo workspace

A tiny Python project used for the GDG DevFest "local AI agent fixes a real bug" demo.

- `stats_utils.py` — a few statistics helpers
- `tests/test_stats.py` — pytest suite

**Known state**: the test suite currently FAILS. A bug has been planted in
`stats_utils.py`. The agent's job: read the code, run the tests, find the bug,
fix it, and make `pytest` green — without breaking the passing tests.
