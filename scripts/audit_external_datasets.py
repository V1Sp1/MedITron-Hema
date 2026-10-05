"""Read downloaded sources; write aggregate audit and a hash manifest, never training labels."""

from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "external"


def anemia_rule(hgb_g_dl, female):
    return hgb_g_dl.lt(np.where(female, 12.0, 13.0))


def nhanes(folder, prefix):
    tables = {p.stem: pd.read_sas(p, format="xport") for p in sorted(folder.glob("*.xpt"))}
    d = tables[f"DEMO_{prefix}"].copy()
    for name, table in tables.items():
        if name != f"DEMO_{prefix}":
            d = d.merge(table, on="SEQN", how="left", validate="one_to_one")
    adults = d.loc[d.RIDAGEYR.ge(18)].copy()
    adults = adults.loc[~adults.RIDEXPRG.eq(1)].copy()
    hb = adults.LBXHGB.notna()
    variables = {
        "hemoglobin": "LBXHGB", "ferritin": "LBDFER", "B12": "LBXB12",
        "serum_folate": "LBXFOL", "MMA": "LBXMMA" if prefix == "C" else "LBXMMASI",
        "B6": "LBXVB6", "copper": "LBDSCUSI", "CRP": "LBXCRP",
        "sTfR": "LBXTFR", "TIBC": "LBDTIBSI", "TSAT": "LBDPCT",
        "creatinine": "LBDSCRSI",
    }
    present = {name: int(adults[var].notna().sum()) if var in adults else None
               for name, var in variables.items()}
    panels = {}
    for name, cols in {
        "Hb_B12_MMA": ["LBXHGB", "LBXB12", variables["MMA"]],
        "Hb_ferritin_B12_folate_CRP": ["LBXHGB", "LBDFER", "LBXB12", "LBXFOL", "LBXCRP"],
        "Hb_B12_MMA_copper": ["LBXHGB", "LBXB12", variables["MMA"], "LBDSCUSI"],
    }.items():
        panels[name] = int(adults[cols].notna().all(axis=1).sum()) if set(cols) <= set(adults) else None
    return {
        "raw_tables": {name: {"rows": len(t), "columns": list(t.columns)} for name, t in tables.items()},
        "adults_18plus_excluding_known_pregnancy": len(adults),
        "with_hemoglobin": int(hb.sum()),
        "anemia_by_case_rule": int(anemia_rule(adults.loc[hb, "LBXHGB"], adults.loc[hb, "RIAGENDR"].eq(2)).sum()),
        "women_18to44_pregnancy_unknown": int((adults.RIAGENDR.eq(2) & adults.RIDAGEYR.between(18, 44) & ~adults.RIDEXPRG.eq(2)).sum()),
        "biomarker_counts_in_filtered_adults": present,
        "complete_panel_counts_in_filtered_adults": panels,
        "notes": "Unweighted counts; pregnancy unknown retained and flagged. No deficiency/class labels created. Null counts mean component absent from this download, not a negative result.",
    }


result = {"checked_on": "2026-10-03", "scope": "source audit only; no training-ready labels"}
result["nhanes_2003_2004"] = nhanes(BASE / "raw" / "nhanes_2003_2004", "C")
result["nhanes_2011_2012"] = nhanes(BASE / "raw" / "nhanes_2011_2012", "G")

k = pd.read_excel(BASE / "review_only" / "kilicarslan" / "SKILICARSLAN_Anemia_DataSet.xlsx")
lab = k.loc[k.All_Class.eq(0)]
female = k.GENDER.eq(0)
ar = anemia_rule(k.HGB, female)
result["kilicarslan"] = {
    "rows": len(k), "columns": list(k.columns), "classes": k.All_Class.value_counts().to_dict(),
    "duplicates_with_labels": int(k.duplicated().sum()), "missing_cells": int(k.isna().sum().sum()),
    "anemia_rule_vs_class0_disagreement": int(ar.ne(k.All_Class.ne(0)).sum()),
    "class0_low_biomarker_counts_for_review_only": {
        "ferritin_lt15": int(lab.FERRITTE.lt(15).sum()),
        "B12_lt180": int(lab.B12.lt(180).sum()),
        "folate_lt3": int(lab.FOLATE.lt(3).sum()),
    },
    "TSAT_column_identity_max_absolute_error": float((k.TSD - 100*k.SD/k.SDTSD).abs().max()),
    "notes": "Low-biomarker counts are review signals, not diagnoses or proof of units. Labels are one-hot classes, not independent negative deficiency labels. Author Mendeley CC BY 4.0 version has identical declared SHA-256; conflicting units and diagnostic criteria still need review.",
}

e = pd.read_csv(BASE / "raw" / "expert_anemia" / "source.csv")
old = pd.read_csv(BASE / "raw" / "cbc_original" / "source.csv", skiprows=[1])
old.columns = old.columns.str.strip()
old = old.loc[old["S. No."].notna()].copy()
old["Sex"] = old.Sex.map({0: "Female", 1: "Male"})
common = [c for c in old.columns if c in e]
old = old.sort_values("S. No.").reset_index(drop=True)
ordered_e = e.sort_values("S. No.").reset_index(drop=True)
matching = pd.Series(True, index=ordered_e.index)
diffs = {}
for c in common:
    if c == "Sex":
        match = ordered_e[c].eq(old[c])
    else:
        match = pd.Series(np.isclose(ordered_e[c], old[c], rtol=0, atol=1e-9, equal_nan=True))
    matching &= match
    diffs[c] = int((~match).sum())
rule = anemia_rule(e.HGB, e.Sex.eq("Female"))
result["expert_anemia"] = {
    "rows": len(e), "columns": list(e.columns), "classes": e.Anemia.value_counts().to_dict(),
    "age_min": int(e.Age.min()), "age_max": int(e.Age.max()), "adults_18plus": int(e.Age.ge(18).sum()),
    "case_rule_disagreement_all_ages": int(rule.ne(e.Anemia.eq("Anemic")).sum()),
    "case_rule_disagreement_adults": int((rule.ne(e.Anemia.eq("Anemic")) & e.Age.ge(18)).sum()),
    "original_cbc_rows": len(old), "original_cbc_columns": list(old.columns),
    "matching_original_rows_all_common_columns": int(matching.sum()),
    "differences_vs_original_by_column": diffs,
    "notes": "Do not treat expert_anemia and cbc_original as independent cohorts. Source provenance differs; matching is an observed file comparison, not a conclusion about authorship.",
}

h = pd.read_excel(BASE / "raw" / "hepcidin_hs" / "source.xlsx")
result["hepcidin_hs"] = {
    "rows": len(h), "columns": list(h.columns), "anemia_label_counts": h.Anemic.value_counts().to_dict(),
    "age_min": int(h.Age.min()), "age_max": int(h.Age.max()), "adults_18plus": int(h.Age.ge(18).sum()),
    "unique_sample_ids": int(h.Sample.nunique()),
    "missing_by_column": h.isna().sum().to_dict(),
    "notes": "Anemic binary label exists; no per-row IDA/ACD/combined etiology label in downloaded spreadsheet. Gender coding and units need confirmation before import.",
}

t = pd.read_csv(BASE / "review_only" / "anemia_types" / "diagnosed_cbc_data_v4.csv")
result["anemia_types"] = {
    "rows": len(t), "columns": list(t.columns), "classes": t.Diagnosis.value_counts().to_dict(),
    "duplicates_with_labels": int(t.duplicated().sum()), "missing_cells": int(t.isna().sum().sum()),
    "hemoglobin_min_g_dl_as_reported": float(t.HGB.min()),
    "hemoglobin_max_g_dl_as_reported": float(t.HGB.max()),
    "negative_hemoglobin_rows": int(t.HGB.lt(0).sum()),
    "notes": "No age/sex or confirmatory nutrient assays; morphology labels cannot be converted to etiologic classes directly.",
}

(BASE / "source_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

urls = {}
for folder, year in [("nhanes_2003_2004", 2003), ("nhanes_2011_2012", 2011)]:
    for p in (BASE / "raw" / folder).glob("*.xpt"):
        urls[str(p.relative_to(BASE))] = f"https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/{year}/DataFiles/{p.name}"
urls.update({
    "raw/hepcidin_hs/source.xlsx": "https://data.mendeley.com/public-files/datasets/9x4n84cxxw/files/ba12e77b-2e9d-4a73-a279-c8c6fb2ab365/file_downloaded",
    "raw/expert_anemia/source.csv": "https://data.mendeley.com/public-files/datasets/8sycpbhzn7/files/ee6a4bf8-f81c-4b4c-b179-49f62e739aaa/file_downloaded",
    "raw/cbc_original/source.csv": "https://data.mendeley.com/public-files/datasets/dy9mfjchm7/files/eb5f6789-afd7-4e76-a828-ec624e185026/file_downloaded",
    "review_only/kilicarslan/SKILICARSLAN_Anemia_DataSet.xlsx": "https://www.kaggle.com/api/v1/datasets/download/serhathoca/anemia-disease?datasetVersionNumber=2",
    "review_only/anemia_types/diagnosed_cbc_data_v4.csv": "https://www.kaggle.com/api/v1/datasets/download/ehababoelnaga/anemia-types-classification",
})
manifest = []
for path, url in sorted(urls.items()):
    p = BASE / path
    entry = {"path": path, "source_url": url, "downloaded_on": "2026-10-03",
             "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    if "kilicarslan" in path:
        metadata = BASE / "review_only/kilicarslan/mendeley_files_metadata.json"
        if metadata.is_file():
            author_file = json.loads(metadata.read_text())[0]
            assert author_file["content_details"]["sha256_hash"] == entry["sha256"]
            assert author_file["size"] == entry["bytes"]
            entry.update(author_source="https://data.mendeley.com/datasets/dt89jydgnv/1",
                         author_download_url=author_file["content_details"]["download_url"],
                         author_source_license="CC BY 4.0", author_version=1,
                         identity_evidence="Local SHA-256 matches author API SHA-256; not an independent cohort.")
    manifest.append(entry)
(BASE / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({name: {k: v for k, v in values.items() if k not in {"raw_tables", "columns", "missing_by_column"}}
                  for name, values in result.items() if isinstance(values, dict)}, ensure_ascii=False, indent=2))
