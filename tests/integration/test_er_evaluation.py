"""T26 full-pipeline invariant: evaluation proposes, and never merges."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis.er.evaluation import evaluate
from aegis.er.ledger import active_entity_for_mention
from aegis.evidence import LocalFilesystemVault
from aegis.ingestion.mvp_fixture import load_mvp_fixture
from aegis.store import IdentityDecision, Mention
from tests.support.database import migrated_test_engine, truncate_domain_data

pytestmark = pytest.mark.requirement("Article-VII", "H-08", "T26")


@pytest.fixture(scope="module")
def er_evaluation_engine(
    test_database_url: str, alembic_config: Config
) -> sa.Engine:
    with migrated_test_engine(test_database_url, alembic_config) as engine:
        yield engine


def test_full_pipeline_keeps_seeded_distinct_people_unmerged(
    er_evaluation_engine: sa.Engine, tmp_path: Path
) -> None:
    truncate_domain_data(er_evaluation_engine)
    with Session(er_evaluation_engine) as session:
        load_mvp_fixture(session, LocalFilesystemVault(tmp_path / "vault"))
        namesakes = session.scalars(
            select(Mention).where(Mention.raw_text == "Ruwan Silva")
        ).all()
        assert len(namesakes) == 2
        assert active_entity_for_mention(session, namesakes[0].mention_id) != (
            active_entity_for_mention(session, namesakes[1].mention_id)
        )
        assert (
            session.scalar(select(func.count()).select_from(IdentityDecision)) or 0
        ) == 0

    report = evaluate()
    assert report.passed is True
    assert report.distinct_pairs_emitted == 0
    assert report.automatic_merges == 0
