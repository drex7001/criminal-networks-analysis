"""A minimal, deterministic PDF writer for ingestion fixtures (T23a).

The derivative stage needs a real PDF to read, and there are two ways to get
one: commit a binary or generate it. This generates it, for three reasons —
``*.pdf`` is gitignored repo-wide, AGENTS.md forbids committing binaries, and a
fixture you can read as source is one a reviewer can check for the data-ethics
rules instead of taking on trust.

The output is a genuine PDF (catalog → pages → page → content stream, with a
correct cross-reference table), not something shaped like one: the point is to
exercise ``pdfplumber``, so anything it would not accept is useless here.
"""

from __future__ import annotations

from typing import Sequence

# Helvetica is one of the PDF standard-14 fonts, so the reader already has its
# metrics and the file needs no embedded font program.
_FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
_FONT_SIZE = 11
_LEADING = 15
_TOP = 760
_LEFT = 54


def _escape(line: str) -> bytes:
    """PDF literal-string escaping: backslash first, then the delimiters."""
    escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return escaped.encode("latin-1", errors="replace")


def _content_stream(lines: Sequence[str]) -> bytes:
    body = bytearray(
        b"BT\n/F1 %d Tf\n%d TL\n%d %d Td\n" % (_FONT_SIZE, _LEADING, _LEFT, _TOP)
    )
    for line in lines:
        # One Tj per line with an explicit T* between them, so the extractor
        # recovers the same line breaks the fixture was written with — the
        # structural pass matches per line and would see nothing otherwise.
        body += b"(" + _escape(line) + b") Tj\nT*\n"
    body += b"ET"
    return bytes(body)


def minimal_pdf(lines: Sequence[str]) -> bytes:
    """A one-page PDF containing ``lines``, byte-for-byte reproducible."""
    content = _content_stream(lines)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        _FONT,
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"

    xref_offset = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"  # the free-list head; trailing space is required
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_offset,
    )
    return bytes(out)


# An arrest/remand annex in the shape `legacy.pipeline.structural_pass` parses.
# ALPHA and BRAVO overlap at one facility (2023-03-02 → 2023-06-30) and so
# produce exactly one co-location edge; CHARLIE is held elsewhere and produces
# none, which is what makes "1 suggestion" an assertion about the rule rather
# than about the row count.
#
# Every name here is synthetic, per the data-ethics rubric: no real person, and
# deliberately no NIC field at all.
REMAND_ANNEX_LINES: tuple[str, ...] = (
    "ANNEX B - REMAND SCHEDULE (FICTIONAL TEST FIXTURE)",
    "",
    "1. Fictional ALPHA - arrested 2023-02-14 - remanded, "
    "Northgate Remand Facility (2023-02-15 to 2023-06-30)",
    "2. Fictional BRAVO - arrested 2023-03-01 - remanded, "
    "Northgate Remand Facility (2023-03-02 to 2023-07-15)",
    "3. Fictional CHARLIE - arrested 2023-05-20 - remanded, "
    "Southmoor Remand Facility (2023-05-21 to 2023-09-01)",
)

REMAND_ANNEX_TEXT = "\n".join(REMAND_ANNEX_LINES)


def remand_annex_pdf() -> bytes:
    """The standard T23a ingestion fixture as a PDF."""
    return minimal_pdf(REMAND_ANNEX_LINES)
