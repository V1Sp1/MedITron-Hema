"""Use the supplied dictionary; never infer units from patient values."""

import json
import math
from collections import Counter
from copy import deepcopy
from pathlib import Path

from lab_parser.catalog import normalize_unit

ROOT = Path(__file__).resolve().parents[1]
DICTIONARY = json.loads((ROOT / "data/feature_dictionary.json").read_text("utf-8"))
FEATURES = {c["name"]: {k: c[k] for k in ("name", "description", "unit")} for c in DICTIONARY["columns"] if c["role"] == "feature"}
LAB_KEYS = [k for k in FEATURES if k not in {"age_years", "sex"}]
DICTIONARY_VERSION = DICTIONARY["source"]["sha256"]

# Dimensional scale changes only. No molar/mass or assay conversions.
MASS = {"g/L": 1, "g/dL": 10, "mg/L": .001, "mg/dL": .01,
        "µg/L": 1e-6, "ng/mL": 1e-6, "pg/mL": 1e-9}
MOLAR = {"mmol/L": 1000, "µmol/L": 1, "nmol/L": .001, "pmol/L": .000001}


def convert_value(analyte: str, value: float, unit: str | None) -> tuple[float, str, str] | None:
    target = FEATURES.get(analyte, {}).get("unit")
    unit = normalize_unit(unit) or unit
    if unit is None or target is None or not math.isfinite(value) or value < 0:
        return None
    if unit == target:
        return value, target, "identity"
    factor = None
    for scales in (MASS, MOLAR):
        if unit in scales and target in scales:
            factor = scales[unit] / scales[target]
    if analyte == "TSH" and unit == "µIU/mL" and target == "mIU/L":
        factor = 1
    if analyte == "hematocrit" and unit == "L/L" and target == "%":
        factor = 100
    if factor is None:
        return None
    converted = value * factor
    if not math.isfinite(converted):
        return None
    return float(f"{converted:.12g}"), target, f"scale:{factor:.12g}"


def prepare_observation(payload: dict) -> dict:
    """Attach canonical values but retain the entire original extraction."""
    result = deepcopy(payload)
    result["unitDictionaryVersion"] = DICTIONARY_VERSION
    result.setdefault("revision", 1)
    result.setdefault("review", {})
    for m in result["measurements"]:
        m["issues"] = [i for i in m["issues"] if i != "model_units_unconfirmed"]
        m["normalized_value"] = m["normalized_unit"] = None
        if m["analyte"] in FEATURES and m["value"] is not None:
            converted = convert_value(m["analyte"], m["value"], m["unit"])
            if converted:
                m["normalized_value"], m["normalized_unit"], m["conversion"] = converted
            else:
                m["issues"].append("unit_conversion_unavailable")
    return result


def draft_inputs(observation: dict) -> dict:
    """Do not choose repeated measurements or replace censored values."""
    result = {key: None for key in FEATURES}
    counts = Counter(m["analyte"] for m in observation["measurements"] if m["analyte"] in LAB_KEYS)
    for m in observation["measurements"]:
        if (m["analyte"] in LAB_KEYS and counts[m["analyte"]] == 1
                and m["comparator"] is None and m["freshness"] not in {"stale", "future_date"}
                and m["normalized_value"] is not None):
            result[m["analyte"]] = m["normalized_value"]
    return result


def observation_warnings(observation: dict) -> list[str]:
    warnings = ["Проверьте распознанные значения, единицы и даты перед расчётом."]
    measurements = observation["measurements"]
    issues = set(observation["issues"]) | {i for m in measurements for i in m["issues"]}
    if "repeated_analytes_preserved" in issues:
        warnings.append("Повторные измерения сохранены. Для них значение в форме не выбрано автоматически.")
    if "possible_patient_mismatch" in issues:
        warnings.append("В бланках различаются сведения о пациенте. Проверьте принадлежность всего пакета.")
    if any(m["collected_on"] is None for m in measurements):
        warnings.append("У части результатов неизвестна дата забора; их давность определить нельзя.")
    if any(m["source"]["method"] == "macos_ocr" for m in measurements):
        warnings.append("Пакет содержит сканы. OCR может ошибаться в названиях, числах и единицах.")
    if any(m["comparator"] for m in measurements):
        warnings.append("Результаты со знаками < или > сохранены как границы и не подставлены в числовую форму.")
    if any(m["analyte"] is None or m["normalized_value"] is None for m in measurements):
        warnings.append("Часть показателей не сопоставлена с формой или требует уточнения единиц/значений.")
    if any(m["freshness"] in {"stale", "future_date"} for m in measurements):
        warnings.append("Есть устаревшие по заданной политике или будущие даты. Эти результаты не подставлены автоматически.")
    if any(d["issues"] or any(p["issues"] and p["issues"] != ["ocr_requires_review"] for p in d["pages"]) for d in observation["documents"]):
        warnings.append("Некоторые документы/страницы обработаны не полностью. Проверьте исходные PDF.")
    if not observation["freshness_policy"]:
        warnings.append("Клинические сроки актуальности не заданы; дата сама по себе не определяет пригодность анализа.")
    return warnings


def observation_response(observation: dict) -> dict:
    public = {k: v for k, v in observation.items() if not k.startswith("_")}
    inputs = observation.get("review", {}).get("inputs") or draft_inputs(observation)
    return {"observationId": observation["id"], "inputs": inputs,
            "revision": observation["revision"], "warnings": observation_warnings(observation),
            "observation": public}


def validate_inputs(inputs: dict, *, require_core: bool = True) -> dict:
    unexpected = set(inputs) - set(FEATURES)
    if unexpected:
        raise ValueError("Во входах обнаружены неизвестные или целевые поля. Передавайте только поля лабораторного словаря.")
    values = {key: inputs.get(key) for key in FEATURES}
    if values["sex"] is not None and (not isinstance(values["sex"], str) or values["sex"] not in {"F", "M"}):
        raise ValueError("Укажите sex: F или M.")
    if require_core and values["sex"] is None:
        raise ValueError("Укажите sex: F или M.")
    for key in FEATURES:
        if key == "sex":
            continue
        value = values[key]
        if value is not None:
            try:
                valid = type(value) in {int, float} and math.isfinite(value) and value >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"{key}: ожидается конечное неотрицательное число или null.")
    age = values["age_years"]
    if (age is not None and (int(age) != age or not 18 <= age <= 120)) or (require_core and age is None):
        raise ValueError("Укажите целый возраст от 18 до 120 лет.")
    if require_core and values["hemoglobin"] is None:
        raise ValueError("Укажите гемоглобин в г/л.")
    return values
