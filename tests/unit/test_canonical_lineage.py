"""Canonical-map lineage resolution (T20; spec 05 §5).

Pure chain arithmetic, so it belongs here rather than behind a database.  The
cycle guard is the interesting case: it cannot be produced through
``adjudicate_identity`` — each merge points a *loser* at a survivor, and a
loser has no mentions left to lose again — so the only way to reach it is a
corrupt ledger, and the only correct response is to stop.
"""

from __future__ import annotations

import pytest

from aegis.er.canonical import CanonicalMapError, _resolve

pytestmark = pytest.mark.requirement("Article-XIII", "ADR-028", "T20")


def test_an_entity_with_no_lineage_resolves_to_itself() -> None:
    assert _resolve("ent_a", {}) == "ent_a"


def test_a_single_merge_resolves_to_the_survivor() -> None:
    assert _resolve("ent_b", {"ent_b": "ent_a"}) == "ent_a"


def test_a_chain_resolves_to_its_end() -> None:
    """B→A then A→Z: B must resolve to Z, not to the intermediate A."""
    lineage = {"ent_b": "ent_a", "ent_a": "ent_z"}
    assert _resolve("ent_b", lineage) == "ent_z"
    assert _resolve("ent_a", lineage) == "ent_z"


def test_a_cycle_fails_the_rebuild_and_names_the_entities() -> None:
    """It never breaks the cycle by picking a winner (spec 05 §5).

    Picking one would produce a plausible-looking map that silently disagrees
    with the ledger — the failure mode the whole design exists to prevent.
    """
    with pytest.raises(CanonicalMapError, match="cycle") as excinfo:
        _resolve("ent_a", {"ent_a": "ent_b", "ent_b": "ent_a"})
    message = str(excinfo.value)
    assert "ent_a" in message and "ent_b" in message
    assert "corrupt ledger" in message


def test_a_longer_cycle_is_also_caught() -> None:
    with pytest.raises(CanonicalMapError, match="cycle"):
        _resolve("ent_a", {"ent_a": "ent_b", "ent_b": "ent_c", "ent_c": "ent_a"})
