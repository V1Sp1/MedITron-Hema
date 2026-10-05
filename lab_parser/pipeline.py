"""Batch orchestration: one invocation is one observation, across all dates."""

from collections import Counter
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from .catalog import CATALOG, clean_name
from .extraction import MacOSOCR, extract_document
from .models import Observation
from .parsing import parse_document


def validate_policy(policy: dict[str, int]) -> dict[str, int]:
    if not isinstance(policy, dict):
        raise ValueError("Freshness policy must be a JSON object: analyte -> maximum age in days")
    for key, value in policy.items():
        if key not in CATALOG["aliases"]:
            raise ValueError(f"Unknown analyte in freshness policy: {key}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Maximum age must be a non-negative integer: {key}")
    return dict(policy)


def parse_batch(paths: list[str | Path], *, assessed_on: date | None = None,
                freshness_policy: dict[str, int] | None = None,
                ocr_mode: str = "auto", max_pages: int = 100,
                max_bytes: int = 50 * 1024 * 1024) -> Observation:
    """Read local PDFs without modifying originals or selecting model values.

    Per-document failures are recorded; missing/oversized input files and
    invalid caller configuration raise errors before extraction starts.
    """
    if not paths:
        raise ValueError("At least one PDF is required")
    if ocr_mode not in {"auto", "off", "always"}:
        raise ValueError("ocr_mode must be auto, off, or always")
    if max_pages <= 0 or max_bytes <= 0:
        raise ValueError("File/page limits must be positive")
    policy = validate_policy({} if freshness_policy is None else freshness_policy)
    local_paths = [Path(p).expanduser().resolve() for p in paths]
    for path in local_paths:
        if not path.is_file():
            raise ValueError(f"File not found: {path}")
        if path.stat().st_size > max_bytes:
            raise ValueError(f"File exceeds size limit: {path.name}")
        with path.open("rb") as stream:
            if b"%PDF-" not in stream.read(1024):
                raise ValueError(f"Not a PDF file: {path.name}")
    timestamp = datetime.now().astimezone()
    assessed_on = assessed_on or timestamp.date()
    documents, measurements, issues = [], [], []
    with MacOSOCR() as ocr:
        for index, path in enumerate(local_paths, 1):
            document = extract_document(path, f"doc-{index}", ocr if ocr_mode != "off" else None,
                                        ocr_mode, max_pages, max_bytes)
            documents.append(document)
            measurements.extend(parse_document(document, assessed_on, policy))
    hashes = Counter(d.sha256 for d in documents)
    if any(count > 1 for count in hashes.values()):
        issues.append("duplicate_documents_in_upload")
    names = {clean_name(name) for d in documents for name in d.metadata.get("patient_names", [])}
    births = {dob for d in documents for dob in d.metadata.get("birth_dates", [])}
    if len(names) > 1 or len(births) > 1:
        issues.append("possible_patient_mismatch")
    counts = Counter(m.analyte for m in measurements if m.analyte is not None)
    for measurement in measurements:
        if measurement.analyte and counts[measurement.analyte] > 1:
            measurement.issues.append("repeated_analyte_requires_selection")
    if any(count > 1 for count in counts.values()):
        issues.append("repeated_analytes_preserved")
    if any(d.issues or any(p.issues and p.issues != ["ocr_requires_review"] for p in d.pages) for d in documents):
        issues.append("extraction_incomplete_or_requires_review")
    if not measurements:
        issues.append("no_measurements_in_upload")
    return Observation(str(uuid4()), timestamp.isoformat(), assessed_on.isoformat(),
                       documents, measurements, issues, policy, alias_version=CATALOG["version"])
