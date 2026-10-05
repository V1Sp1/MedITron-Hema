"""Conservative row parser. Uncertain values stay candidates for review."""

import re
import math
from datetime import date

from .catalog import clean_name, normalize_unit, resolve_name
from .models import Document, Measurement, Row, Source


DATE_RE = re.compile(r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{2}[./-]\d{2}[./-]\d{4})(?!\d)")
NUMBER_RE = re.compile(r"^\s*(?P<cmp><=|>=|[<>≤≥])?\s*(?P<num>[+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?)")
COLLECTION_RE = re.compile(
    r"(?:дата(?:\s+и\s+время|\s*/\s*время)?\s+(?:взятия|забора|сбора)(?:\s+(?:биоматериала|материала|крови))?|(?:взятие|забор|сбор)\s+(?:биоматериала|материала|крови)|"
    r"sample\s+collected|collection\s+date|collected\s+(?:on|at))\s*[:：]?", re.I)
BIRTH_RE = re.compile(r"(?:дата\s+рождения|date\s+of\s+birth|DOB)\s*[:：]?", re.I)
REPORT_RE = re.compile(r"(?:дата(?:\s+и\s+время)?\s+(?:выдачи|печати|готовности)|report\s+date)\s*[:：]?", re.I)
PATIENT_RE = re.compile(r"(?:пациент(?:ка)?|Ф\.?\s*И\.?\s*О\.?|patient(?:\s+name)?)\s*[:：]\s*(.+?)(?=\s+(?:Отделение|Пол|Дата|Договор)\b|$)", re.I)
SPECIMEN_RE = re.compile(r"(?:вид\s*материала|биоматериал|specimen|sample\s+type)\s*[:：]\s*(.+?)(?=\s+(?:Валидация|Регистрация|Дата)\s*[:(]|$)", re.I)
UNIT_TOKEN_RE = re.compile(r"^(?:[%‰]|(?:[хx×*]?\s*)?10\s*(?:\^\s*)?[⁰¹²³⁴⁵⁶⁷⁸⁹\d]+\s*/\s*\w+|[a-zA-Zа-яА-Яµμ]+(?:\s*/\s*[\w.,²³]+)+|пг|фл|pg|fL)$")
FLAG_RE = re.compile(r"\s*(?:\*+|[↑↓]|\(?(?:H|L|высокий|низкий)\)?(?![\w/]))\s*", re.I)


def parse_date(raw: str) -> str | None:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return date.fromisoformat(raw).isoformat()
        d, m, y = re.split(r"[./-]", raw)
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def labeled_dates(text: str, label: re.Pattern) -> list[str]:
    found = []
    for match in label.finditer(text):
        # Stop at the next label/field; never borrow a date from DOB/report date.
        rest = text[match.end():].lstrip()
        value = DATE_RE.match(rest)
        if value and parse_date(value.group()):
            found.append(parse_date(value.group()))
    return found


def header_kind(text: str) -> str | None:
    name = clean_name(text)
    if name in {"исследование", "показатель", "наименование", "наименование исследования", "наименование теста", "название показатель", "тест", "test", "analyte"}:
        return "name"
    if name in {"результат", "результаты", "result", "value"}:
        return "value"
    if name in {"ед", "ед изм", "единицы", "единицы измерения", "unit", "units"}:
        return "unit"
    if name in {"норма", "нормы", "reference", "reference range"} or name.startswith(("референс", "референт")):
        return "reference"
    if name in {"дата забора", "дата взятия", "collection date"}:
        return "date"
    return None


def detect_header(row: Row) -> tuple[dict[str, int], list[float] | None] | None:
    if row.cells is not None:
        indices = {kind: i for i, cell in enumerate(row.cells) if (kind := header_kind(cell))}
        if "name" in indices and "value" in indices:
            return indices, None
        return None
    # Unruled tables: separated header cells define x positions of columns.
    groups: list[list] = []
    for word in row.words:
        if not groups or word.x0 - groups[-1][-1].x1 > 12:
            groups.append([word])
        else:
            groups[-1].append(word)
    indices = {}
    starts = []
    for i, group in enumerate(groups):
        starts.append(group[0].x0)
        kind = header_kind(" ".join(w.text for w in group))
        if kind:
            indices[kind] = i
    if "name" in indices and "value" in indices:
        return indices, starts
    return None


def column_cells(row: Row, starts: list[float]) -> list[str]:
    cells = ["" for _ in starts]
    for word in row.words:
        index = max((i for i, start in enumerate(starts) if word.x0 >= start - 10), default=0)
        cells[index] = (cells[index] + " " + word.text).strip()
    return cells


def _split_free_row(text: str) -> tuple[str, str] | None:
    text = re.sub(r"\s*\|\s*", " ", text).strip()
    # Choose the longest recognized complete prefix, including B12/B6 digits.
    # A value must immediately follow the name; a footnote is not a result.
    candidates = []
    for match in re.finditer(r"\s+|\s*[:：|]\s*", text):
        name, tail = text[:match.start()].strip(), text[match.end():].strip()
        if resolve_name(name) and (NUMBER_RE.match(tail) or tail in {"—", "-", "не выполнено"}):
            candidates.append((name, tail))
    if candidates:
        return max(candidates, key=lambda c: len(c[0]))
    # Unknown numeric rows are retained only if they include a recognizable
    # unit. Administrative identifiers and dates are not measurements.
    match = re.match(r"^(.+?)\s+([<>≤≥]?\s*[+-]?\d+(?:[.,]\d+)?\s+\S+.*)$", text)
    if match and not DATE_RE.search(text):
        name, tail = match.groups()
        if len(name) > 70 or re.search(r"пример|комментарий|пересчет|пересчёт|у вас|составляет|необходимо|формул", name, re.I):
            return None
        if not any(word in clean_name(name).split() for word in ["заказ", "номер", "код", "телефон", "возраст"]):
            number = NUMBER_RE.match(tail)
            unit = tail[number.end():].strip().split()[0] if number and tail[number.end():].strip() else ""
            if normalize_unit(unit) or UNIT_TOKEN_RE.fullmatch(unit):
                return name, tail
    return None


def parse_value(text: str) -> tuple[float | None, str | None, str, str, list[str]]:
    text = re.sub(r"^\s*[↑↓]\s*", "", text)
    match = NUMBER_RE.match(text)
    if not match:
        return None, None, text.strip(), "", ["non_numeric_or_missing_result"]
    tail = text[match.end():]
    # Ranges, thousands separators and malformed decimals are never read as
    # one exact scalar. A following unit exponent belongs to the tail.
    exponent_unit = re.match(r"\s*(?:[хx×*]\s*)?10\s*(?:\^\s*)?[⁰¹²³⁴⁵⁶⁷⁸⁹\d]+\s*/", tail)
    if re.match(r"\s*[–—-]\s*\d", tail) or re.match(r"[.,]\d", tail) or (re.match(r"\s+\d", tail) and not exponent_unit):
        return None, None, text.strip(), "", ["ambiguous_numeric_result"]
    value = float(match["num"].replace(",", "."))
    if not math.isfinite(value):
        return None, None, text.strip(), "", ["non_finite_result"]
    comparator = {"≤": "<=", "≥": ">="}.get(match["cmp"], match["cmp"])
    issues = []
    if value < 0:
        issues.append("negative_result")
    if comparator:
        issues.append("censored_result")
    return value, comparator, match.group().strip(), tail.strip(), issues


def split_unit_reference(tail: str) -> tuple[str | None, str | None, list[str]]:
    tail = FLAG_RE.sub("", tail, count=1) if FLAG_RE.match(tail) else tail
    if not tail:
        return None, None, ["unit_missing"]
    tokens = tail.split()
    # Try the longest unit prefix first (e.g. "10^9 / л").
    for count in range(min(4, len(tokens)), 0, -1):
        candidate = " ".join(tokens[:count])
        if normalize_unit(candidate) or UNIT_TOKEN_RE.fullmatch(candidate):
            rest = " ".join(tokens[count:]).strip()
            return candidate, rest or None, []
    return None, tail, ["unit_missing_or_ambiguous"]


def parse_document(document: Document, assessed_on: date, policy: dict[str, int]) -> list[Measurement]:
    texts = [row.text for page in document.pages for row in page.rows]
    metadata = {
        "collection_dates": sorted(set(d for text in texts for d in labeled_dates(text, COLLECTION_RE))),
        "birth_dates": sorted(set(d for text in texts for d in labeled_dates(text, BIRTH_RE))),
        "report_dates": sorted(set(d for text in texts for d in labeled_dates(text, REPORT_RE))),
        "patient_names": sorted(set(m.group(1).strip() for text in texts if (m := PATIENT_RE.search(text))
                                    and not re.match(r"(?:Фамилия|Имя|Отчество|Контингент)\s*:", m.group(1), re.I))),
        "specimens": sorted(set(m.group(1).strip() for text in texts if (m := SPECIMEN_RE.search(text)))),
    }
    document.metadata = {key: values for key, values in metadata.items() if values}
    defaults = metadata["collection_dates"]
    default_date = defaults[0] if len(defaults) == 1 else None
    results = []
    for page in document.pages:
        page_dates = sorted(set(d for row in page.rows for d in labeled_dates(row.text, COLLECTION_RE)))
        current_date = page_dates[0] if len(page_dates) == 1 else default_date
        date_source = "page_collection_date" if len(page_dates) == 1 else "document_collection_date" if default_date else None
        specimens = [m.group(1).strip() for row in page.rows if (m := SPECIMEN_RE.search(row.text))]
        current_specimen = specimens[0] if len(set(specimens)) == 1 else None
        layouts = {}
        section_name = None
        section_row = None
        for row in page.rows:
            if re.match(r"\s*(?:Метод\b|Комментарий\b|Интерпретац|\*\s*[-–]|ЗАКАЗ\b|ЗАРЕГИСТРИРОВАН\b)", row.text, re.I):
                continue
            if re.search(r"пример пересчет|пример пересчёт|у вас|составляет|необходимо использовать|интерпретировать", row.text, re.I):
                continue
            dates = labeled_dates(row.text, COLLECTION_RE)
            if dates:
                current_date, date_source = dates[-1], "section_collection_date"
                continue
            if COLLECTION_RE.search(row.text) and DATE_RE.search(row.text):
                current_date, date_source = None, None
                document.issues.append("invalid_collection_date")
                continue
            specimen_match = SPECIMEN_RE.search(row.text)
            if specimen_match:
                current_specimen = specimen_match.group(1).strip()
                continue
            header = detect_header(row)
            if header:
                layouts[row.table_id] = header
                continue
            layout = layouts.get(row.table_id) or layouts.get(None)
            row_date = current_date
            row_date_source = date_source
            issues = []
            used_context = False
            if layout:
                indices, starts = layout
                cells = row.cells if starts is None else column_cells(row, starts)
                if cells is None:
                    continue
                def get(kind):
                    idx = indices.get(kind)
                    return cells[idx].strip() if idx is not None and idx < len(cells) else ""
                name, raw_result = get("name"), get("value")
                if not name:
                    continue
                if header_kind(name) or COLLECTION_RE.search(name) or BIRTH_RE.search(name) or REPORT_RE.search(name):
                    continue
                if not raw_result and not get("unit") and not get("reference"):
                    # A test title above a "Concentration/Activity" result is
                    # context, not a measured value with a missing result.
                    section_name = name
                    section_row = row
                    continue
                if not raw_result and not resolve_name(name):
                    continue
                if not resolve_name(name) and not NUMBER_RE.match(raw_result):
                    continue
                if section_name and clean_name(name).rstrip() in {"концентрация", "концентрация ↑", "активность", "расчет", "результат", "скорость оседания", "доля"}:
                    name = section_name
                    used_context = True
                raw_unit = get("unit") or None
                reference = get("reference") or None
                if get("date"):
                    match = DATE_RE.search(get("date"))
                    row_date = parse_date(match.group()) if match else None
                    row_date_source = "row_collection_date" if row_date else None
                    if not row_date:
                        issues.append("invalid_collection_date")
                value, comparator, raw_value, tail, value_issues = parse_value(raw_result)
                issues.extend(value_issues)
                if tail and not FLAG_RE.fullmatch(tail):
                    # Sometimes the unit is printed in the result cell.
                    if not raw_unit:
                        raw_unit, tail_reference, tail_issues = split_unit_reference(tail)
                        reference = reference or tail_reference
                        issues.extend(tail_issues)
                    else:
                        issues.append("unexpected_result_suffix")
            else:
                pair = _split_free_row(row.text)
                if not pair:
                    continue
                name, raw_result = pair
                value, comparator, raw_value, tail, value_issues = parse_value(raw_result)
                issues.extend(value_issues)
                raw_unit, reference, unit_issues = split_unit_reference(tail)
                issues.extend(unit_issues)
                issues.append("inferred_column_order")
            analyte = resolve_name(name)
            if current_specimen and re.search(r"моч[аи]|urine|кал|stool|слюна|saliva", current_specimen, re.I):
                if analyte:
                    analyte = None
                issues.append("specimen_not_supported_by_model")
            if not analyte:
                issues.append("unknown_analyte")
            unit = normalize_unit(raw_unit)
            if not raw_unit:
                issues.append("unit_missing")
            elif unit is None:
                issues.append("unknown_unit")
            # Model units are not supplied yet; spelling normalization is not
            # conversion into the units of the training CSV.
            issues.append("model_units_unconfirmed")
            age_days = (assessed_on - date.fromisoformat(row_date)).days if row_date else None
            if age_days is None:
                freshness = "unknown_date"
                issues.append("collection_date_missing")
            elif age_days < 0:
                freshness = "future_date"
                issues.append("collection_date_in_future")
            elif analyte in policy:
                freshness = "stale" if age_days > policy[analyte] else "within_policy"
                if freshness == "stale":
                    issues.append("stale_by_configured_policy")
            else:
                freshness = "no_policy"
            confidences = [w.confidence for w in row.words if w.confidence is not None]
            if page.method == "macos_ocr":
                issues.append("ocr_requires_review")
                if any(w.geometry_approximate for w in row.words):
                    issues.append("approximate_ocr_coordinates")
            source = Source(document.id, page.number, row.bbox, row.text, page.method,
                            min(confidences) if confidences else None,
                            section_row.text if used_context else None,
                            section_row.bbox if used_context else None)
            results.append(Measurement(f"{document.id}-m{len(results)+1}", analyte, name, raw_value,
                                       value, comparator, raw_unit, unit, reference, row_date,
                                       row_date_source, age_days, freshness, source, sorted(set(issues)),
                                       specimen=current_specimen))
    if not results:
        document.issues.append("no_measurements_recognized")
    return results
