"""Shared Phase 1 schema expectations."""

T4_TABLES = {
    "source",
    "source_record",
    "entity",
    "claim",
    "claim_relation",
    "review_queue",
    "case_file",
    "case_member",
    "authz_outbox",
}
EXPECTED_CHECKS = {
    "ck_source_record_status",
    "ck_source_record_authority_window",
    "ck_case_file_status",
    "ck_claim_object_exactly_one",
    "ck_claim_no_self_reference",
    "ck_claim_event_time_order",
    "ck_claim_valid_date_order",
    "ck_claim_object_anchor_needs_entity",
    "ck_claim_relation_relation",
    "ck_review_queue_status",
    "ck_review_queue_kind",
    "ck_review_queue_accepted_result",
    "ck_authz_outbox_op",
}

# P2 identity ledger (T17, spec 02 §2).  Kept separate from T4_TABLES so the
# Phase-1 contract above keeps asserting exactly what Phase 1 shipped.
LEDGER_TABLES = {
    "mention",
    "identity_revision",
    "identity_decision",
    "identity_membership",
    "er_candidate",
    "identity_negative_constraint",
    "entity_canonical_map",
}
LEDGER_CHECKS = {
    "ck_mention_offset_order",
    "ck_identity_decision_kind",
    "ck_er_candidate_disposition",
    "ck_er_candidate_pair_order",
    "ck_identity_negative_constraint_pair_order",
}

ONTOLOGY_COLUMNS = {
    "source_type",
    "reliability_normalized",
    "entity_type",
    "predicate",
    "credibility_normalized",
    "verification_status",
    "analytic_confidence",
    "handling_code",
    "role",
}
