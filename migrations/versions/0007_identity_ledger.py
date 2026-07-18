"""Identity decision ledger + P2 schema (T17; specs/02 §1-§3, §8, specs/05 §7).

One Alembic series for the whole Milestone A design pack:

* **T17a / ADR-028** — ``identity_revision`` chain, ``identity_decision``,
  revision-keyed ``identity_membership`` with the one-active-membership
  invariant enforced by a partial unique index, ``er_candidate``,
  versioned ``identity_negative_constraint``, ``entity_canonical_map``.
* **T17b / ADR-029** — ``claim`` mention anchors and the identity-revision
  stamp.  (The v2 ``edge_projection`` is T21's; migration ``0006``'s view
  stays live until then.)
* **T17c / ADR-031** — the typed suggestion envelope on ``review_queue``,
  including the data migration for live Phase-1 rows.
* **T17d / B-08** — the nullable governance seams on ``source_record``.

**Revision 0** is inserted as the migration baseline and every pre-existing
membership is backfilled as opened there and closed nowhere: the Phase-1
one-mention clusters are *verified* as revision 0 of the ledger rather than
being given an invented decision (specs/05 §7 step 3, ADR-005).  Revision 0 is
the only revision with ``decision_id IS NULL``.

Pre-existing data that the ledger cannot express fails this migration loudly
rather than being silently repaired — see ``_guard_*`` below (specs/05 §7 step 4).

**Downgrade is lossy and says so.**  The Phase-1 shape has nowhere to put a
decision, a candidate, a negative constraint, a revision, a mention anchor or a
suggestion kind, so those rows and columns are dropped.  ``decided_by`` cannot
be recovered — it was deleted precisely because it admitted ``rule:<name>``
(ADR-027) — so downgrade refills it with ``system:downgrade``.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

BASELINE_REVISION = 0


class LedgerMigrationError(RuntimeError):
    """Pre-existing data the decision ledger cannot represent."""


# ── guards ───────────────────────────────────────────────────────────────────


def _guard_closed_memberships(bind: sa.engine.Connection) -> None:
    """A Phase-1 closed membership has no revision that could have closed it.

    Nothing in Phase 1 ever set ``valid_to`` (merge and split were never
    implemented), so this is unreachable in practice — but inventing a closing
    revision for a row whose decision was never recorded would fabricate
    ledger history, which is the one thing this migration exists to prevent.
    """
    stale = bind.execute(
        sa.text(
            "SELECT membership_id FROM identity_membership "
            "WHERE valid_to IS NOT NULL ORDER BY membership_id LIMIT 10"
        )
    ).scalars().all()
    if stale:
        raise LedgerMigrationError(
            "identity_membership rows are already closed (valid_to IS NOT NULL) "
            f"but no decision recorded why: {stale}. The ledger cannot invent the "
            "closing revision — resolve these rows before upgrading (specs/05 §7)."
        )


def _guard_one_active_membership(bind: sa.engine.Connection) -> None:
    """The ADR-028 §2 invariant, checked before the index enforces it."""
    dupes = bind.execute(
        sa.text(
            "SELECT mention_id, count(*) AS n FROM identity_membership "
            "WHERE valid_to IS NULL GROUP BY mention_id HAVING count(*) > 1 "
            "ORDER BY mention_id LIMIT 10"
        )
    ).all()
    if dupes:
        raise LedgerMigrationError(
            "mentions hold more than one active membership, which the ledger "
            f"forbids (ADR-028 §2): {[(m, n) for m, n in dupes]}. De-duplicating "
            "them here would be an unrecorded identity decision — fix the data "
            "first (specs/05 §7 step 4)."
        )


# ── upgrade ──────────────────────────────────────────────────────────────────


def upgrade() -> None:
    bind = op.get_bind()
    _guard_closed_memberships(bind)
    _guard_one_active_membership(bind)

    _upgrade_entity_and_mention()
    _upgrade_ledger_tables()
    _upgrade_membership(bind)
    _upgrade_claim()
    _upgrade_review_queue()
    _upgrade_source_record_seams()


def _upgrade_entity_and_mention() -> None:
    # An entity is never deleted on merge; a tombstone marks one with no active
    # memberships and no lineage target (specs/05 §5).  Ids are never reused.
    op.add_column("entity", sa.Column("tombstoned_at", sa.DateTime(timezone=True)))

    # H-06 minimum: without offsets and script a mention cannot be re-anchored
    # to the text it was read from, so no anchor built on it is verifiable.
    op.add_column("mention", sa.Column("char_start", sa.Integer()))
    op.add_column("mention", sa.Column("char_end", sa.Integer()))
    op.add_column("mention", sa.Column("script", sa.Text()))
    op.add_column("mention", sa.Column("language", sa.Text()))
    op.create_check_constraint(
        "ck_mention_offset_order",
        "mention",
        "char_end IS NULL OR char_start IS NULL OR char_end >= char_start",
    )


def _upgrade_ledger_tables() -> None:
    # identity_revision and identity_decision reference each other, and so do
    # identity_decision and er_candidate.  Both cycles are created as tables
    # first; the closing foreign keys are added at the end of this function as
    # DEFERRABLE INITIALLY DEFERRED so an adjudication can insert a decision and
    # its revision in either order inside one transaction (specs/02 §2).
    op.create_table(
        "identity_revision",
        sa.Column("revision_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("decision_id", sa.Text()),  # NULL only for revision 0
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "identity_decision",
        sa.Column("decision_id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        # Article VII: a decision is always a person.  'rule:<name>' belongs on
        # er_candidate.producer, never here (ADR-027).
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=False),
        sa.Column("candidate_id", sa.Text()),
        sa.Column(
            "input_mentions",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "parent_revision_id",
            sa.BigInteger(),
            sa.ForeignKey("identity_revision.revision_id"),
            nullable=False,
        ),
        sa.Column("result_revision_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "kind IN ('confirm', 'reject', 'merge', 'split', 'unresolved')",
            name="ck_identity_decision_kind",
        ),
        sa.UniqueConstraint("result_revision_id", name="uq_identity_decision_result"),
    )
    op.create_table(
        "er_candidate",
        sa.Column("candidate_id", sa.Text(), primary_key=True),
        sa.Column("mention_a", sa.Text(), sa.ForeignKey("mention.mention_id"), nullable=False),
        sa.Column("mention_b", sa.Text(), sa.ForeignKey("mention.mention_id"), nullable=False),
        sa.Column("producer", sa.Text(), nullable=False),
        sa.Column("producer_version", sa.Text(), nullable=False),
        sa.Column("graph_snapshot_id", sa.Text()),
        sa.Column("score", sa.Numeric()),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.Column(
            "pre_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "disposition", sa.Text(), nullable=False, server_default=sa.text("'open'")
        ),
        sa.Column(
            "decision_id",
            sa.Text(),
            sa.ForeignKey("identity_decision.decision_id"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "disposition IN ('open', 'confirmed', 'rejected', 'unresolved', 'superseded')",
            name="ck_er_candidate_disposition",
        ),
        # canonical pair ordering — one row per pair, whichever side found it
        sa.CheckConstraint("mention_a < mention_b", name="ck_er_candidate_pair_order"),
    )
    op.create_table(
        "identity_negative_constraint",
        sa.Column("constraint_id", sa.Text(), primary_key=True),
        sa.Column("mention_a", sa.Text(), sa.ForeignKey("mention.mention_id"), nullable=False),
        sa.Column("mention_b", sa.Text(), sa.ForeignKey("mention.mention_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "decision_id",
            sa.Text(),
            sa.ForeignKey("identity_decision.decision_id"),
            nullable=False,
        ),
        sa.Column("evidence_basis", sa.Text(), nullable=False),
        sa.Column(
            "superseded_by",
            sa.Text(),
            sa.ForeignKey("identity_negative_constraint.constraint_id"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "mention_a < mention_b", name="ck_identity_negative_constraint_pair_order"
        ),
    )
    # Article XIII: a rebuildable projection of merge lineage.  Dropping the
    # whole table loses nothing — specs/05 §5 replays it from the ledger.
    op.create_table(
        "entity_canonical_map",
        sa.Column(
            "entity_id",
            sa.Text(),
            sa.ForeignKey("entity.entity_id"),
            primary_key=True,
        ),
        sa.Column(
            "canonical_entity_id",
            sa.Text(),
            sa.ForeignKey("entity.entity_id"),
            nullable=False,
        ),
        sa.Column(
            "at_revision_id",
            sa.BigInteger(),
            sa.ForeignKey("identity_revision.revision_id"),
            nullable=False,
        ),
    )

    op.create_foreign_key(
        "fk_identity_revision_decision",
        "identity_revision",
        "identity_decision",
        ["decision_id"],
        ["decision_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_identity_decision_result_revision",
        "identity_decision",
        "identity_revision",
        ["result_revision_id"],
        ["revision_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_identity_decision_candidate",
        "identity_decision",
        "er_candidate",
        ["candidate_id"],
        ["candidate_id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_index(
        "ix_er_candidate_open",
        "er_candidate",
        ["disposition"],
        postgresql_where=sa.text("disposition = 'open'"),
    )
    op.create_index("ix_er_candidate_mentions", "er_candidate", ["mention_a", "mention_b"])


def _upgrade_membership(bind: sa.engine.Connection) -> None:
    # Revision 0: the baseline, not a decision anyone made.  It is the only
    # revision permitted to carry decision_id IS NULL (specs/05 §2).
    op.execute(
        sa.text(
            "INSERT INTO identity_revision (revision_id, decision_id) "
            f"VALUES ({BASELINE_REVISION}, NULL) ON CONFLICT DO NOTHING"
        )
    )
    # BIGSERIAL started at 1; revision 0 was inserted explicitly, so the
    # sequence is already past it and needs no adjustment.

    op.add_column(
        "identity_membership", sa.Column("opened_revision_id", sa.BigInteger())
    )
    op.add_column(
        "identity_membership", sa.Column("closed_revision_id", sa.BigInteger())
    )
    op.execute(
        sa.text(
            "UPDATE identity_membership SET opened_revision_id = "
            f"{BASELINE_REVISION} WHERE opened_revision_id IS NULL"
        )
    )
    op.alter_column("identity_membership", "opened_revision_id", nullable=False)
    op.create_foreign_key(
        "fk_identity_membership_opened_revision",
        "identity_membership",
        "identity_revision",
        ["opened_revision_id"],
        ["revision_id"],
    )
    op.create_foreign_key(
        "fk_identity_membership_closed_revision",
        "identity_membership",
        "identity_revision",
        ["closed_revision_id"],
        ["revision_id"],
    )

    # decided_by admitted 'rule:<name>' — retired by ADR-027.  Who decided is
    # now identity_decision.decided_by, reachable through the opening revision;
    # a membership opened at revision 0 has no decision, which is the truthful
    # answer for a legacy cluster nobody adjudicated.
    op.drop_index("ix_identity_membership_mention", table_name="identity_membership")
    op.drop_column("identity_membership", "decided_by")
    op.drop_column("identity_membership", "decision_note")
    op.drop_column("identity_membership", "valid_from")
    op.drop_column("identity_membership", "valid_to")

    # THE invariant (ADR-028 §2), enforced by the database rather than by
    # application code that a future caller could forget to run.
    op.create_index(
        "ux_membership_one_active",
        "identity_membership",
        ["mention_id"],
        unique=True,
        postgresql_where=sa.text("closed_revision_id IS NULL"),
    )
    # canonical-map rebuild and the scoped concurrency check both scan by
    # entity over active rows only (specs/02 §8)
    op.create_index(
        "ix_identity_membership_active_entity",
        "identity_membership",
        ["entity_id"],
        postgresql_where=sa.text("closed_revision_id IS NULL"),
    )


def _upgrade_claim() -> None:
    # ADR-029: the textual evidence each entity argument came from.  Nullable
    # because analyst and assessment claims legitimately have no mention; the
    # "required for observed/reported" rule is an actions-layer invariant, not
    # a CHECK, because it depends on assertion_type semantics the DB does not
    # own (specs/02 §3.1 rule 1).
    op.add_column(
        "claim",
        sa.Column("subject_mention_id", sa.Text(), sa.ForeignKey("mention.mention_id")),
    )
    op.add_column(
        "claim",
        sa.Column("object_mention_id", sa.Text(), sa.ForeignKey("mention.mention_id")),
    )
    op.create_check_constraint(
        "ck_claim_object_anchor_needs_entity",
        "claim",
        "object_mention_id IS NULL OR object_id IS NOT NULL",
    )
    # The identity revision current at recorded_at — what identity *meant* when
    # the claim was made.  Not a resolution instruction (specs/02 §3.1 rule 2).
    op.add_column("claim", sa.Column("identity_revision_id", sa.BigInteger()))
    op.execute(
        sa.text(
            "UPDATE claim SET identity_revision_id = "
            f"{BASELINE_REVISION} WHERE identity_revision_id IS NULL"
        )
    )
    op.alter_column("claim", "identity_revision_id", nullable=False)
    op.create_foreign_key(
        "fk_claim_identity_revision",
        "claim",
        "identity_revision",
        ["identity_revision_id"],
        ["revision_id"],
    )
    # split re-adjudication looks claims up by mention (specs/02 §8)
    op.create_index("ix_claim_subject_mention", "claim", ["subject_mention_id"])
    op.create_index("ix_claim_object_mention", "claim", ["object_mention_id"])


# The Phase-1 queue smuggled a pseudo-kind into producer_meta->>'draft_kind'.
# Both values map to claim_draft: there is no entity_draft kind (ADR-031 §1),
# because entity creation folds into claim_draft acceptance (specs/02 §3.2).
_PHASE1_PRODUCER_VERSION = (
    "coalesce(producer_meta->>'pattern_version', producer_meta->>'model_version', "
    "producer_meta->>'model', 'phase1-unversioned')"
)


def _upgrade_review_queue() -> None:
    op.add_column("review_queue", sa.Column("suggestion_kind", sa.Text()))
    op.add_column("review_queue", sa.Column("schema_version", sa.Integer()))
    op.add_column("review_queue", sa.Column("target_action", sa.Text()))
    op.add_column("review_queue", sa.Column("producer_version", sa.Text()))
    op.add_column(
        "review_queue",
        sa.Column("record_id", sa.Text(), sa.ForeignKey("source_record.record_id")),
    )
    op.add_column(
        "review_queue", sa.Column("case_id", sa.Text(), sa.ForeignKey("case_file.case_id"))
    )
    op.add_column("review_queue", sa.Column("idempotency_key", sa.Text()))
    op.add_column(
        "review_queue",
        sa.Column("supersedes", sa.Text(), sa.ForeignKey("review_queue.suggestion_id")),
    )
    op.add_column("review_queue", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "review_queue",
        sa.Column(
            "result_decision_id",
            sa.Text(),
            sa.ForeignKey("identity_decision.decision_id"),
        ),
    )
    op.add_column(
        "review_queue", sa.Column("result_relation", postgresql.JSONB())
    )
    op.alter_column("review_queue", "result_claim", new_column_name="result_claim_id")

    op.execute(
        sa.text(
            "UPDATE review_queue SET "
            "  suggestion_kind = 'claim_draft', "
            "  schema_version  = 1, "
            "  target_action   = 'record_claim', "
            "  producer_version = " + _PHASE1_PRODUCER_VERSION + ", "
            "  record_id = payload->>'record_id', "
            "  case_id   = payload->>'case_id', "
            # Phase-1 rows have no derivative hash to key on, so the backfilled
            # key digests the row's own content; suggestion_id is included
            # because the column is UNIQUE and two identical drafts from one
            # producer are possible in the legacy data.
            "  idempotency_key = encode(sha256(convert_to("
            "    'phase1|' || producer || '|' || payload::text || '|' || suggestion_id, "
            "    'UTF8')), 'hex')"
        )
    )
    # An entity draft was never acceptable — review_suggestion has always
    # hardwired claim creation, so accepting one raised.  Article VIII forbids
    # deleting them: undecided ones are closed with a note naming this
    # migration, and rows already decided by a human keep their decision.
    op.execute(
        sa.text(
            "UPDATE review_queue SET "
            "  status = CASE WHEN EXISTS ("
            "      SELECT 1 FROM review_queue peer "
            "      WHERE peer.payload->>'record_id' = review_queue.payload->>'record_id' "
            "        AND peer.producer_meta->>'draft_kind' = 'claim' "
            "        AND review_queue.producer_meta->>'norm_key' IN ("
            "              peer.producer_meta->>'subject_ref', "
            "              peer.producer_meta->>'object_ref')"
            "    ) THEN 'superseded' ELSE 'expired' END, "
            "  decision_note = coalesce(decision_note || ' | ', '') || "
            "    'closed by migration 0007 (ADR-031): entity drafts fold into "
            "claim_draft acceptance and were never acceptable on their own' "
            "WHERE producer_meta->>'draft_kind' = 'entity' AND status = 'suggested'"
        )
    )

    for column in ("suggestion_kind", "schema_version", "target_action",
                   "producer_version", "idempotency_key"):
        op.alter_column("review_queue", column, nullable=False)
    op.create_unique_constraint(
        "uq_review_queue_idempotency_key", "review_queue", ["idempotency_key"]
    )
    op.create_check_constraint(
        "ck_review_queue_kind",
        "review_queue",
        "suggestion_kind IN ('claim_draft', 'identity_candidate', 'claim_relation')",
    )
    op.drop_constraint("ck_review_queue_status", "review_queue", type_="check")
    op.create_check_constraint(
        "ck_review_queue_status",
        "review_queue",
        "status IN ('suggested', 'accepted', 'rejected', 'superseded', 'expired')",
    )
    # exactly one typed result on acceptance, per kind (ADR-031 §2)
    op.create_check_constraint(
        "ck_review_queue_accepted_result",
        "review_queue",
        "status <> 'accepted' OR num_nonnulls(result_claim_id, result_decision_id, "
        "result_relation) = 1",
    )
    op.create_index("ix_review_queue_kind_status", "review_queue", ["suggestion_kind", "status"])


def _upgrade_source_record_seams() -> None:
    """B-08 seams: stored and displayed in P2, enforced in P7.

    They land now only because retrofitting a classification column onto a
    populated evidence corpus is far more expensive than carrying nullable
    columns from the start.  No read path may consult them in P2 (specs/02 §1).
    """
    op.add_column("source_record", sa.Column("collection_policy_ref", sa.Text()))
    op.add_column("source_record", sa.Column("retention_class", sa.Text()))
    op.add_column("source_record", sa.Column("authority_ref", sa.Text()))
    op.add_column(
        "source_record", sa.Column("authority_valid_from", sa.DateTime(timezone=True))
    )
    op.add_column(
        "source_record", sa.Column("authority_valid_to", sa.DateTime(timezone=True))
    )
    op.create_check_constraint(
        "ck_source_record_authority_window",
        "source_record",
        "authority_valid_to IS NULL OR authority_valid_from IS NULL "
        "OR authority_valid_to >= authority_valid_from",
    )


# ── downgrade ────────────────────────────────────────────────────────────────


def downgrade() -> None:
    _downgrade_source_record_seams()
    _downgrade_review_queue()
    _downgrade_claim()
    _downgrade_membership()
    _downgrade_ledger_tables()
    _downgrade_entity_and_mention()


def _downgrade_source_record_seams() -> None:
    op.drop_constraint("ck_source_record_authority_window", "source_record", type_="check")
    for column in (
        "authority_valid_to",
        "authority_valid_from",
        "authority_ref",
        "retention_class",
        "collection_policy_ref",
    ):
        op.drop_column("source_record", column)


def _downgrade_review_queue() -> None:
    # The Phase-1 status vocabulary has no 'superseded'/'expired'; those rows
    # become 'rejected' with their note intact, which is the closest the old
    # shape can express without losing the fact that a human never saw them.
    op.execute(
        sa.text(
            "UPDATE review_queue SET status = 'rejected' "
            "WHERE status IN ('superseded', 'expired')"
        )
    )
    op.drop_index("ix_review_queue_kind_status", table_name="review_queue")
    op.drop_constraint("ck_review_queue_accepted_result", "review_queue", type_="check")
    op.drop_constraint("ck_review_queue_status", "review_queue", type_="check")
    op.create_check_constraint(
        "ck_review_queue_status",
        "review_queue",
        "status IN ('suggested', 'accepted', 'rejected')",
    )
    op.drop_constraint("ck_review_queue_kind", "review_queue", type_="check")
    op.drop_constraint("uq_review_queue_idempotency_key", "review_queue", type_="unique")
    op.alter_column("review_queue", "result_claim_id", new_column_name="result_claim")
    for column in (
        "result_relation",
        "result_decision_id",
        "expires_at",
        "supersedes",
        "idempotency_key",
        "case_id",
        "record_id",
        "producer_version",
        "target_action",
        "schema_version",
        "suggestion_kind",
    ):
        op.drop_column("review_queue", column)


def _downgrade_claim() -> None:
    op.drop_index("ix_claim_object_mention", table_name="claim")
    op.drop_index("ix_claim_subject_mention", table_name="claim")
    op.drop_constraint("fk_claim_identity_revision", "claim", type_="foreignkey")
    op.drop_column("claim", "identity_revision_id")
    op.drop_constraint("ck_claim_object_anchor_needs_entity", "claim", type_="check")
    op.drop_column("claim", "object_mention_id")
    op.drop_column("claim", "subject_mention_id")


def _downgrade_membership() -> None:
    op.drop_index("ix_identity_membership_active_entity", table_name="identity_membership")
    op.drop_index("ux_membership_one_active", table_name="identity_membership")
    op.add_column(
        "identity_membership",
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "identity_membership", sa.Column("valid_to", sa.DateTime(timezone=True))
    )
    op.add_column("identity_membership", sa.Column("decision_note", sa.Text()))
    # Irrecoverable: the column was dropped because it admitted 'rule:<name>'.
    op.add_column(
        "identity_membership",
        sa.Column(
            "decided_by",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'system:downgrade'"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE identity_membership SET valid_to = now() "
            "WHERE closed_revision_id IS NOT NULL"
        )
    )
    op.create_index(
        "ix_identity_membership_mention",
        "identity_membership",
        ["mention_id", "valid_to"],
    )
    op.drop_constraint(
        "fk_identity_membership_closed_revision", "identity_membership", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_identity_membership_opened_revision", "identity_membership", type_="foreignkey"
    )
    op.drop_column("identity_membership", "closed_revision_id")
    op.drop_column("identity_membership", "opened_revision_id")


def _downgrade_ledger_tables() -> None:
    op.drop_constraint("fk_identity_decision_candidate", "identity_decision", type_="foreignkey")
    op.drop_constraint(
        "fk_identity_decision_result_revision", "identity_decision", type_="foreignkey"
    )
    op.drop_constraint("fk_identity_revision_decision", "identity_revision", type_="foreignkey")
    op.drop_table("entity_canonical_map")
    op.drop_table("identity_negative_constraint")
    op.drop_index("ix_er_candidate_mentions", table_name="er_candidate")
    op.drop_index("ix_er_candidate_open", table_name="er_candidate")
    op.drop_table("er_candidate")
    op.drop_table("identity_decision")
    op.drop_table("identity_revision")


def _downgrade_entity_and_mention() -> None:
    op.drop_constraint("ck_mention_offset_order", "mention", type_="check")
    for column in ("language", "script", "char_end", "char_start"):
        op.drop_column("mention", column)
    op.drop_column("entity", "tombstoned_at")
