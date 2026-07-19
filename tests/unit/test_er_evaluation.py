"""T26 numeric ER quality gates over deterministic fictional data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegis.cli import app
from aegis.er import evaluation

pytestmark = pytest.mark.requirement("Article-VII", "H-08", "T26")


def test_first_passing_run_is_pinned_to_numeric_metrics() -> None:
    report = evaluation.evaluate()

    assert report.passed is True
    assert report.rule_pairwise_precision == 1.0
    assert report.transliteration_pairwise_recall == 1.0
    assert report.review_load_per_1000_mentions == pytest.approx(33.333333)
    assert report.rule_true_positives == 1
    assert report.rule_false_positives == 0
    assert report.transliteration_true_positives == 2
    assert report.emitted_candidate_pairs == 2
    assert report.distinct_pairs_emitted == 0
    assert report.automatic_merges == 0


def test_cli_writes_machine_readable_report_and_fails_below_a_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "er-evaluation.json"
    passed = CliRunner().invoke(
        app, ["identity", "evaluate", "--output", str(output)]
    )
    assert passed.exit_code == 0, passed.output
    assert "rule precision=1.000" in passed.output
    assert "ER QUALITY GATE PASSED" in passed.output
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is True

    monkeypatch.setattr(evaluation, "REVIEW_LOAD_CEILING_PER_1000", 0.0)
    failed = CliRunner().invoke(
        app, ["identity", "evaluate", "--output", str(output)]
    )
    assert failed.exit_code == 1
    assert "ER QUALITY GATE FAILED" in failed.output
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is False


def test_golden_set_rejects_truth_label_drift(tmp_path: Path) -> None:
    data = json.loads(evaluation.DEFAULT_GOLDEN_SET.read_text(encoding="utf-8"))
    data["mentions"][5]["truth_entity"] = data["mentions"][4]["truth_entity"]
    path = tmp_path / "bad-golden.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(evaluation.EvaluationError, match="disagrees with truth labels"):
        evaluation.evaluate(path)


def test_identifier_values_are_obviously_fictional_placeholders() -> None:
    data = json.loads(evaluation.DEFAULT_GOLDEN_SET.read_text(encoding="utf-8"))
    values = [
        identifier["value"]
        for mention in data["mentions"]
        for identifier in mention.get("identifiers", [])
    ]
    assert values
    assert all(value.upper().startswith("FIXTURE-ID-") for value in values)
