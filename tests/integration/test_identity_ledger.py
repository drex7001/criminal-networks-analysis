"""Identity decision ledger against real PostgreSQL (T17; ADR-028, spec 05 §2).

These live at the integration layer on purpose: the one-active-membership
invariant is a *partial unique index*, not application logic, so proving it
requires the database that enforces it.  A component-layer double would only
prove that the double agrees with itself.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis.actions import ActionContext, ActionService, ActionValidationError, new_id
from aegis.er.ledger import (
    BASELINE_REVISION,
    LedgerError,
    active_entity_for_mention,
    active_revision_id,
    open_membership,
    resolve_norm_key,
)
from aegis.store import (
    Claim,
    Entity,
    IdentityMembership,
    IdentityRevision,
    Mention,
    Source,
    SourceRecord,
)
from tests.support.database import migrated_test_engine

pytestmark = pytest.mark.requirement("Article-V", "Article-VII", "ADR-028", "T17")


@pytest.fixture(scope="module")
def ledger_engine(test_database_url: str, alembic_config: Config) -> sa.Engine:
    with migrated_test_engine(test_database_url, alembic_config) as engine:
        yield engine


@pytest.fixture()
def seeded(ledger_engine: sa.Engine):
    """One record, two entities, two mentions of the same name key."""
    ids = {key: new_id(prefix) for key, prefix in {
        "source": "src",
        "record": "rec",
        "entity_a": "ent",
        "entity_b": "ent",
        "mention_a": "men",
        "mention_b": "men",
    }.items()}
    # Tests in this module share one database and some of them commit, so the
    # blocking key is made unique per test — otherwise a norm_key assertion
    # would read a membership an earlier test left behind.
    norm_key = f"nimal_perera_{ids['record'].lower()}"
    session = Session(ledger_engine)
    with session.begin():
        session.add(
            Source(source_id=ids["source"], source_type="open_source", name="T17 source")
        )
        session.add(
            SourceRecord(
                record_id=ids["record"],
                source_id=ids["source"],
                ingest_key=new_id("key"),
                content_hash="c" * 64,
                storage_uri="test://ledger",
            )
        )
        session.add_all(
            [
                Entity(entity_id=ids["entity_a"], entity_type="person", label="Nimal P"),
                Entity(entity_id=ids["entity_b"], entity_type="person", label="Nimal P."),
            ]
        )
        session.flush()
        session.add_all(
            [
                Mention(
                    mention_id=ids["mention_a"],
                    record_id=ids["record"],
                    raw_text="Nimal Perera",
                    norm_key=norm_key,
                    char_start=10,
                    char_end=22,
                    script="Latn",
                    language="en",
                ),
                Mention(
                    mention_id=ids["mention_b"],
                    record_id=ids["record"],
                    raw_text="නිමල් පෙරේරා",
                    norm_key=norm_key,
                    script="Sinh",
                    language="si",
                ),
            ]
        )
    try:
        yield {**ids, "session": session, "norm_key": norm_key}
    finally:
        session.close()


# ── the baseline ─────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_migration_seeds_exactly_one_decisionless_baseline(seeded) -> None:
    """Revision 0 is a baseline, not a decision anyone made (spec 05 §7)."""
    session: Session = seeded["session"]
    decisionless = session.scalars(
        select(IdentityRevision.revision_id).where(IdentityRevision.decision_id.is_(None))
    ).all()
    assert decisionless == [BASELINE_REVISION]
    assert active_revision_id(session) >= BASELINE_REVISION


# ── the invariant ────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_database_refuses_a_second_active_membership(seeded) -> None:
    """ADR-028 §2 is enforced by ``ux_membership_one_active``, not by code."""
    session: Session = seeded["session"]
    open_membership(
        session, mention_id=seeded["mention_a"], entity_id=seeded["entity_a"]
    )
    # bypass open_membership's own guard to prove the *database* refuses it
    session.add(
        IdentityMembership(
            membership_id=new_id("mem"),
            mention_id=seeded["mention_a"],
            entity_id=seeded["entity_b"],
            opened_revision_id=BASELINE_REVISION,
        )
    )
    with pytest.raises(IntegrityError, match="ux_membership_one_active"):
        session.flush()
    session.rollback()


@pytest.mark.integration
def test_open_membership_refuses_to_move_an_adjudicated_mention(seeded) -> None:
    """Moving a mention is an adjudication, never a membership open (ADR-027)."""
    session: Session = seeded["session"]
    open_membership(
        session, mention_id=seeded["mention_a"], entity_id=seeded["entity_a"]
    )
    with pytest.raises(LedgerError, match="adjudicate_identity"):
        open_membership(
            session, mention_id=seeded["mention_a"], entity_id=seeded["entity_b"]
        )
    session.rollback()


@pytest.mark.integration
def test_closing_a_membership_frees_the_mention(seeded) -> None:
    """The invariant constrains *active* rows only — history is never deleted.

    This is the precondition for reversal: a closed row stays on the table
    naming the revision that closed it, so the pre-merge state remains
    reconstructible (spec 05 §2).
    """
    session: Session = seeded["session"]
    first = open_membership(
        session, mention_id=seeded["mention_a"], entity_id=seeded["entity_a"]
    )
    closing = IdentityRevision(decision_id=None)
    session.add(closing)
    session.flush()
    first.closed_revision_id = closing.revision_id
    session.flush()

    second = open_membership(
        session,
        mention_id=seeded["mention_a"],
        entity_id=seeded["entity_b"],
        revision_id=closing.revision_id,
    )
    assert active_entity_for_mention(session, seeded["mention_a"]) == seeded["entity_b"]
    # both rows survive; only one is active
    assert session.scalar(
        select(func.count())
        .select_from(IdentityMembership)
        .where(IdentityMembership.mention_id == seeded["mention_a"])
    ) == 2
    assert second.opened_revision_id == closing.revision_id
    session.rollback()


@pytest.mark.integration
def test_norm_key_resolves_only_through_an_active_membership(seeded) -> None:
    """A shared norm_key is a blocking key, not identity (Article V)."""
    session: Session = seeded["session"]
    assert resolve_norm_key(session, seeded["norm_key"]) is None  # nothing adjudicated
    open_membership(
        session, mention_id=seeded["mention_a"], entity_id=seeded["entity_a"]
    )
    assert resolve_norm_key(session, seeded["norm_key"]) == seeded["entity_a"]
    session.rollback()


# ── claims carry their identity context ──────────────────────────────────────


@pytest.mark.integration
def test_claim_stamps_the_active_revision(seeded) -> None:
    """ADR-029 §2: a claim records what identity meant when it was made."""
    session: Session = seeded["session"]
    service = ActionService(session)
    context = ActionContext(actor="user:analyst", purpose="T17 test")
    claim = service.record_claim(
        context,
        subject_id=seeded["entity_a"],
        predicate="known_as",
        object_value="Nimal",
        assertion_type="reported",
        record_id=seeded["record"],
    )
    session.commit()
    assert claim.identity_revision_id == active_revision_id(session)


@pytest.mark.integration
def test_anchor_must_not_contradict_its_entity_argument(seeded) -> None:
    session: Session = seeded["session"]
    open_membership(
        session, mention_id=seeded["mention_a"], entity_id=seeded["entity_a"]
    )
    session.commit()
    service = ActionService(session)
    context = ActionContext(actor="user:analyst", purpose="T17 test")

    with pytest.raises(ActionValidationError, match="belongs to entity"):
        service.record_claim(
            context,
            subject_id=seeded["entity_b"],  # mention_a belongs to entity_a
            predicate="known_as",
            object_value="Nimal",
            assertion_type="reported",
            record_id=seeded["record"],
            subject_mention_id=seeded["mention_a"],
        )

    # the matching anchor is accepted and persisted
    claim = service.record_claim(
        context,
        subject_id=seeded["entity_a"],
        predicate="known_as",
        object_value="Nimal",
        assertion_type="reported",
        record_id=seeded["record"],
        subject_mention_id=seeded["mention_a"],
    )
    session.commit()
    assert claim.subject_mention_id == seeded["mention_a"]


@pytest.mark.integration
def test_object_anchor_without_an_entity_object_is_refused(seeded) -> None:
    """The DB owns this one: no anchor without an entity argument."""
    session: Session = seeded["session"]
    service = ActionService(session)
    context = ActionContext(actor="user:analyst", purpose="T17 test")
    with pytest.raises(ActionValidationError, match="not an entity argument"):
        service.record_claim(
            context,
            subject_id=seeded["entity_a"],
            predicate="known_as",
            object_value="Nimal",
            assertion_type="reported",
            record_id=seeded["record"],
            object_mention_id=seeded["mention_b"],
        )
    session.rollback()

    # and the constraint holds even if the actions layer were bypassed
    with pytest.raises(IntegrityError, match="ck_claim_object_anchor_needs_entity"):
        session.add(
            Claim(
                claim_id=new_id("clm"),
                subject_id=seeded["entity_a"],
                predicate="known_as",
                object_value="Nimal",
                object_mention_id=seeded["mention_b"],
                assertion_type="reported",
                record_id=seeded["record"],
                identity_revision_id=BASELINE_REVISION,
                ontology_version="1.0.0",
            )
        )
        session.flush()
    session.rollback()


@pytest.mark.integration
def test_two_mentions_of_one_name_can_hold_separate_identities(seeded) -> None:
    """A shared ``norm_key`` never implies a shared entity (Article V).

    The transliteration pair below is exactly the case ER must *propose* and a
    human must decide — the schema permits them to stay apart indefinitely.
    """
    session: Session = seeded["session"]
    open_membership(
        session, mention_id=seeded["mention_a"], entity_id=seeded["entity_a"]
    )
    open_membership(
        session, mention_id=seeded["mention_b"], entity_id=seeded["entity_b"]
    )
    session.flush()
    assert active_entity_for_mention(session, seeded["mention_a"]) == seeded["entity_a"]
    assert active_entity_for_mention(session, seeded["mention_b"]) == seeded["entity_b"]
    session.rollback()
