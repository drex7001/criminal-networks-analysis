"""Core canonical claim-store schema (speckit T4, spec 02).

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Required by entity label lookup.  Extensions are cluster capabilities, so the
    # downgrade removes our dependent index but intentionally does not drop pg_trgm.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "source",
        sa.Column("source_id", sa.Text(), primary_key=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text()),
        sa.Column("reliability_scheme", sa.Text()),
        sa.Column("reliability_original", sa.Text()),
        sa.Column("reliability_normalized", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )

    op.create_table(
        "source_record",
        sa.Column("record_id", sa.Text(), primary_key=True),
        sa.Column("source_id", sa.Text(), sa.ForeignKey("source.source_id"), nullable=False),
        sa.Column("ingest_key", sa.Text(), nullable=False, unique=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text()),
        sa.Column("source_time", sa.DateTime(timezone=True)),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "handling_code", sa.Text(), nullable=False, server_default=sa.text("'open'")
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'landed'")),
        sa.Column("quarantine_reason", sa.Text()),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "status IN ('landed', 'quarantined', 'processed')",
            name="ck_source_record_status",
        ),
    )
    op.create_index("ix_source_record_content_hash", "source_record", ["content_hash"])

    op.create_table(
        "entity",
        sa.Column("entity_id", sa.Text(), primary_key=True),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index(
        "ix_entity_label_trgm",
        "entity",
        ["label"],
        postgresql_using="gin",
        postgresql_ops={"label": "gin_trgm_ops"},
    )

    op.create_table(
        "case_file",
        sa.Column("case_id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column(
            "handling_code", sa.Text(), nullable=False, server_default=sa.text("'open'")
        ),
        sa.Column("opened_by", sa.Text(), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('open', 'closed', 'sealed')", name="ck_case_file_status"
        ),
    )

    op.create_table(
        "claim",
        sa.Column("claim_id", sa.Text(), primary_key=True),
        sa.Column("subject_id", sa.Text(), sa.ForeignKey("entity.entity_id"), nullable=False),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("object_id", sa.Text(), sa.ForeignKey("entity.entity_id")),
        sa.Column("object_value", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("assertion_type", sa.Text(), nullable=False),
        sa.Column(
            "record_id", sa.Text(), sa.ForeignKey("source_record.record_id"), nullable=False
        ),
        sa.Column("excerpt", sa.Text()),
        sa.Column("collection_method", sa.Text()),
        sa.Column("credibility_scheme", sa.Text()),
        sa.Column("credibility_original", sa.Text()),
        sa.Column(
            "credibility_normalized",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'cannot_judge'"),
        ),
        sa.Column(
            "verification_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'unverified'"),
        ),
        sa.Column("analytic_confidence", sa.Text()),
        sa.Column("event_time_earliest", sa.DateTime(timezone=True)),
        sa.Column("event_time_latest", sa.DateTime(timezone=True)),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("retracted_at", sa.DateTime(timezone=True)),
        sa.Column("retraction_reason", sa.Text()),
        sa.Column(
            "handling_code", sa.Text(), nullable=False, server_default=sa.text("'open'")
        ),
        sa.Column("case_id", sa.Text(), sa.ForeignKey("case_file.case_id")),
        sa.Column("jurisdiction", sa.Text()),
        sa.Column("location_text", sa.Text()),
        sa.Column("supersedes", sa.Text(), sa.ForeignKey("claim.claim_id")),
        sa.Column("ontology_version", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(object_id IS NULL) <> (object_value IS NULL)",
            name="ck_claim_object_exactly_one",
        ),
        sa.CheckConstraint("subject_id <> object_id", name="ck_claim_no_self_reference"),
        sa.CheckConstraint(
            "event_time_latest IS NULL OR event_time_earliest IS NULL "
            "OR event_time_latest >= event_time_earliest",
            name="ck_claim_event_time_order",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_claim_valid_date_order",
        ),
    )
    op.create_index("ix_claim_subject_id", "claim", ["subject_id"])
    op.create_index("ix_claim_object_id", "claim", ["object_id"])
    op.create_index("ix_claim_predicate", "claim", ["predicate"])
    op.create_index("ix_claim_record_id", "claim", ["record_id"])
    op.create_index(
        "ix_claim_active_edges",
        "claim",
        ["subject_id", "object_id", "predicate"],
        postgresql_where=sa.text("retracted_at IS NULL"),
    )

    op.create_table(
        "claim_relation",
        sa.Column("from_claim", sa.Text(), sa.ForeignKey("claim.claim_id"), primary_key=True),
        sa.Column("to_claim", sa.Text(), sa.ForeignKey("claim.claim_id"), primary_key=True),
        sa.Column("relation", sa.Text(), primary_key=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint(
            "relation IN ('corroborates', 'contradicts')",
            name="ck_claim_relation_relation",
        ),
    )

    op.create_table(
        "review_queue",
        sa.Column("suggestion_id", sa.Text(), primary_key=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("producer", sa.Text(), nullable=False),
        sa.Column("producer_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'suggested'")
        ),
        sa.Column("decided_by", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.Text()),
        sa.Column("result_claim", sa.Text(), sa.ForeignKey("claim.claim_id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'accepted', 'rejected')",
            name="ck_review_queue_status",
        ),
    )

    op.create_table(
        "case_member",
        sa.Column("case_id", sa.Text(), sa.ForeignKey("case_file.case_id"), primary_key=True),
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),
    )

    op.create_table(
        "authz_outbox",
        sa.Column("outbox_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column("fga_tuple", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint("op IN ('write', 'delete')", name="ck_authz_outbox_op"),
    )
    op.create_index(
        "ix_authz_outbox_pending",
        "authz_outbox",
        ["processed_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_authz_outbox_pending", table_name="authz_outbox")
    op.drop_table("authz_outbox")
    op.drop_table("case_member")
    op.drop_table("review_queue")
    op.drop_table("claim_relation")
    op.drop_index("ix_claim_active_edges", table_name="claim")
    op.drop_index("ix_claim_record_id", table_name="claim")
    op.drop_index("ix_claim_predicate", table_name="claim")
    op.drop_index("ix_claim_object_id", table_name="claim")
    op.drop_index("ix_claim_subject_id", table_name="claim")
    op.drop_table("claim")
    op.drop_table("case_file")
    op.drop_index("ix_entity_label_trgm", table_name="entity", postgresql_using="gin")
    op.drop_table("entity")
    op.drop_index("ix_source_record_content_hash", table_name="source_record")
    op.drop_table("source_record")
    op.drop_table("source")
