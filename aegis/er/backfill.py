"""Mention-anchor backfill for pre-T17 claims (spec 02 §3.1).

**This is heuristic and lossy, and says so.**  A Phase-1 claim records only its
``record_id``; the mention that produced it was never persisted, because there
was nowhere to put it.  So the only signal available is: within the claim's own
record, is there exactly one mention whose ``norm_key`` matches the entity
argument's own mentions?

Where that is ambiguous — several matching mentions in the record, or none —
the claim is left **unanchored** rather than guessed.  An unanchored claim is
handled correctly by design: a split affecting its entity routes it to
re-adjudication instead of silently picking a side (spec 02 §3.1 rule 4).  A
*wrongly* anchored claim would instead follow the wrong mention silently, which
is strictly worse than no anchor at all.

Idempotent: only claims with a NULL anchor are considered, so re-running adds
anchors for mentions that have since been extracted and changes nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis.store import Claim, IdentityMembership, Mention


@dataclass
class BackfillReport:
    """What the heuristic could and could not decide."""

    considered: int = 0
    anchored: int = 0
    ambiguous: int = 0  # several candidate mentions in the record
    unmatched: int = 0  # no mention of that entity in the record at all
    ambiguous_claims: list[str] = field(default_factory=list)

    @property
    def left_unanchored(self) -> int:
        return self.ambiguous + self.unmatched

    def to_dict(self) -> dict[str, object]:
        return {
            "considered": self.considered,
            "anchored": self.anchored,
            "ambiguous": self.ambiguous,
            "unmatched": self.unmatched,
            "left_unanchored": self.left_unanchored,
            # a sample, not the whole list: this is an operator report, and the
            # ambiguous set can be large on a real corpus
            "ambiguous_sample": sorted(self.ambiguous_claims)[:20],
        }


def _mentions_of_entity_in_record(
    session: Session, entity_id: str, record_id: str
) -> list[str]:
    """Mentions in this record that belong to this entity, active memberships only."""
    return list(
        session.scalars(
            select(Mention.mention_id)
            .join(
                IdentityMembership,
                IdentityMembership.mention_id == Mention.mention_id,
            )
            .where(
                IdentityMembership.entity_id == entity_id,
                IdentityMembership.closed_revision_id.is_(None),
                Mention.record_id == record_id,
            )
            .order_by(Mention.mention_id)
        )
    )


def backfill_anchors(session: Session, *, limit: int | None = None) -> BackfillReport:
    """Anchor claims whose evidence is unambiguous; report the rest honestly."""
    report = BackfillReport()
    query = select(Claim).where(
        (Claim.subject_mention_id.is_(None)) | (Claim.object_mention_id.is_(None))
    ).order_by(Claim.claim_id)
    if limit is not None:
        query = query.limit(limit)

    for claim in session.scalars(query):
        report.considered += 1
        decided = False
        ambiguous = False
        for role, entity_id in (
            ("subject", claim.subject_id),
            ("object", claim.object_id),
        ):
            if entity_id is None:
                continue  # a literal object has no mention to anchor to
            if getattr(claim, f"{role}_mention_id") is not None:
                continue
            candidates = _mentions_of_entity_in_record(
                session, entity_id, claim.record_id
            )
            if len(candidates) == 1:
                setattr(claim, f"{role}_mention_id", candidates[0])
                decided = True
            elif len(candidates) > 1:
                ambiguous = True
        if decided:
            report.anchored += 1
        elif ambiguous:
            report.ambiguous += 1
            report.ambiguous_claims.append(claim.claim_id)
        else:
            report.unmatched += 1

    session.flush()
    return report


__all__ = ["BackfillReport", "backfill_anchors"]
