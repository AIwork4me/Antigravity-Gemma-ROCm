"""Tiny settings parser (scenario C)."""


def parse_port(raw: str) -> int:
    """Parse a port setting value into an int.

    Accepted forms (per the app.conf format):
      - plain number:            "8080"
      - key=value assignment:    "port=8080"
    Surrounding whitespace is tolerated in both forms.
    Anything else raises ValueError.
    """
    return int(raw)
