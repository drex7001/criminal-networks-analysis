"""Derivative stage (spec 04 §1 stage 3, T23a).

Extraction reads *text*; landing accepts whatever the operator has.  This
module is the bridge: it turns a landed ``source_record`` into readable text
and records the transformation as a ``derivative`` row — tool, version, params
and the content hash of the output — so every suggestion can be traced back
through the thing it was actually read from, not merely to the original file
(Article I: the chain, not the assertion).

Idempotency (spec 04 §5).  A derivative is keyed by *(parent record, kind,
tool, tool version, params)*: re-running the stage over the same record with
the same tool reuses the existing row rather than writing a second one.  The
spec phrases that key over the *parent hash*; within a record the two are the
same key, and across two records that landed identical bytes the vault's
content addressing already collapses the stored output to one object — what
differs is only which record each derivative row hangs off, which is exactly
the provenance we want to keep distinct.

``params`` is in the key on purpose.  Changing how pages are joined changes
the text the extractor reads, so it must produce a *new* derivative instead of
silently reusing text that no longer matches how we would produce it today.
"""

from __future__ import annotations

from dataclasses import dataclass
import mimetypes
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis.actions import new_id
from aegis.evidence import EvidenceVault, ProvenanceEnvelope
from aegis.ingestion.service import IngestionError, _connector_version
from aegis.store import Derivative, SourceRecord

TEXT_KIND = "text"

PDF_MEDIA_TYPE = "application/pdf"
PDF_TOOL = "pdfplumber"
# Named so a change here is a change to the key: joining pages differently
# produces different text, hence a different derivative (module docstring).
PDF_PARAMS: dict[str, Any] = {"extractor": "page.extract_text", "page_separator": "\n\n"}

TEXT_ENCODING = "utf-8"


class UnsupportedMediaType(IngestionError):
    """No derivative tool is registered for this record's media type."""


class EmptyDerivative(IngestionError):
    """The tool ran but produced no text — a scanned PDF needs OCR, not a retry."""


@dataclass(frozen=True, slots=True)
class TextExtraction:
    """Text for the extraction passes, plus how it was obtained.

    ``derivative`` is ``None`` when the record *is* text: a pasted note needs
    no transformation, and inventing a derivative row for the identity function
    would make the provenance chain longer without making it truer.
    """

    text: str
    derivative: Derivative | None
    created: bool

    @property
    def tool(self) -> str:
        return self.derivative.tool if self.derivative else "none (record is text)"


def resolve_media_type(record: SourceRecord) -> str | None:
    """The record's declared media type, falling back to its filename."""
    if record.media_type:
        return record.media_type
    filename = (record.provenance or {}).get("original_filename")
    return mimetypes.guess_type(filename)[0] if filename else None


def pdf_to_text(data: bytes) -> str:
    """Page text joined with blank lines, skipping pages that yield nothing."""
    import io

    import pdfplumber  # lazy: only needed when a PDF actually arrives

    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
    return PDF_PARAMS["page_separator"].join(pages)


def _pdf_tool_version() -> str:
    from importlib.metadata import version

    try:
        return version(PDF_TOOL)
    except Exception:  # pragma: no cover - dependency is in pyproject
        return "unknown"


def _existing(
    session: Session,
    *,
    record: SourceRecord,
    tool: str,
    tool_version: str,
    params: dict[str, Any],
) -> Derivative | None:
    """Match on the full key.

    ``params`` is compared in Python rather than in SQL: the column is JSONB,
    whose ``=`` is a normalized whole-document comparison that is easy to get
    subtly wrong from the ORM, and the preceding columns already narrow this to
    a handful of rows.
    """
    candidates = session.scalars(
        select(Derivative).where(
            Derivative.parent_record == record.record_id,
            Derivative.kind == TEXT_KIND,
            Derivative.tool == tool,
            Derivative.tool_version == tool_version,
        )
    ).all()
    return next((row for row in candidates if row.params == params), None)


def ensure_text(
    session: Session,
    vault: EvidenceVault,
    *,
    record: SourceRecord,
    operator: str,
) -> TextExtraction:
    """Text for ``record``, producing and recording a derivative if needed.

    Raises :class:`UnsupportedMediaType` when no tool handles the media type
    and :class:`EmptyDerivative` when the tool returns nothing readable — both
    are answers the operator can act on, unlike an extraction pass that
    silently proposes zero claims.
    """
    media_type = resolve_media_type(record)

    if media_type and media_type.startswith("text/"):
        raw = vault.get(record.content_hash)
        return TextExtraction(
            text=raw.decode(TEXT_ENCODING, errors="replace"), derivative=None, created=False
        )

    if media_type != PDF_MEDIA_TYPE:
        raise UnsupportedMediaType(
            f"no text derivative tool for media type {media_type!r} "
            f"(supported: text/*, {PDF_MEDIA_TYPE})"
        )

    tool_version = _pdf_tool_version()
    existing = _existing(
        session, record=record, tool=PDF_TOOL, tool_version=tool_version, params=PDF_PARAMS
    )
    if existing is not None:
        return TextExtraction(
            text=vault.get(existing.content_hash).decode(TEXT_ENCODING, errors="replace"),
            derivative=existing,
            created=False,
        )

    text = pdf_to_text(vault.get(record.content_hash))
    if not text.strip():
        raise EmptyDerivative(
            f"{PDF_TOOL} found no text in {record.record_id} — a scanned document "
            "needs OCR, which this pipeline does not run"
        )

    stored = vault.put(
        text.encode(TEXT_ENCODING),
        ProvenanceEnvelope(
            source_system="derivative",
            original_filename=f"{record.record_id}.text.txt",
            connector="aegis.ingestion.derivatives",
            connector_version=_connector_version(),
            operator=operator,
            notes=f"{TEXT_KIND} derivative of {record.record_id} via {PDF_TOOL} {tool_version}",
        ),
        media_type="text/plain",
    )
    derivative = Derivative(
        derivative_id=new_id("der"),
        parent_record=record.record_id,
        kind=TEXT_KIND,
        tool=PDF_TOOL,
        tool_version=tool_version,
        params=PDF_PARAMS,
        operator=operator,
        content_hash=stored.content_hash,
        storage_uri=stored.storage_uri,
    )
    session.add(derivative)
    session.flush()
    return TextExtraction(text=text, derivative=derivative, created=True)
