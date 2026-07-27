"""Random redemption code generation."""

import secrets
import string

# Excludes visually ambiguous characters (0/O, 1/I/l) to cut down on
# transcription errors when a viewer types a code out.
_ALPHABET = "".join(
    c for c in (string.ascii_uppercase + string.digits) if c not in "01OIL"
)


def generate_code(length: int = 10) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
