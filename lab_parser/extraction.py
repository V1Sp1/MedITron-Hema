"""PDF text/table extraction with an optional on-device macOS OCR backend."""

import hashlib
import json
import math
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

import pdfplumber

from .models import Document, Page, Row, Word


class MacOSOCR:
    """Compile Apple's Vision bridge once per batch into a temporary directory."""

    def __init__(self, timeout: int = 90):
        self.timeout = timeout
        self._temp: tempfile.TemporaryDirectory | None = None
        self._binary: Path | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self._temp:
            self._temp.cleanup()

    def _prepare(self) -> Path:
        if self._binary:
            return self._binary
        if platform.system() != "Darwin" or not shutil.which("swiftc"):
            raise RuntimeError("macOS OCR requires macOS and Xcode Command Line Tools (swiftc)")
        self._temp = tempfile.TemporaryDirectory(prefix="lab-parser-ocr-")
        root = Path(self._temp.name)
        binary = root / "macos-ocr"
        source = Path(__file__).with_name("macos_ocr.swift")
        result = subprocess.run(
            ["swiftc", "-module-cache-path", str(root / "module-cache"), str(source), "-o", str(binary)],
            capture_output=True, text=True, timeout=self.timeout, check=False,
        )
        if result.returncode:
            raise RuntimeError("Cannot compile local OCR bridge: " + result.stderr[-1500:])
        self._binary = binary
        return binary

    def extract(self, path: Path, page: int) -> list[Word]:
        result = subprocess.run([str(self._prepare()), str(path), str(page)],
                                capture_output=True, text=True, timeout=self.timeout, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Local OCR failed")
        data = json.loads(result.stdout)
        return [Word(**word) for word in data["words"]]


def rows_from_words(words: list[Word]) -> list[Row]:
    """Group by vertical overlap, then retain column geometry within each row."""
    groups: list[list[Word]] = []
    for word in sorted(words, key=lambda w: ((w.top + w.bottom) / 2, w.x0)):
        if not groups:
            groups.append([word])
            continue
        group = groups[-1]
        center = sum((w.top + w.bottom) / 2 for w in group) / len(group)
        tolerance = max(2.5, min(word.bottom - word.top, max(w.bottom - w.top for w in group)) * .45)
        if abs((word.top + word.bottom) / 2 - center) <= tolerance:
            group.append(word)
        else:
            groups.append([word])
    rows = []
    for group in groups:
        group.sort(key=lambda w: w.x0)
        rows.append(Row(" ".join(w.text for w in group),
                        [min(w.x0 for w in group), min(w.top for w in group),
                         max(w.x1 for w in group), max(w.bottom for w in group)], group))
    return rows


def _native_page(page, number: int) -> Page:
    words = [Word(w["text"], w["x0"], w["top"], w["x1"], w["bottom"])
             for w in page.dedupe_chars().extract_words(x_tolerance=2, y_tolerance=3)]
    rows = rows_from_words(words)
    tables = page.find_tables()
    outside = []
    for row in rows:
        # A native text row inside a ruled table is replaced with actual cells.
        if not any(t.bbox[0] <= (row.bbox[0] + row.bbox[2]) / 2 <= t.bbox[2]
                   and t.bbox[1] <= (row.bbox[1] + row.bbox[3]) / 2 <= t.bbox[3] for t in tables):
            outside.append(row)
    for table_id, table in enumerate(tables):
        for table_row, cells in zip(table.rows, table.extract()):
            values = [(c or "").replace("\n", " ").strip() for c in cells]
            bbox = list(table_row.bbox)
            row_words = [w for w in words if bbox[0] <= (w.x0 + w.x1) / 2 <= bbox[2]
                         and bbox[1] <= (w.top + w.bottom) / 2 <= bbox[3]]
            outside.append(Row(" | ".join(values), bbox, row_words, values, table_id))
    outside.sort(key=lambda row: (row.bbox[1], row.bbox[0]))
    return Page(number, float(page.width), float(page.height), "pdf_text", outside)


def _text_usable(page: Page) -> bool:
    text = " ".join(row.text for row in page.rows)
    if sum(c.isalnum() for c in text) < 25:
        return False
    broken = text.count("\ufffd") + text.count("(cid:") * 6
    return broken / max(len(text), 1) < .05


def extract_document(path: Path, document_id: str, ocr: MacOSOCR | None,
                     ocr_mode: str = "auto", max_pages: int = 100,
                     max_bytes: int = 50 * 1024 * 1024) -> Document:
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"File exceeds size limit ({max_bytes} bytes): {path.name}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    document = Document(document_id, path.name, digest)
    try:
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) > max_pages:
                document.issues.append(f"page_limit_exceeded:{len(pdf.pages)}")
                return document
            for number, page in enumerate(pdf.pages, 1):
                if not (0 < page.width <= 14400 and 0 < page.height <= 14400
                        and math.isfinite(page.width) and math.isfinite(page.height)):
                    document.pages.append(Page(number, 0, 0, "unavailable", issues=["invalid_page_dimensions"]))
                    continue
                try:
                    native = _native_page(page, number)
                except Exception as exc:
                    native = Page(number, float(page.width), float(page.height), "unavailable",
                                  issues=[f"text_extraction_failed:{type(exc).__name__}"])
                usable = _text_usable(native)
                # Mixed image/text pages may have only a small textual header.
                image_area = sum(i["width"] * i["height"] for i in page.images)
                image_dominant = image_area > page.width * page.height * .4
                needs_ocr = ocr_mode == "always" or not usable or image_dominant
                if needs_ocr and ocr_mode != "off" and ocr is not None:
                    try:
                        words = ocr.extract(path, number)
                        if words:
                            native = Page(number, float(page.width), float(page.height), "macos_ocr",
                                          rows_from_words(words), ["ocr_requires_review"])
                        else:
                            native.issues.append("ocr_no_text")
                    except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError) as exc:
                        native.issues.append(f"ocr_failed:{exc}")
                elif needs_ocr:
                    native.issues.append("ocr_required")
                if not native.rows:
                    native.issues.append("no_text_extracted")
                document.pages.append(native)
    except Exception as exc:
        document.issues.append(f"pdf_read_failed:{type(exc).__name__}")
    return document
