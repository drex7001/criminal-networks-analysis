"""Prefixed ULID generation.

Split out of ``aegis.actions.service`` so modules the service itself imports —
notably :mod:`aegis.er.ledger` — can mint ids without an import cycle.  This
module deliberately imports nothing from ``aegis``.
"""

from __future__ import annotations

import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id(prefix: str) -> str:
    """Generate a prefixed ULID without adding a runtime dependency."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(secrets.token_bytes(10), "big")
    encoded = ""
    for _ in range(26):
        encoded = _CROCKFORD[value & 31] + encoded
        value >>= 5
    return f"{prefix}_{encoded}"


__all__ = ["new_id"]
