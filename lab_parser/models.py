"""Auditable intermediate and output structures; no clinical interpretation."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    confidence: float | None = None
    geometry_approximate: bool = False


@dataclass
class Row:
    text: str
    bbox: list[float]
    words: list[Word] = field(default_factory=list)
    cells: list[str] | None = None
    table_id: int | None = None


@dataclass
class Page:
    number: int
    width: float
    height: float
    method: str
    rows: list[Row] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class Document:
    id: str
    filename: str
    sha256: str
    pages: list[Page] = field(default_factory=list)
    metadata: dict[str, list[str]] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


@dataclass
class Source:
    document_id: str
    page: int
    bbox: list[float]
    text: str
    method: str
    ocr_confidence: float | None = None
    context_text: str | None = None
    context_bbox: list[float] | None = None


@dataclass
class Measurement:
    id: str
    analyte: str | None
    raw_name: str
    raw_value: str
    value: float | None
    comparator: str | None
    raw_unit: str | None
    unit: str | None
    reference: str | None
    collected_on: str | None
    date_source: str | None
    age_days: int | None
    freshness: str
    source: Source
    issues: list[str] = field(default_factory=list)
    normalized_value: float | None = None
    normalized_unit: str | None = None
    review_status: str = "pending"
    specimen: str | None = None


@dataclass
class Observation:
    id: str
    uploaded_at: str
    assessed_on: str
    documents: list[Document]
    measurements: list[Measurement]
    issues: list[str]
    freshness_policy: dict[str, int]
    schema_version: str = "1.0"
    parser_version: str = "0.1.0"
    alias_version: str = "1.0"
    review_status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
