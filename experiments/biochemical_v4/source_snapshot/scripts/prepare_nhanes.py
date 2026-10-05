"""Normalize downloaded NHANES components using audited unit metadata, without diagnoses."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/external"


def convert(series, rule):
    if not rule["unit_conversion_allowed"] or rule["unit_status"] != "documented":
        raise ValueError("Only documented mappings may be imported")
    if "categorical_mapping" in rule:
        mapping = {int(k): v for k, v in rule["categorical_mapping"].items()}
        if not set(series.dropna().unique()) <= set(mapping):
            raise ValueError("Unknown categorical code")
        return series.map(mapping)
    return pd.to_numeric(series, errors="raise") * rule["factor_to_case"] + rule["offset_to_case"]


def run(output=BASE / "processed/nhanes_case_units_v1"):
    if output.exists():
        raise ValueError("Use a new output directory, preserving previous derivations")
    dictionary = json.loads((ROOT / "data/feature_dictionary.json").read_text())
    mappings = json.loads((BASE / "unit_mapping.json").read_text())
    features = [r["name"] for r in dictionary["columns"] if r["role"] == "feature"]
    targets = [r["name"] for r in dictionary["columns"] if r["role"] == "target"]
    output.mkdir(parents=True)
    manifest = {"schema_version": "1.0", "normalization_version": "nhanes-case-units-1", "diagnosis_labels_created": False,
                "clinical_calibration_ready": False, "survey_weighting_applied": False, "source_sha256": {},
                "mapping_sha256": hashlib.sha256((BASE / "unit_mapping.json").read_bytes()).hexdigest(),
                "dictionary_sha256": dictionary["source"]["sha256"], "cycles": {},
                "excluded_from_automatic_training": {"vitamin_B6_2003_2004": "CDC low-range method incompatibility", "all_deficiency_and_multiclass_labels": "Not observed in downloaded sources"}}
    for cycle, suffix in [("nhanes_2003_2004", "C"), ("nhanes_2011_2012", "G")]:
        folder = BASE / "raw" / cycle
        paths = sorted(folder.glob("*.xpt"))
        tables = {p.stem: pd.read_sas(p, format="xport") for p in paths}
        demo = tables[f"DEMO_{suffix}"].set_index("SEQN", verify_integrity=True)
        index = demo.index[demo.RIDAGEYR.ge(18) & ~demo.RIDEXPRG.eq(1)]
        result = pd.DataFrame(np.nan, index=index, columns=features)
        result["sex"] = pd.Series(index=index, dtype=object)
        source_mapping = mappings["sources"][cycle]
        for rule in source_mapping["entries"]:
            table = tables[rule["table"]].set_index("SEQN", verify_integrity=True)
            result[rule["target"]] = convert(table[rule["column"]].reindex(index), rule)
        ids = pd.Series([f"{cycle}_SEQN_{int(i)}" for i in index], index=index)
        result.insert(0, "patient_id", ids)
        result.insert(1, "cycle", cycle)
        result.insert(2, "SEQN", index.astype(int))
        result.to_csv(output / f"{cycle}_features.csv", index=False)
        labels = pd.DataFrame(np.nan, index=index, columns=targets)
        labels.insert(0, "patient_id", ids)
        # Rule-derived screening status is explicitly separate from an observed diagnosis.
        known = result.hemoglobin.notna() & result.sex.notna()
        labels["anemia_by_case_rule"] = np.where(known, (result.hemoglobin < np.where(result.sex.eq("F"), 120., 130.)).astype(float), np.nan)
        labels.to_csv(output / f"{cycle}_unknown_labels.csv", index=False)
        mask = pd.DataFrame({"patient_id": ids})
        for target in targets:
            mask[f"known_{target}"] = 0
        mask.to_csv(output / f"{cycle}_target_masks.csv", index=False)
        metadata = pd.DataFrame({"patient_id": ids, "pregnancy_code": demo.RIDEXPRG.reindex(index),
                                 "pregnancy_unknown": demo.RIDEXPRG.reindex(index).isna(),
                                 "age_top_coded": demo.RIDAGEYR.reindex(index).ge(85 if suffix == "C" else 80)})
        # Preserve available sampling/design weights and LOD/comment codes per component.
        for name, table in tables.items():
            table = table.set_index("SEQN", verify_integrity=True)
            for column in table:
                if column.startswith(("WT", "SDMV")) or column.endswith("LC"):
                    metadata[f"{name}__{column}"] = table[column].reindex(index)
        metadata.to_csv(output / f"{cycle}_metadata.csv", index=False)
        manifest["source_sha256"].update({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
        manifest["cycles"][cycle] = {"rows": len(result), "verified_feature_count": len(source_mapping["entries"]),
                                      "present_counts": {f: int(result[f].notna().sum()) for f in features},
                                      "unknown_target_cells": len(result)*len(targets), "pregnancy_unknown": int(metadata.pregnancy_unknown.sum()),
                                      "anemia_rule_available": int(known.sum()), "anemia_rule_positive": int(labels.anemia_by_case_rule.eq(1).sum()),
                                      "source_mapping": source_mapping, "negative_feature_cells": int((result[features].select_dtypes(include="number") < 0).sum().sum())}
    manifest["outputs_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob("*.csv")}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    (output / "README.md").write_text("""# NHANES в единицах кейса

Производные из уже скачанных официальных public-use CDC/NCHS файлов. Исходники не изменены.
Скрипт `scripts/prepare_nhanes.py`, правила `data/external/unit_mapping.json`, контрольные суммы в manifest.json.

Взрослые ≥18 лет, исключена известная беременность; неизвестная отмечена отдельно.
SEQN соединяется один-к-одному внутри цикла. ID включает цикл. Возраст 80/85+ отмечен как ограниченный сверху.
Hb/MCHC: г/дл ×10 → г/л; CRP 2003: мг/дл ×10 → мг/л; MMA 2011: нмоль/л ×0.001 → мкмоль/л.
Другие преобразования документированы в manifest.json; общий билирубин не заменяет непрямой.
Циклы и методы сохранены отдельно. B6 2003–2004 нельзя автоматически объединять с поздними методами.

Все 10 целей кейса неизвестны: пустые ячейки и маски 0. `anemia_by_case_rule` — вычисленный скрининговый статус, отдельный от целей.
Нет меток причины анемии, меди, B6, отсутствия дефицитов или смешанных состояний.
Эти данные готовы для анализа распределений/пропусков, но не для клинической калибровки вероятностей.
Веса выборки, страты/PSU и коды предела обнаружения сохранены по компонентам; популяционное взвешивание не применялось.
Числа в manifest.json — невзвешенные количества, не распространённость заболеваний.

Источник: CDC/NCHS NHANES 2003–2004 и 2011–2012. Ссылки на документацию каждого компонента включены в manifest.json.
Условия public-use: https://www.cdc.gov/nchs/policy/data-user-agreement.html.
""", encoding="utf-8")
    print(json.dumps({k: {"rows": v["rows"], "features": v["verified_feature_count"]} for k,v in manifest["cycles"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    run()
