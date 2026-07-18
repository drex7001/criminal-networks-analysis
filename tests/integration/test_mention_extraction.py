"""Mention extraction and the anchor backfill (T17; spec 02 §2/§3.1, H-06).

Integration-layer because both concern rows and their relationships: the
idempotency of extraction, and a heuristic that reads the mention table.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis.actions import ActionContext, ActionService, ActionValidationError, new_id
from aegis.er.backfill import backfill_anchors
from aegis.er.ledger import active_entity_for_mention, open_membership
from aegis.er.mentions import extract_mentions
from aegis.store import Claim, Entity, Mention, Source, SourceRecord
from tests.support.database import migrated_test_engine

pytestmark = pytest.mark.requirement("Article-V", "ADR-029", "H-06", "T17")

# Deliberately mirrors the shape of the real corpus: the name an extractor
# reports is often *not* a contiguous span of the text it came from.
TEXT = (
    "According to remand records, Kasun \"Podda\" WIJERATNE and Rizvi FAROOK were "
    "both held on E-Wing between March and August 2023. නිමල් පෙරේරා was named "
    "separately in the same file."
)


@pytest.fixture(scope="module")
def extraction_engine(test_database_url: str, alembic_config: Config) -> sa.Engine:
    with migrated_test_engine(test_database_url, alembic_config) as engine:
        yield engine


@pytest.fixture()
def record(extraction_engine: sa.Engine):
    ids = {"source": new_id("src"), "record": new_id("rec")}
    session = Session(extraction_engine)
    with session.begin():
        session.add(
            Source(source_id=ids["source"], source_type="open_source", name="T17 text")
        )
        row = SourceRecord(
            record_id=ids["record"],
            source_id=ids["source"],
            ingest_key=new_id("key"),
            content_hash="e" * 64,
            storage_uri="test://text",
        )
        session.add(row)
    try:
        yield {"session": session, "record": session.get(SourceRecord, ids["record"])}
    finally:
        session.close()


@pytest.mark.integration
def test_offsets_are_recorded_only_when_the_name_is_actually_there(record) -> None:
    session: Session = record["session"]
    result = extract_mentions(
        session,
        record=record["record"],
        text=TEXT,
        names={
            "rizvi_farook": "Rizvi Farook",  # present, differently cased
            "kasun_wijeratne": "Kasun Wijeratne",  # split by a nickname
            "nimal": "නිමල් පෙරේරා",  # present, non-Latin script
        },
    )
    session.flush()

    located = result.by_ref["rizvi_farook"]
    assert located.char_start is not None and located.char_end is not None
    # the offsets point at the text as the *source* writes it
    assert TEXT[located.char_start : located.char_end] == "Rizvi FAROOK"
    assert located.raw_text == "Rizvi FAROOK"
    assert located.script == "Latn"
    assert located.context

    # A name that is plainly present but not a contiguous span still becomes a
    # mention — with NULL offsets and a place on the unverified list, because
    # claiming an offset we cannot prove would misrepresent the source.
    unlocated = result.by_ref["kasun_wijeratne"]
    assert unlocated.char_start is None and unlocated.char_end is None
    assert unlocated.raw_text == "Kasun Wijeratne"
    assert "Kasun Wijeratne" in result.unverified

    sinhala = result.by_ref["nimal"]
    assert sinhala.script == "Sinh"
    assert sinhala.norm_key not in {"unknown", ""}
    # language is left unset: there is no detector, and a guess would be wrong
    # more often than useful in this corpus
    assert sinhala.language is None
    session.rollback()


@pytest.mark.integration
def test_re_extraction_reuses_mentions_instead_of_duplicating(record) -> None:
    """A replay of the same pass adds nothing (spec 04 §5)."""
    session: Session = record["session"]
    names = {"rizvi_farook": "Rizvi Farook", "kasun_wijeratne": "Kasun Wijeratne"}
    first = extract_mentions(session, record=record["record"], text=TEXT, names=names)
    session.commit()
    assert len(first.created) == 2

    second = extract_mentions(session, record=record["record"], text=TEXT, names=names)
    session.commit()
    assert second.created == []
    assert len(second.reused) == 2
    assert {row.mention_id for row in second.reused} == {
        row.mention_id for row in first.created
    }
    assert session.scalar(
        select(sa.func.count())
        .select_from(Mention)
        .where(Mention.record_id == record["record"].record_id)
    ) == 2


@pytest.mark.integration
def test_accepting_a_claim_creates_the_entity_from_its_anchor(record) -> None:
    """Entity creation folds into claim acceptance — no entity_draft kind."""
    session: Session = record["session"]
    result = extract_mentions(
        session,
        record=record["record"],
        text=TEXT,
        names={"rizvi_farook": "Rizvi Farook"},
    )
    session.commit()
    mention = result.by_ref["rizvi_farook"]
    assert active_entity_for_mention(session, mention.mention_id) is None

    service = ActionService(session)
    context = ActionContext(actor="user:analyst", purpose="T17 test")

    # `known_as` accepts a person *or* an organization, so the type cannot be
    # inferred from the predicate — it has to be proposed, not guessed.
    with pytest.raises(ActionValidationError, match="must be proposed explicitly"):
        service.record_claim(
            context,
            predicate="known_as",
            object_value="Podda",
            assertion_type="reported",
            collection_method="semantic_llm",
            record_id=record["record"].record_id,
            subject_mention_id=mention.mention_id,
        )
    session.rollback()

    claim = service.record_claim(
        context,
        predicate="known_as",
        object_value="Podda",
        assertion_type="reported",
        collection_method="semantic_llm",
        record_id=record["record"].record_id,
        subject_mention_id=mention.mention_id,  # no subject_id at all
        subject_entity_type="person",  # what the extractor labelled the node
    )
    session.commit()

    assert claim.subject_id is not None
    entity = session.get(Entity, claim.subject_id)
    assert entity is not None and entity.entity_type == "person"
    assert entity.label == "Rizvi FAROOK"  # as the source writes it
    # the new entity is a single-mention entity, not an adjudicated merge
    assert active_entity_for_mention(session, mention.mention_id) == claim.subject_id


@pytest.mark.integration
def test_extracted_claims_must_carry_an_anchor(record) -> None:
    """ADR-029 rule 1: a claim about what a source reported must point at it."""
    session: Session = record["session"]
    entity = Entity(entity_id=new_id("ent"), entity_type="person", label="Anchorless")
    session.add(entity)
    session.commit()
    service = ActionService(session)
    context = ActionContext(actor="user:analyst", purpose="T17 test")

    with pytest.raises(ActionValidationError, match="an anchor is required"):
        service.record_claim(
            context,
            subject_id=entity.entity_id,
            predicate="known_as",
            object_value="No anchor",
            assertion_type="reported",
            collection_method="semantic_llm",
            record_id=record["record"].record_id,
        )
    session.rollback()

    # An analyst's own assessment legitimately has no mention behind it.
    claim = service.record_claim(
        context,
        subject_id=entity.entity_id,
        predicate="known_as",
        object_value="Analyst judgement",
        assertion_type="inferred",
        collection_method="manual",
        record_id=record["record"].record_id,
    )
    session.commit()
    assert claim.subject_mention_id is None


@pytest.mark.integration
def test_backfill_anchors_unambiguous_claims_and_reports_the_rest(record) -> None:
    session: Session = record["session"]
    subject = Entity(entity_id=new_id("ent"), entity_type="person", label="Backfill A")
    other = Entity(entity_id=new_id("ent"), entity_type="person", label="Backfill B")
    session.add_all([subject, other])
    session.flush()

    single = Mention(
        mention_id=new_id("men"),
        record_id=record["record"].record_id,
        raw_text="Backfill A",
        norm_key="backfill_a",
    )
    twin_a = Mention(
        mention_id=new_id("men"),
        record_id=record["record"].record_id,
        raw_text="Backfill B",
        norm_key="backfill_b",
    )
    twin_b = Mention(
        mention_id=new_id("men"),
        record_id=record["record"].record_id,
        raw_text="Backfill B again",
        norm_key="backfill_b",
    )
    session.add_all([single, twin_a, twin_b])
    session.flush()
    for mention, entity in ((single, subject), (twin_a, other), (twin_b, other)):
        open_membership(
            session, mention_id=mention.mention_id, entity_id=entity.entity_id
        )

    # Two pre-T17-shaped claims written straight to the table: one whose entity
    # has exactly one mention in the record, one whose entity has two.
    unambiguous = Claim(
        claim_id=new_id("clm"),
        subject_id=subject.entity_id,
        predicate="known_as",
        object_value="Anchor me",
        assertion_type="reported",
        record_id=record["record"].record_id,
        identity_revision_id=0,
        ontology_version="1.0.0",
    )
    ambiguous = Claim(
        claim_id=new_id("clm"),
        subject_id=other.entity_id,
        predicate="known_as",
        object_value="Which mention?",
        assertion_type="reported",
        record_id=record["record"].record_id,
        identity_revision_id=0,
        ontology_version="1.0.0",
    )
    session.add_all([unambiguous, ambiguous])
    session.commit()

    report = backfill_anchors(session)
    session.commit()

    assert unambiguous.subject_mention_id == single.mention_id
    # Ambiguity is reported, never guessed: a wrong anchor would follow the
    # wrong mention silently, which is worse than none (spec 02 §3.1).
    assert ambiguous.subject_mention_id is None
    assert ambiguous.claim_id in report.ambiguous_claims
    assert report.anchored >= 1 and report.ambiguous >= 1

    # Idempotent: a second pass finds nothing new to decide.
    again = backfill_anchors(session)
    session.commit()
    assert again.anchored == 0
