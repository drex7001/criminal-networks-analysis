"""Deterministic ER quality gates over the fictional T26 golden set.

The harness is deliberately database-free: it evaluates the pure identifier
matcher used by ``run_rules`` and the exact versioned Splink settings used by
``run_splink``.  It emits candidates only in memory and therefore cannot make
an identity decision or alter canonical membership (Article VII).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis.er.features import FeatureFrame
from aegis.er.normalize import detect_script, norm_key
from aegis.er.rules import (
    IdentifierObservation,
    MentionKeyObservation,
    evaluate_preverified_identifiers,
    evaluate_same_key_in_document,
)
from aegis.er.settings import (
    REVIEW_LOAD_CEILING_PER_1000,
    RULE_PRECISION_FLOOR,
    RULES_VERSION,
    SPLINK_MATCH_THRESHOLD,
    SPLINK_VERSION,
    TRANSLITERATION_RECALL_FLOOR,
)
from aegis.er.splink_job import score_splink
from aegis.er.translit import latin_key, phonetic_key, script_key

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN_SET = REPO_ROOT / "data" / "sample" / "mvp" / "er-golden-set.json"
DEFAULT_REPORT = REPO_ROOT / "output" / "er-evaluation.json"


class EvaluationError(RuntimeError):
    """The golden set is invalid or one or more quality gates failed."""


class _Identifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicate: str
    value: str
    jurisdiction: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None

    @field_validator("value")
    @classmethod
    def fictional_identifier_only(cls, value: str) -> str:
        if not value.upper().startswith("FIXTURE-ID-"):
            raise ValueError("CI identifiers must use the FIXTURE-ID- placeholder prefix")
        return value


class _Mention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    truth_entity: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    record: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    aliases: list[str] = Field(default_factory=list)
    affiliations: list[str] = Field(default_factory=list)
    associates: list[str] = Field(default_factory=list)
    date_of_birth: date | None = None
    identifiers: list[_Identifier] = Field(default_factory=list)


class _Pair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: str
    right: str

    def key(self) -> tuple[str, str]:
        left, right = sorted((self.left, self.right))
        return left, right


class _GoldenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["aegis.er-golden/v1"] = Field(alias="schema")
    description: str
    mentions: list[_Mention] = Field(min_length=2)
    transliteration_pairs: list[_Pair] = Field(min_length=1)
    distinct_pairs: list[_Pair] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_truth(self) -> "_GoldenSet":
        by_id = {mention.id: mention for mention in self.mentions}
        if len(by_id) != len(self.mentions):
            raise ValueError("mention ids must be unique")
        for label, pairs, expected_same in (
            ("transliteration", self.transliteration_pairs, True),
            ("distinct", self.distinct_pairs, False),
        ):
            seen: set[tuple[str, str]] = set()
            for pair in pairs:
                if pair.left == pair.right or pair.left not in by_id or pair.right not in by_id:
                    raise ValueError(f"{label} pair references an invalid mention")
                if pair.key() in seen:
                    raise ValueError(f"duplicate {label} pair: {pair.key()}")
                seen.add(pair.key())
                same = by_id[pair.left].truth_entity == by_id[pair.right].truth_entity
                if same is not expected_same:
                    raise ValueError(f"{label} pair disagrees with truth labels: {pair.key()}")
        return self


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    schema: str
    golden_set_sha256: str
    mention_count: int
    rules_version: str
    splink_version: str
    splink_match_threshold: float
    rule_true_positives: int
    rule_false_positives: int
    rule_pairwise_precision: float
    transliteration_true_positives: int
    transliteration_pairs: int
    transliteration_pairwise_recall: float
    emitted_candidate_pairs: int
    review_load_per_1000_mentions: float
    distinct_pairs_emitted: int
    automatic_merges: int
    rule_precision_floor: float
    transliteration_recall_floor: float
    review_load_ceiling_per_1000: float
    passed: bool

    def to_dict(self) -> dict[str, str | int | float | bool]:
        return asdict(self)


def _load(path: Path) -> tuple[_GoldenSet, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvaluationError(f"cannot read golden set {path}: {exc}") from exc
    try:
        golden = _GoldenSet.model_validate_json(raw)
    except Exception as exc:
        raise EvaluationError(f"invalid ER golden set: {exc}") from exc
    return golden, sha256(raw).hexdigest()


def _feature_frame(golden: _GoldenSet, digest: str) -> FeatureFrame:
    rows = []
    for mention in golden.mentions:
        rows.append(
            {
                "unique_id": mention.id,
                # Evaluation starts with one unresolved entity per mention,
                # exactly as the live pre-adjudication pipeline does.
                "entity_id": f"eval:{mention.id}",
                "latin_key": latin_key(mention.name),
                "script_key": script_key(mention.name),
                "phonetic_key": phonetic_key(mention.name),
                "norm_key": norm_key(mention.name),
                "script": detect_script(mention.name),
                "alias_keys": [latin_key(alias) for alias in mention.aliases],
                "affiliations": [norm_key(value) for value in mention.affiliations],
                "associates": sorted(set(mention.associates)),
                "date_of_birth": (
                    mention.date_of_birth.isoformat()
                    if mention.date_of_birth is not None
                    else None
                ),
            }
        )
    return FeatureFrame(rows=rows, graph_snapshot_id=f"golden:sha256:{digest[:32]}")


def _identifier_observations(golden: _GoldenSet) -> list[IdentifierObservation]:
    observations: list[IdentifierObservation] = []
    for mention in golden.mentions:
        for index, identifier in enumerate(mention.identifiers):
            observations.append(
                IdentifierObservation(
                    mention_id=mention.id,
                    entity_id=f"eval:{mention.id}",
                    claim_id=f"golden:{mention.id}:{index}",
                    predicate=identifier.predicate,
                    value=identifier.value,
                    jurisdiction=identifier.jurisdiction,
                    valid_from=identifier.valid_from,
                    valid_to=identifier.valid_to,
                )
            )
    return observations


def evaluate(path: Path = DEFAULT_GOLDEN_SET) -> EvaluationReport:
    """Compute all T26 gates using production matchers and settings."""
    golden, digest = _load(path)
    truth = {mention.id: mention.truth_entity for mention in golden.mentions}

    rules = evaluate_preverified_identifiers(_identifier_observations(golden))
    rule_pairs = {(pair.mention_a, pair.mention_b) for pair in rules.pairs}
    rule_tp = sum(truth[left] == truth[right] for left, right in rule_pairs)
    rule_fp = len(rule_pairs) - rule_tp
    rule_precision = rule_tp / len(rule_pairs) if rule_pairs else 0.0

    scored = score_splink(_feature_frame(golden, digest))
    splink_pairs = {
        (pair.mention_a, pair.mention_b)
        for pair in scored
        if pair.probability >= SPLINK_MATCH_THRESHOLD
    }
    transliteration = {pair.key() for pair in golden.transliteration_pairs}
    transliteration_tp = len(transliteration & splink_pairs)
    transliteration_recall = transliteration_tp / len(transliteration)

    # Rules run first in the live fixture; Splink skips a pair already open.
    # The union is therefore the exact review queue load of the full pipeline.
    same_document_pairs = set(
        evaluate_same_key_in_document(
            MentionKeyObservation(
                mention_id=mention.id,
                record_id=mention.record,
                norm_key=norm_key(mention.name),
            )
            for mention in golden.mentions
        )
    )
    candidate_pairs = rule_pairs | same_document_pairs | splink_pairs
    review_load = len(candidate_pairs) * 1000.0 / len(golden.mentions)
    distinct = {pair.key() for pair in golden.distinct_pairs}
    distinct_emitted = len(candidate_pairs & distinct)

    passed = (
        rule_precision >= RULE_PRECISION_FLOOR
        and transliteration_recall >= TRANSLITERATION_RECALL_FLOOR
        and review_load <= REVIEW_LOAD_CEILING_PER_1000
    )
    return EvaluationReport(
        schema="aegis.er-evaluation/v1",
        golden_set_sha256=f"sha256:{digest}",
        mention_count=len(golden.mentions),
        rules_version=RULES_VERSION,
        splink_version=SPLINK_VERSION,
        splink_match_threshold=SPLINK_MATCH_THRESHOLD,
        rule_true_positives=rule_tp,
        rule_false_positives=rule_fp,
        rule_pairwise_precision=round(rule_precision, 6),
        transliteration_true_positives=transliteration_tp,
        transliteration_pairs=len(transliteration),
        transliteration_pairwise_recall=round(transliteration_recall, 6),
        emitted_candidate_pairs=len(candidate_pairs),
        review_load_per_1000_mentions=round(review_load, 6),
        distinct_pairs_emitted=distinct_emitted,
        # Neither evaluated producer has an identity-write capability.  This
        # explicit field makes that invariant visible in the CI artifact.
        automatic_merges=0,
        rule_precision_floor=RULE_PRECISION_FLOOR,
        transliteration_recall_floor=TRANSLITERATION_RECALL_FLOOR,
        review_load_ceiling_per_1000=REVIEW_LOAD_CEILING_PER_1000,
        passed=passed,
    )


def write_report(report: EvaluationReport, path: Path = DEFAULT_REPORT) -> None:
    """Write stable, reviewable JSON for CI artifact publication."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


__all__ = [
    "DEFAULT_GOLDEN_SET",
    "DEFAULT_REPORT",
    "EvaluationError",
    "EvaluationReport",
    "evaluate",
    "write_report",
]
