"""Entity detail as the provenance panel's backing route (T23c, Article VIII).

The AC this file exists for: *"seeded contradictory DOB claims both render with
a visible `contradicts` badge"*. The route cannot render a badge, but it decides
whether one is possible — grouping the two claims under one predicate is what
puts them side by side, and carrying ``contradicted_by`` is what lets the panel
name the disagreement instead of leaving a reader to spot it.

The ontology backs this deliberately: ``person.date_of_birth`` declares
``conflicts: preserve`` — two stated dates may coexist, and neither is deleted
to make the record tidy.

Fictional deterministic fixtures throughout.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
import sqlalchemy as sa
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from aegis.actions import new_id
from aegis.api import create_app
from aegis.api.auth import OIDCAuthenticator
from aegis.er.ledger import active_revision_id
from aegis.store import (
    Claim,
    ClaimRelation,
    Entity,
    EntityCanonicalMap,
    Source,
    SourceRecord,
)
from tests.support.database import configured_test_database, truncate_domain_data

ISSUER = "http://localhost:8180/realms/aegis"
AUDIENCE = "aegis-api"

pytestmark = pytest.mark.requirement(
    "Article-III", "Article-V", "Article-VIII", "spec-06-2.1", "T23c"
)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

EARLIER_DOB = "1985-03-12"
LATER_DOB = "1987-11-02"


class _StubKey:
    key = _KEY.public_key()


class _StubJWKS:
    def get_signing_key_from_jwt(self, token: str) -> _StubKey:
        return _StubKey()


def auth(sub: str, *roles: str, clearance: int = 2) -> dict:
    now = datetime.now(timezone.utc)
    encoded = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": sub,
            "preferred_username": sub,
            "iat": now,
            "exp": now + timedelta(minutes=10),
            "realm_access": {"roles": list(roles)},
            "clearance": clearance,
        },
        _KEY,
        algorithm="RS256",
    )
    return {"Authorization": f"Bearer {encoded}"}


ANALYST = auth("user:analyst", "analyst")
LOW_CLEARANCE = auth("user:junior", "analyst", clearance=0)


@pytest.fixture(scope="module")
def provenance_db(test_database_url: str, alembic_config: Config):
    with configured_test_database(test_database_url, alembic_config):
        yield test_database_url


@pytest.fixture(scope="module")
def client(provenance_db: str) -> TestClient:
    app = create_app()
    app.state.authenticator = OIDCAuthenticator(app.state.settings, jwks_client=_StubJWKS())
    return TestClient(app)


@pytest.fixture(scope="module")
def engine(provenance_db: str) -> sa.Engine:
    return sa.create_engine(provenance_db)


def _claim(session: Session, subject: str, record: str, **kwargs) -> str:
    claim_id = new_id("clm")
    session.add(
        Claim(
            claim_id=claim_id,
            subject_id=subject,
            predicate=kwargs.pop("predicate", "date_of_birth"),
            object_value=kwargs.pop("object_value", EARLIER_DOB),
            assertion_type="reported",
            handling_code=kwargs.pop("handling", "open"),
            record_id=record,
            identity_revision_id=active_revision_id(session),
            ontology_version="1.2.0",
            credibility_normalized="possibly_true",
            verification_status="unverified",
            **kwargs,
        )
    )
    return claim_id


@pytest.fixture()
def world(engine: sa.Engine):
    """One person with two stated dates of birth, recorded as contradicting."""
    truncate_domain_data(engine)
    session = Session(engine)
    ids: dict[str, str] = {"source": new_id("src"), "record": new_id("rec")}
    with session.begin():
        session.add(
            Source(source_id=ids["source"], source_type="open_source", name="T23c registry")
        )
        session.add(
            SourceRecord(
                record_id=ids["record"],
                source_id=ids["source"],
                ingest_key=new_id("key"),
                content_hash="e" * 64,
                storage_uri="test://t23c-provenance",
            )
        )
        ids["person"] = new_id("ent")
        session.add(
            Entity(entity_id=ids["person"], entity_type="person", label="Fictional GOLF")
        )
        session.flush()

        ids["dob_early"] = _claim(session, ids["person"], ids["record"])
        ids["dob_late"] = _claim(
            session, ids["person"], ids["record"], object_value=LATER_DOB
        )
        ids["alias"] = _claim(
            session,
            ids["person"],
            ids["record"],
            predicate="known_as",
            object_value="Fictional G",
        )
        session.flush()
        session.add(
            ClaimRelation(
                from_claim=ids["dob_early"],
                to_claim=ids["dob_late"],
                relation="contradicts",
                created_by="user:analyst",
            )
        )
    try:
        yield {**ids, "session": session}
    finally:
        session.close()


def _detail(client: TestClient, entity_id: str, headers: dict) -> dict:
    response = client.get(f"/v1/entities/{entity_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_contradictory_dobs_are_grouped_under_one_predicate(client, world) -> None:
    """Grouping is what renders them side by side."""
    body = _detail(client, world["person"], ANALYST)
    dobs = body["claims_by_predicate"]["date_of_birth"]
    assert {entry["claim"]["object_value"] for entry in dobs} == {EARLIER_DOB, LATER_DOB}


def test_each_contradictory_claim_names_the_other(client, world) -> None:
    """The badge the AC asks for is only renderable if the route says so.

    Both directions, because directionality is a recording artefact: the claim
    that happened to be written second is not the contested one.
    """
    body = _detail(client, world["person"], ANALYST)
    by_id = {
        entry["claim"]["claim_id"]: entry
        for entry in body["claims_by_predicate"]["date_of_birth"]
    }
    assert by_id[world["dob_early"]]["contradicted_by"] == [world["dob_late"]]
    assert by_id[world["dob_late"]]["contradicted_by"] == [world["dob_early"]]


def test_corroboration_does_not_cancel_contradiction(client, world) -> None:
    """Article VIII: the reader is shown the disagreement, never a net score."""
    session: Session = world["session"]
    with session.begin():
        supporting = _claim(
            session, world["person"], world["record"], object_value=EARLIER_DOB
        )
        session.flush()
        session.add(
            ClaimRelation(
                from_claim=supporting,
                to_claim=world["dob_early"],
                relation="corroborates",
                created_by="user:analyst",
            )
        )

    body = _detail(client, world["person"], ANALYST)
    entry = next(
        e
        for e in body["claims_by_predicate"]["date_of_birth"]
        if e["claim"]["claim_id"] == world["dob_early"]
    )
    # Both survive on the same claim. A UI that showed only the stronger of the
    # two would be making the judgement the analyst is here to make.
    assert entry["corroborated_by"] and entry["contradicted_by"]


def test_uncontested_predicates_carry_no_relations(client, world) -> None:
    body = _detail(client, world["person"], ANALYST)
    alias = body["claims_by_predicate"]["known_as"][0]
    assert alias["contradicted_by"] == []
    assert alias["corroborated_by"] == []


def test_each_claim_carries_its_three_grading_dimensions_apart(client, world) -> None:
    """Article III — separately, and with the source's reliability among them."""
    body = _detail(client, world["person"], ANALYST)
    grading = body["claims_by_predicate"]["date_of_birth"][0]["grading"]
    assert set(grading) == {
        "reliability",
        "credibility",
        "verification",
        "analytic_confidence",
    }
    assert "score" not in grading


def test_each_claim_carries_its_source_and_record(client, world) -> None:
    body = _detail(client, world["person"], ANALYST)
    entry = body["claims_by_predicate"]["date_of_birth"][0]
    assert entry["source"]["name"] == "T23c registry"
    assert entry["record"]["record_id"] == world["record"]


def test_claims_written_against_a_merged_away_id_still_appear(client, world) -> None:
    """The panel must not lose evidence to a merge (Article V).

    A claim written before a merge still names the id it was written against.
    Asking only about the surviving id would answer "nothing is known" about an
    entity the graph is actively drawing — the same reason `why_connected`
    resolves through the canonical map.
    """
    session: Session = world["session"]
    absorbed = new_id("ent")
    with session.begin():
        session.add(
            Entity(entity_id=absorbed, entity_type="person", label="Fictional GOLF (dup)")
        )
        session.flush()
        _claim(
            session,
            absorbed,
            world["record"],
            predicate="known_as",
            object_value="Fictional Golfie",
        )
        session.add(
            EntityCanonicalMap(
                entity_id=absorbed,
                canonical_entity_id=world["person"],
                at_revision_id=active_revision_id(session),
            )
        )

    body = _detail(client, world["person"], ANALYST)
    aliases = {
        entry["claim"]["object_value"] for entry in body["claims_by_predicate"]["known_as"]
    }
    assert "Fictional Golfie" in aliases, "a merge must not hide evidence"


def test_following_a_merged_away_id_says_so(client, world) -> None:
    """Answering about the survivor silently would answer a different question."""
    session: Session = world["session"]
    absorbed = new_id("ent")
    with session.begin():
        session.add(Entity(entity_id=absorbed, entity_type="person", label="Fictional HOTEL"))
        session.flush()
        session.add(
            EntityCanonicalMap(
                entity_id=absorbed,
                canonical_entity_id=world["person"],
                at_revision_id=active_revision_id(session),
            )
        )

    body = _detail(client, absorbed, ANALYST)
    assert body["resolved_entity_id"] == world["person"]


def test_a_restricted_claim_is_absent_for_a_junior_analyst(client, world) -> None:
    session: Session = world["session"]
    with session.begin():
        _claim(
            session,
            world["person"],
            world["record"],
            predicate="known_as",
            object_value="Fictional Secret",
            handling="sensitive",
        )

    cleared = _detail(client, world["person"], ANALYST)
    junior = _detail(client, world["person"], LOW_CLEARANCE)
    values = lambda body: {  # noqa: E731 - local reader, not an API
        entry["claim"]["object_value"]
        for entry in body["claims_by_predicate"].get("known_as", [])
    }
    assert "Fictional Secret" in values(cleared)
    assert "Fictional Secret" not in values(junior)


def test_an_unknown_entity_is_absent_not_forbidden(client, world) -> None:
    """404, so asking cannot confirm existence (spec 06 §6)."""
    response = client.get("/v1/entities/ent_does_not_exist", headers=ANALYST)
    assert response.status_code == 404


def test_entity_detail_requires_authentication(client, world) -> None:
    assert client.get(f"/v1/entities/{world['person']}").status_code == 401
