"""Audit documented units; preserve raw files and do not create clinical labels."""

from pathlib import Path
import hashlib
import json

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "external"
REFERENCE = ROOT / "data" / "reference" / "variables.xlsx"
CASE = Path("data/case/deficiency_anemia.csv")
CDC = "https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/{year}/DataFiles/{table}.htm"
WILEY = "https://onlinelibrary.wiley.com/doi/10.1111/exsy.13528"
MENDELEY = "https://data.mendeley.com/datasets/dt89jydgnv/1"

variables = pd.read_excel(REFERENCE, sheet_name="Variables")
case = pd.read_csv(CASE)
assert variables.variable.is_unique
assert list(variables.variable) == list(case.columns), "Dictionary must match all CSV columns in order"
TARGETS = {
    "anemia", "iron_deficiency", "B12_deficiency", "folate_deficiency", "B6_deficiency",
    "copper_deficiency", "inflammation_anemia", "mixed_deficiency", "anemia_class", "deficiency_cause",
}
units = {row.variable: str(row.unit) if row.unit != "—" else None for row in variables.itertuples()}
dictionary = {
    "schema_version": "1.0", "checked_on": "2026-10-03",
    "source": {"path": "data/reference/variables.xlsx", "sheet": "Variables",
               "sha256": hashlib.sha256(REFERENCE.read_bytes()).hexdigest()},
    "case_source": {"path": str(CASE), "sha256": hashlib.sha256(CASE.read_bytes()).hexdigest()},
    "units_status": "confirmed_by_user_supplied_dictionary",
    "notes": "Unit metadata does not establish reference ranges, diagnostic criteria or assay equivalence.",
    "columns": [],
}
for index, row in enumerate(variables.itertuples(index=False), start=2):
    role = "identifier" if row.variable == "patient_id" else "target" if row.variable in TARGETS else "feature"
    numeric = pd.api.types.is_numeric_dtype(case[row.variable])
    entry = {"name": row.variable, "description": row.description, "unit": units[row.variable],
             "source_unit_literal": row.unit, "role": role,
             "source_range": f"Variables!A{index}:C{index}",
             "non_missing": int(case[row.variable].notna().sum())}
    if numeric:
        entry.update(min=float(case[row.variable].min()), median=float(case[row.variable].median()),
                     max=float(case[row.variable].max()))
    else:
        entry["values"] = sorted(case[row.variable].dropna().unique().tolist()) if role != "identifier" else None
    dictionary["columns"].append(entry)
(ROOT / "data" / "feature_dictionary.json").write_text(json.dumps(dictionary, ensure_ascii=False, indent=2) + "\n")
INPUTS = [c["name"] for c in dictionary["columns"] if c["role"] == "feature"]


def mapping(target, table, column, unit, year, factor=1.0, notes="", status="documented"):
    return {"target": target, "target_unit": units[target], "table": table, "column": column,
            "source_unit": unit, "factor_to_case": factor, "offset_to_case": 0.0,
            "unit_status": status, "unit_conversion_allowed": status == "documented",
            "source_url": CDC.format(year=year, table=table), "notes": notes}


def nhanes(year, prefix, folder):
    cbc = "L25_C" if prefix == "C" else "CBC_G"
    bio = "L40_C" if prefix == "C" else "BIOPRO_G"
    result = [
        mapping("age_years", f"DEMO_{prefix}", "RIDAGEYR", "years", year,
                notes=f"Top-coded at {85 if prefix == 'C' else 80}; these ages are not exact."),
        {**mapping("sex", f"DEMO_{prefix}", "RIAGENDR", None, year),
         "factor_to_case": None, "offset_to_case": None, "categorical_mapping": {"1": "M", "2": "F"}},
    ]
    for target, column, unit, factor in [
        ("hemoglobin", "LBXHGB", "g/dL", 10), ("RBC", "LBXRBCSI", "million cells/µL", 1),
        ("hematocrit", "LBXHCT", "%", 1), ("MCV", "LBXMCVSI", "fL", 1),
        ("MCH", "LBXMCHSI", "pg", 1), ("MCHC", "LBXMC", "g/dL", 10),
        ("RDW", "LBXRDW", "%", 1), ("platelets", "LBXPLTSI", "1000 cells/µL", 1),
        ("WBC", "LBXWBCSI", "1000 cells/µL", 1),
    ]:
        result.append(mapping(target, cbc, column, unit, year, factor))
    for target, column, unit in [("creatinine", "LBDSCRSI", "µmol/L"),
                                 ("albumin", "LBDSALSI", "g/L"), ("LDH", "LBXSLDSI", "U/L")]:
        result.append(mapping(target, bio, column, unit, year))
    if prefix == "C":
        for target, table, column, unit, factor, note in [
            ("ferritin", "L06TFR_C", "LBDFERSI", "µg/L", 1, "LBDFER ng/mL is numerically equivalent; subsample and assay constraints apply."),
            ("sTfR", "L06TFR_C", "LBXTFR", "mg/L", 1, "Assay reference ranges may differ despite identical units."),
            ("serum_iron", "L40FE_C", "LBDIRNSI", "µmol/L", 1, "Frozen-serum iron; refrigerated BIOPRO iron is a separate measurement, not an automatic fallback."),
            ("TIBC", "L40FE_C", "LBDTIBSI", "µmol/L", 1, "Same panel as frozen-serum iron."),
            ("TSAT", "L40FE_C", "LBDPCT", "%", 1, "Same frozen-serum panel."),
            ("vitamin_B12", "L06NB_C", "LBXB12", "pg/mL", 1, "Bio-Rad assay; unit identity does not remove between-cycle assay differences."),
            ("folate", "L06NB_C", "LBXFOL", "ng/mL", 1, "Serum folate only; not RBC folate."),
            ("MMA", "L06MH_C", "LBXMMA", "µmol/L", 1, "Plasma MMA; retain specimen and method."),
            ("homocysteine", "L06MH_C", "LBXHCY", "µmol/L", 1, "Retain specimen and method."),
            ("vitamin_B6", "L43_C", "LBXVB6", "nmol/L", 1, "Same unit/PLP analyte, but low-range assay incompatibility prevents automatic pooling with later HPLC."),
            ("CRP", "L11_C", "LBXCRP", "mg/dL", 10, "Case unit is mg/L; do not retain mg/dL numbers."),
        ]:
            result.append(mapping(target, table, column, unit, year, factor, note))
    else:
        result.extend([
            mapping("vitamin_B12", "VITB12_G", "LBXB12", "pg/mL", year,
                    notes="Roche assay; not an assay-calibration correction."),
            mapping("MMA", "MMA_G", "LBXMMASI", "nmol/L", year, .001,
                    "Serum MMA; keep LBDMMALC censoring/comment code."),
            mapping("copper", "CUSEZN_G", "LBDSCUSI", "µmol/L", year),
            mapping("serum_iron", bio, "LBDSIRSI", "µmol/L", year,
                    notes="Refrigerated-serum measurement; no TIBC/TSAT in this downloaded panel."),
        ])
    present = {p.stem: set(pd.read_sas(p, format="xport").columns) for p in folder.glob("*.xpt")}
    assert len({r["target"] for r in result}) == len(result)
    for r in result:
        assert r["column"] in present[r["table"]]
    return {"entries": result, "absent_direct_features": [key for key in INPUTS if key not in {r["target"] for r in result}],
            "training_ready": False,
            "derived_candidates": [{"target": "UIBC", "expression": "TIBC - serum_iron",
                                    "requires": "Paired same-specimen iron and TIBC; derivation recorded separately.",
                                    "enabled": False}] if prefix == "C" else [],
            "forbidden_substitutions": {"indirect_bilirubin": "Total bilirubin LBXSTB/LBDSTBSI is not indirect bilirubin."}}


sources = {
    "nhanes_2003_2004": nhanes(2003, "C", BASE / "raw" / "nhanes_2003_2004"),
    "nhanes_2011_2012": nhanes(2011, "G", BASE / "raw" / "nhanes_2011_2012"),
}
kcolumns = {"sex": "GENDER", "hemoglobin": "HGB", "RBC": "RBC", "hematocrit": "HCT", "MCV": "MCV",
            "MCH": "MCH", "MCHC": "MCHC", "RDW": "RDW", "platelets": "PLT", "WBC": "WBC",
            "serum_iron": "SD", "TIBC": "SDTSD", "TSAT": "TSD", "ferritin": "FERRITTE",
            "folate": "FOLATE", "vitamin_B12": "B12"}
known = {"hemoglobin": ("g/dL", 10), "hematocrit": ("%", 1), "MCV": ("fL", 1), "MCH": ("pg", 1),
         "MCHC": ("g/dL", 10), "RDW": ("%", 1), "platelets": ("K/µL", 1),
         "serum_iron": ("µg/dL", .1791), "ferritin": ("ng/mL", 1), "folate": ("ng/mL", 1)}
conflicts = {
    "RBC": "Paper reports 10^3/mL for RBC, contradicting expected CBC scale; no automatic reinterpretation.",
    "WBC": "Paper reports 10^3/mL, not 10^3/µL; apparent scale typo needs original-author confirmation.",
    "vitamin_B12": "Paper reports ng/mL; values suggest pg/mL, but range inference is not proof. Do not apply x1000 or identity automatically.",
    "TIBC": "SDTSD has µg/dL in paper but an empty analyte description; candidate TIBC requires confirmation.",
    "TSAT": "TSD is called total serum iron in µg/dL, while file exactly equals 100*SD/SDTSD; candidate percentage requires confirmation.",
    "sex": "0/1 female/male inferred from population counts and Hb rule; retain provenance pending confirmation.",
}
reported_conflicting_units = {"RBC": "10^3/mL", "WBC": "10^3/mL", "vitamin_B12": "ng/mL",
                              "TIBC": "µg/dL", "TSAT": "µg/dL", "sex": "0/1"}
kentries = []
for target, column in kcolumns.items():
    unit, factor = known.get(target, (None, None))
    kentries.append({"target": target, "target_unit": units[target], "column": column,
                     "source_unit": unit, "reported_unit_literal": reported_conflicting_units.get(target, unit),
                     "factor_to_case": factor, "offset_to_case": 0 if factor is not None else None,
                     "unit_status": "documented_in_followup_paper" if target in known else "conflicting_or_unconfirmed",
                     "unit_conversion_allowed": target in known, "source_url": WILEY,
                     "notes": conflicts.get(target, "Table 1 is a follow-up study on the same dataset; dataset itself has no unit-bearing headers. Conversion is not approval for training.")})
sources["kilicarslan"] = {"entries": kentries, "training_ready": False,
                          "license": "CC BY 4.0 for byte-identical author Mendeley version 1",
                          "license_source": MENDELEY,
                          "absent_direct_features": [key for key in INPUTS if key not in kcolumns]}

# Other downloads have insufficient trustworthy unit metadata for automatic conversion.
for source, filename, columns in [
    ("hepcidin_hs", BASE / "raw/hepcidin_hs/source.xlsx",
     {"age_years": "Age", "sex": "Gender", "hemoglobin": "Hb", "MCV": "MCV", "platelets": "PLT",
      "serum_iron": "Fe", "ferritin": "Ferritin", "CRP": "CRP", "ESR": "ESR", "TSAT": "Tsat", "transferrin": "Transferrin"}),
    ("expert_anemia", BASE / "raw/expert_anemia/source.csv",
     {"age_years": "Age", "sex": "Sex", "RBC": "RBC", "hematocrit": "PCV", "MCV": "MCV", "MCH": "MCH",
      "MCHC": "MCHC", "RDW": "RDW", "WBC": "TLC", "platelets": "PLT /mm3", "hemoglobin": "HGB"}),
    ("cbc_original", BASE / "raw/cbc_original/source.csv",
     {"age_years": "Age", "sex": "Sex", "RBC": "RBC", "hematocrit": "PCV", "MCV": "MCV", "MCH": "MCH",
      "MCHC": "MCHC", "RDW": "RDW", "WBC": "TLC", "platelets": "PLT /mm3", "hemoglobin": "HGB"}),
    ("anemia_types", BASE / "review_only/anemia_types/diagnosed_cbc_data_v4.csv",
     {"WBC": "WBC", "RBC": "RBC", "hemoglobin": "HGB", "hematocrit": "HCT", "MCV": "MCV",
      "MCH": "MCH", "MCHC": "MCHC", "platelets": "PLT"}),
]:
    data = pd.read_excel(filename) if filename.suffix == ".xlsx" else pd.read_csv(filename, skiprows=[1] if source == "cbc_original" else None)
    data.columns = data.columns.str.strip()
    entries = []
    for target, column in columns.items():
        assert column in data
        entries.append({"target": target, "target_unit": units[target], "column": column,
                        "source_unit": None, "factor_to_case": None, "unit_status": "not_verified",
                        "unit_conversion_allowed": False,
                        "notes": "Column match is a candidate; numeric ranges alone do not confirm units or analyte equivalence."})
    sources[source] = {"entries": entries, "training_ready": False,
                       "absent_direct_features": [key for key in INPUTS if key not in columns]}

unit_mapping = {"schema_version": "1.0", "checked_on": "2026-10-03",
                "target_dictionary": "data/feature_dictionary.json",
                "scope": "Unit comparison of downloaded components; does not generate diagnoses or pool assay methods.",
                "sources": sources}
(BASE / "unit_mapping.json").write_text(json.dumps(unit_mapping, ensure_ascii=False, indent=2) + "\n")


def comparison_cell(source, target):
    entry = next((r for r in sources[source]["entries"] if r["target"] == target), None)
    if not entry:
        return "Нет в скачанном поднаборе"
    if not entry["unit_conversion_allowed"]:
        return f"`{entry['column']}`: не подтверждено"
    if target == "sex":
        return "`RIAGENDR`: 1→M, 2→F"
    factor = entry["factor_to_case"]
    operation = "без пересчёта" if factor == 1 else f"×{factor:g}"
    return f"`{entry['column']}`: {entry['source_unit']}, {operation}"


def table_value(value):
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


lines = [
    "# Признаки и единицы измерения", "", "Проверено: 2026-10-03.", "",
    "Источник единиц кейса — предоставленный пользователем `variables.xlsx`, лист `Variables`. Локальная неизменённая копия: `data/reference/variables.xlsx`. Все 48 названий и порядок столбцов совпадают с исходным CSV. Единицы установлены по документу, а не по диапазонам значений.", "",
    "Машиночитаемый словарь: `data/feature_dictionary.json`; полное сравнение скачанных источников: `data/external/unit_mapping.json`. В каждой записи сохранены источник и статус проверки. Исходные значения и столбцы CSV не изменялись. Статистика ниже — описательная проверка, не клинические референсы.", "",
    "## Все столбцы исходного CSV", "",
    "37 входных признаков: возраст, пол и 35 лабораторных показателей. Остальные столбцы — идентификатор и 10 целей; их нельзя подавать как признаки.", "",
    "| Столбец | Значение | Единица | Роль | Непустых | Минимум — медиана — максимум |",
    "|---|---|---|---|---:|---|",
]
for c in dictionary["columns"]:
    role = {"feature": "признак", "target": "цель", "identifier": "ID"}[c["role"]]
    interval = " — ".join(table_value(c[k]) for k in ["min", "median", "max"]) if "min" in c else "—"
    lines.append(f"| `{c['name']}` | {c['description']} | {c['unit'] or 'не применяется'} | {role} | {c['non_missing']} | {interval} |")
lines.extend([
    "", "`sex` кодируется F/M; бинарные цели — 0/1; `anemia_class` и `deficiency_cause` — категории. Для RDW единица % означает ширину в относительном выражении; RDW-SD в fL не является тем же признаком. `vitamin_B6` — PLP, `active_B12` — холотранскобаламин, `folate` — сывороточный фолат, `indirect_bilirubin` — непрямой билирубин. `eGFR` нормирована на площадь тела 1,73 м²; ненормированную СКФ в mL/min нельзя подставлять напрямую.", "",
    "## Сравнение с NHANES и Kılıçarslan", "",
    "Таблица относится к реально скачанным компонентам. «Нет» не означает, что показатель отсутствует во всех циклах NHANES. Указанная операция переводит внешний результат в единицы кейса. «Без пересчёта» включает численно эквивалентные формы единиц.", "",
    "| Признак | Единица кейса | NHANES 2003–2004 | NHANES 2011–2012 | Kılıçarslan |",
    "|---|---|---|---|---|",
])
for target in INPUTS:
    lines.append(f"| `{target}` | {units[target] or 'категория'} | {comparison_cell('nhanes_2003_2004', target)} | {comparison_cell('nhanes_2011_2012', target)} | {comparison_cell('kilicarslan', target)} |")
lines.extend([
    "", "### Существенные различия и ограничения", "",
    "- Hb и MCHC: g/dL → g/L, ×10. CRP 2003–2004: mg/dL → mg/L, ×10. MMA 2011–2012: nmol/L → µmol/L, ×0,001; MMA 2003–2004 уже в µmol/L.",
    "- 1 ng/mL ферритина = 1 µg/L; 1 million cells/µL эритроцитов = 1×10¹²/L; 1 thousand cells/µL лейкоцитов/тромбоцитов = 1×10⁹/L. Это равные численные значения, не необходимость домножать на 1000.",
    "- Для B12 выбирать LBXB12 (pg/mL), для сывороточного фолата — LBXFOL (ng/mL). Альтернативы LBDB12SI (pmol/L) и LBDFOLSI (nmol/L) требуют деления на 0,738 и 2,265 соответственно. Не использовать RBC-фолаты вместо сывороточных.",
    "- Для железа/TIBC предпочтительны готовые SI-колонки; для меди — LBDSCUSI, для креатинина — LBDSCRSI, для альбумина — LBDSALSI. Если выбран другой столбец, сначала проверить его единицу. URXUCR — креатинин мочи, не аналог сывороточного creatinine.",
    "- UIBC не опубликован отдельной колонкой выбранного NHANES-компонента; TIBC−iron — кандидат производного признака только для согласованной пары измерений. Расчёт eGFR потребует отдельного решения о формуле; готовая метка в скачанном BIOPRO отсутствует.",
    "- Одинаковые единицы не подтверждают одинаковую методику и клиническую сопоставимость. Особенно важны методы B6, B12 и sTfR. Общий билирубин нельзя перенести в непрямой, общий B12 — в активный.", "",
    "### Kılıçarslan: противоречия документации", "",
    f"[Таблица 1 исследования на том же наборе]({WILEY}) подтверждает g/dL для Hb/MCHC и часть остальных единиц. Численные границы таблицы совпадают с нашим файлом. Но авторы указывают B12 в ng/mL, WBC/RBC в 10³/mL и TSD как железо в µg/dL. Диапазоны B12 похожи на pg/mL, а в файле TSD=100×SD/SDTSD. Эти наблюдения указывают на возможные ошибки описания, но не дают права самостоятельно считать единицы исправленными. RBC, WBC, B12, TIBC/TSAT и кодировка пола оставлены для уточнения. Коэффициент 0,1791 для SD µg/dL → µmol/L относится к железу, не к меди и другим веществам.", "",
    f"Дополнительно найден [авторский источник Mendeley]({MENDELEY}), DOI 10.17632/dt89jydgnv.1, CC BY 4.0. SHA-256 в API автора совпадает со скачанным XLSX: `e6f7dfd4e359911134a55525f133775e3f9b6d773988a0fd550bed0ea9466f3c`. Прежняя неопределённость лицензии Kaggle разрешена для этой авторской версии, но не неопределённость единиц/меток.", "",
    "### Другие скачанные источники", "",
    "Для Hepcidin/HS, Expert Anemia, исходного CBC и Anemia Types точные единицы всех совпадающих полей пока не подтверждены по надёжной документации. В unit_mapping.json записаны возможные соответствия колонок, но коэффициенты неизвестны и автоматическое преобразование выключено. У HS Fe порядка десятков и transferrin порядка сотен отличаются по масштабу от единиц кейса; это причина проверить µg/dL и mg/dL, а не готовое подтверждение этих единиц. Диапазоны и заголовок PLT /mm3 тоже не гарантируют отсутствие предварительного масштабирования.", "",
    "## Источники единиц внешних данных", "",
])
urls = sorted({r["source_url"] for key, source in sources.items() if key.startswith("nhanes") for r in source["entries"]})
for url in urls:
    lines.append(f"- [{url.rsplit('/', 1)[-1]}]({url})")
lines.extend(["", "Скрипт воспроизводимой сверки: `scripts/audit_feature_units.py`. Словарь единиц уже можно использовать для подписей полей и проверки импортов. Для Kılıçarslan созданы исследовательские копии: 10 документированных показателей и 17 признаков с явными предположениями; метод и ограничения — [KILICARSLAN_ANALYSIS.md](KILICARSLAN_ANALYSIS.md), скрипт — `scripts/prepare_kilicarslan.py`. Копии не являются медицински утверждённой обучающей выборкой. Подключение преобразований к PDF-парсеру пока не выполнено.", ""])
(ROOT / "FEATURE_UNITS.md").write_text("\n".join(lines))
print(json.dumps({"case_columns": len(dictionary["columns"]), "features": len(INPUTS),
                  "sources": {key: {"candidate_features": len(source["entries"]),
                                    "documented_unit_entries": sum(r["unit_conversion_allowed"] for r in source["entries"])}
                              for key, source in sources.items()}}, ensure_ascii=False, indent=2))
