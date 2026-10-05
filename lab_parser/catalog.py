"""Exact alias matching and conservative unit spelling normalization."""

import json
import re
import unicodedata
from importlib.resources import files


def clean_name(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return re.sub(r"[\W_]+", " ", text).strip()


CATALOG = json.loads(files("lab_parser").joinpath("data/analytes.json").read_text("utf-8"))
ALIASES = {clean_name(a): key for key, aliases in CATALOG["aliases"].items() for a in [key, *aliases]}


def resolve_name(text: str) -> str | None:
    name = clean_name(text)
    if name in ALIASES:
        return ALIASES[name]
    # A parenthesized short name is accepted only if the rest also resolves
    # to that same analyte, avoiding e.g. "glycated hemoglobin (HbA1c)".
    parts = re.split(r"[()]", text)
    found = [ALIASES.get(clean_name(p)) for p in parts if clean_name(p)]
    if found and all(found) and len(set(found)) == 1:
        return found[0]
    return None


_UNITS = {
    "г/л": "g/L", "g/l": "g/L", "г/дл": "g/dL", "g/dl": "g/dL",
    "мг/л": "mg/L", "mg/l": "mg/L", "мг/дл": "mg/dL", "mg/dl": "mg/dL",
    "мкг/л": "µg/L", "ug/l": "µg/L", "µg/l": "µg/L",
    "нг/мл": "ng/mL", "ng/ml": "ng/mL", "пг/мл": "pg/mL", "pg/ml": "pg/mL",
    "пг": "pg", "pg": "pg", "фл": "fL", "fl": "fL",
    "ммоль/л": "mmol/L", "mmol/l": "mmol/L", "мкмоль/л": "µmol/L",
    "µmol/l": "µmol/L", "umol/l": "µmol/L", "пмоль/л": "pmol/L", "pmol/l": "pmol/L",
    "нмоль/л": "nmol/L", "nmol/l": "nmol/L",
    "мккат/л": "µkat/L", "ед/л": "U/L", "ме/л": "IU/L", "u/l": "U/L", "iu/l": "IU/L",
    "мме/л": "mIU/L", "мкме/мл": "µIU/mL", "miu/l": "mIU/L", "µiu/ml": "µIU/mL",
    "мм/ч": "mm/h", "mm/h": "mm/h", "%": "%", "л/л": "L/L", "l/l": "L/L",
    "10^9/л": "10^9/L", "10^9/l": "10^9/L", "10^12/л": "10^12/L", "10^12/l": "10^12/L",
    "мл/мин/1.73м2": "mL/min/1.73m²", "ml/min/1.73m2": "mL/min/1.73m²",
}


def normalize_unit(text: str | None) -> str | None:
    if not text:
        return None
    key = text.casefold().replace("μ", "µ").replace("³", "^3").replace("⁹", "^9").replace("¹²", "^12")
    key = unicodedata.normalize("NFKC", key)
    key = key.replace("μ", "µ").replace(",", ".").replace("м^2", "м2").replace("m^2", "m2")
    key = re.sub(r"\s+", "", key).replace("*", "").replace("×", "").replace("х10", "10").replace("x10", "10")
    return _UNITS.get(key)
