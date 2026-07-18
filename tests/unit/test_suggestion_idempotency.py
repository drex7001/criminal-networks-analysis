"""Suggestion idempotency keys (T17; ADR-031, spec 04 §5).

The key decides whether a re-run of an extraction pass is a *replay* (submit
nothing) or *new output* (submit a fresh suggestion).  Getting that boundary
wrong either floods the review queue with duplicates or silently swallows the
output of an improved model, so it is pinned here rather than left implicit in
the ingestion path.
"""

from __future__ import annotations

import pytest

from aegis.actions.service import suggestion_idempotency_key

pytestmark = pytest.mark.requirement("Article-VII", "ADR-031", "T17")

PAYLOAD = {
    "subject_id": "ent_a",
    "predicate": "known_as",
    "object_value": "Alias",
    "record_id": "rec_1",
}


def _key(**overrides: object) -> str:
    args = {
        "kind": "claim_draft",
        "producer": "semantic_pass",
        "producer_version": "mock+aaa",
        "payload": PAYLOAD,
    }
    args.update(overrides)
    return suggestion_idempotency_key(**args)  # type: ignore[arg-type]


def test_identical_input_replays_to_the_same_key() -> None:
    assert _key() == _key()


def test_key_ignores_payload_key_order() -> None:
    """A dict is unordered; two spellings of one proposal are one proposal."""
    shuffled = dict(reversed(list(PAYLOAD.items())))
    assert _key(payload=shuffled) == _key()


@pytest.mark.parametrize(
    "field, value",
    [
        ("kind", "claim_relation"),
        ("producer", "structural_pass"),
        # a changed model or prompt hash is a different producer, even though
        # the pass and the text are unchanged (spec 04 §4)
        ("producer_version", "mock+bbb"),
        ("payload", {**PAYLOAD, "object_value": "Different alias"}),
    ],
)
def test_any_changed_input_is_new_output(field: str, value: object) -> None:
    assert _key(**{field: value}) != _key()


def test_key_survives_non_json_scalars_in_a_payload() -> None:
    """Drafts carry dates before coercion; the digest must not raise on them."""
    from datetime import date

    assert _key(payload={**PAYLOAD, "valid_from": date(2019, 4, 21)})
