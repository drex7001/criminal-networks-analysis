"""Query dataclasses → response models, where two routes render the same unit.

``ClaimProvenanceOut`` is the unit both the why-connected panel (an edge) and
the entity panel (a node) draw, and the whole point of it is that the three
grading dimensions stay apart and both relation directions survive
(Articles III and VIII). Two routes each building that by hand is how one of
them quietly starts omitting ``contradicted_by``; there is one builder instead.
"""

from __future__ import annotations

from aegis.api.schemas import (
    ClaimOut,
    ClaimProvenanceOut,
    GradingOut,
    MentionOut,
    SourceOut,
    SourceRecordOut,
)
from aegis.queries.provenance import ClaimProvenance


def claim_provenance_out(entry: ClaimProvenance) -> ClaimProvenanceOut:
    claim = entry.claim
    return ClaimProvenanceOut(
        claim=ClaimOut.model_validate(claim),
        grading=GradingOut(
            # Reliability is graded on the source, not the claim: repeating a
            # claim does not make its source more reliable.
            reliability=entry.source.reliability_normalized if entry.source else None,
            credibility=claim.credibility_normalized,
            verification=claim.verification_status,
            analytic_confidence=claim.analytic_confidence,
        ),
        source=SourceOut.model_validate(entry.source) if entry.source else None,
        record=SourceRecordOut.model_validate(entry.record) if entry.record else None,
        corroborated_by=entry.corroborated_by,
        contradicted_by=entry.contradicted_by,
        subject_mention=(
            MentionOut.model_validate(entry.subject_mention)
            if entry.subject_mention
            else None
        ),
        object_mention=(
            MentionOut.model_validate(entry.object_mention)
            if entry.object_mention
            else None
        ),
    )


__all__ = ["claim_provenance_out"]
