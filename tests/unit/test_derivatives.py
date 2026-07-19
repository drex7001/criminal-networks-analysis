"""Derivative stage, the parts with no database in them (T23a, spec 04 §1).

The interesting behavior here is refusal: a stage that quietly returns empty
text for a scanned page, or picks a tool by guessing at a media type, produces
an extraction run that proposes nothing and explains nothing.
"""

from __future__ import annotations

import pytest

from aegis.ingestion.derivatives import (
    PDF_MEDIA_TYPE,
    EmptyDerivative,
    UnsupportedMediaType,
    ensure_text,
    pdf_to_text,
    resolve_media_type,
)
from aegis.store import SourceRecord
from tests.support.pdf import REMAND_ANNEX_TEXT, minimal_pdf, remand_annex_pdf

pytestmark = pytest.mark.requirement("spec-04-1", "T23a")


class _ExplodingSession:
    """Any attribute access fails — proves a guard ran before the database did."""

    def __getattr__(self, name: str):  # pragma: no cover - only reached on regression
        raise AssertionError(f"the session was used ({name}) before the guard rejected")


class _ExplodingVault:
    def __getattr__(self, name: str):  # pragma: no cover - only reached on regression
        raise AssertionError(f"the vault was used ({name}) before the guard rejected")


class _DictVault:
    """The vault surface ``ensure_text`` uses, backed by a dict."""

    def __init__(self, **blobs: bytes) -> None:
        self.blobs = dict(blobs)

    def get(self, content_hash: str) -> bytes:
        return self.blobs[content_hash]


def _record(**kwargs) -> SourceRecord:
    defaults = dict(
        record_id="rec_test",
        source_id="src_test",
        ingest_key="key_test",
        content_hash="a" * 64,
        storage_uri="test://fixture",
        provenance={},
    )
    return SourceRecord(**{**defaults, **kwargs})


def test_generated_pdf_round_trips_through_the_extractor() -> None:
    """The fixture is a real PDF: every line with content comes back, in order.

    Blank lines do not survive, and should not — they emit no glyphs, so there
    is nothing on the page for the extractor to find. What the structural pass
    depends on is that a *content* line arrives whole and in sequence.
    """
    written = [line for line in REMAND_ANNEX_TEXT.splitlines() if line.strip()]
    assert pdf_to_text(remand_annex_pdf()).splitlines() == written


def test_pdf_generation_is_byte_reproducible() -> None:
    """Two builds are identical, so a derivative content hash is stable."""
    assert remand_annex_pdf() == remand_annex_pdf()


def test_pdf_text_escapes_string_delimiters() -> None:
    """Unescaped parens would truncate the content stream and lose the line."""
    line = r"Exhibit (A) filed under \\case 7 (sealed)"
    assert line in pdf_to_text(minimal_pdf([line]))


def test_media_type_falls_back_to_the_original_filename() -> None:
    record = _record(media_type=None, provenance={"original_filename": "annex-b.pdf"})
    assert resolve_media_type(record) == PDF_MEDIA_TYPE


def test_declared_media_type_wins_over_the_filename() -> None:
    """The connector's declaration is evidence; the extension is a guess."""
    record = _record(media_type="text/plain", provenance={"original_filename": "notes.pdf"})
    assert resolve_media_type(record) == "text/plain"


def test_media_type_is_none_when_nothing_declares_one() -> None:
    assert resolve_media_type(_record(media_type=None, provenance={})) is None


def test_unsupported_media_type_is_refused_before_any_io() -> None:
    with pytest.raises(UnsupportedMediaType, match="video/mp4"):
        ensure_text(
            _ExplodingSession(),
            _ExplodingVault(),
            record=_record(media_type="video/mp4"),
            operator="user:analyst",
        )


def test_a_record_with_no_media_type_is_refused_rather_than_guessed() -> None:
    with pytest.raises(UnsupportedMediaType):
        ensure_text(
            _ExplodingSession(),
            _ExplodingVault(),
            record=_record(media_type=None, provenance={}),
            operator="user:analyst",
        )


def test_text_records_need_no_derivative() -> None:
    """A pasted note is already the text: inventing a derivative row for the
    identity function lengthens the provenance chain without making it truer."""
    record = _record(media_type="text/plain", content_hash="b" * 64)
    result = ensure_text(
        _ExplodingSession(),
        _DictVault(**{"b" * 64: b"pasted note"}),
        record=record,
        operator="user:analyst",
    )
    assert result.text == "pasted note"
    assert result.derivative is None
    assert result.created is False


def test_undecodable_text_is_replaced_rather_than_raised() -> None:
    """Losing one byte to U+FFFD beats losing the document (Article VIII)."""
    record = _record(media_type="text/plain", content_hash="c" * 64)
    result = ensure_text(
        _ExplodingSession(),
        _DictVault(**{"c" * 64: b"caf\xff report"}),
        record=record,
        operator="user:analyst",
    )
    assert "report" in result.text


def test_a_pdf_with_no_text_layer_is_named_as_such() -> None:
    """A scanned page needs OCR; reporting "0 suggestions" would blame the pass."""
    blank = minimal_pdf([])
    record = _record(media_type=PDF_MEDIA_TYPE, content_hash="d" * 64)

    class _NoDerivatives:
        def scalars(self, *_args, **_kwargs):
            class _Empty:
                @staticmethod
                def all() -> list:
                    return []

            return _Empty()

    with pytest.raises(EmptyDerivative, match="OCR"):
        ensure_text(
            _NoDerivatives(),
            _DictVault(**{"d" * 64: blank}),
            record=record,
            operator="user:analyst",
        )
