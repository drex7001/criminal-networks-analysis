"""Ontology vocabulary for the workspace (Article XI, spec 07 §3).

The UI must not contain a hand-written list of handling codes or source types.
Article XI makes ``ontology/aegis.yaml`` the single domain artifact, and a
picker hard-coded in TypeScript is a second one: it would keep working, and
keep being wrong, for exactly as long as nobody noticed the ontology had moved.

This is the smallest route that closes that gap — the vocabulary a form needs
to render, and the version it came from. The generated ``ui_meta.json`` of
spec 07 §3 supersedes it in P4, when whole screens are rendered from the
ontology rather than one dropdown.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aegis.api.deps import AuthContext, OntologyDep, authorize
from aegis.api.schemas import OntologyVocabularyOut

router = APIRouter(tags=["ontology"])


@router.get(
    "/ontology/vocabulary",
    response_model=OntologyVocabularyOut,
    operation_id="getOntologyVocabulary",
)
def get_vocabulary(
    ontology: OntologyDep,
    auth: AuthContext = Depends(authorize()),
) -> OntologyVocabularyOut:
    """Closed vocabularies a caller needs to compose a valid request.

    Authenticated but unrestricted by role: this is the shape of the domain,
    not anything asserted about anyone in it.
    """
    return OntologyVocabularyOut(
        version=ontology.version,
        # Ordered, and the order is load-bearing: clearance is an index into
        # this list (aegis.authz.filters.allowed_handling_codes), so the UI
        # must not sort it for display.
        handling_codes=list(ontology.handling_codes),
        source_types=list(ontology.source_types),
    )
