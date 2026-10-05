"""Reproducible source audit, unit normalization and explicitly partial/weak labels.

No model is trained. Raw files are preserved. Assumed units and reconstructed
source rules are never represented as clinically validated case labels.
"""

from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/external/review_only/kilicarslan/SKILICARSLAN_Anemia_DataSet.xlsx"
CASE = Path("data/case/deficiency_anemia.csv")
OUT = ROOT / "data/external/processed/kilicarslan"
OUT.mkdir(parents=True, exist_ok=True)
VERSION = "kilicarslan-transfer-0.1.0"
CLASS_NAMES = {0: "non-anemic", 1: "HGB anemia", 2: "iron deficiency anemia",
               3: "folate deficiency anemia", 4: "B12 deficiency anemia"}
SOURCE_FLAGS = {"HGB_Anemia_Class": 1, "Iron_anemia_Class": 2,
                "Folate_anemia_class": 3, "B12_Anemia_class": 4}
TARGETS = ["anemia", "iron_deficiency", "B12_deficiency", "folate_deficiency",
           "B6_deficiency", "copper_deficiency", "inflammation_anemia", "mixed_deficiency"]
DEFICITS = ["iron_deficiency", "B12_deficiency", "folate_deficiency"]
DICT = json.loads((ROOT / "data/feature_dictionary.json").read_text())
FEATURES = [c["name"] for c in DICT["columns"] if c["role"] == "feature"]
CASE_COLUMNS = [c["name"] for c in DICT["columns"]]
UNITS = {c["name"]: c["unit"] for c in DICT["columns"]}
UNIT_MAP = json.loads((ROOT / "data/external/unit_mapping.json").read_text())["sources"]["kilicarslan"]["entries"]
WHO_IRON = "https://www.who.int/publications/i/item/9789240000124"
NICE = "https://www.nice.org.uk/guidance/ng239/chapter/Recommendations"
WHO_FOLATE = "https://extranet.who.int/indcat/TemplateView.aspx?id=32"

k = pd.read_excel(RAW)
case = pd.read_csv(CASE)
row_ids = pd.Series([f"kilicarslan_row_{i + 2:05d}" for i in range(len(k))], index=k.index)
raw_features = list(k.columns[:24])
raw_targets = list(SOURCE_FLAGS) + ["All_Class"]
assert len(k.columns) == 29 and len(k) == 15300
assert k.All_Class.isin(CLASS_NAMES).all()
assert k.GENDER.isin([0, 1]).all()
assert np.isfinite(k.to_numpy()).all() and k.ge(0).all().all()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_write(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def csv_write(name, frame):
    frame.to_csv(OUT / name, index=False, na_rep="", float_format="%.12g")


def integer_targets(frame):
    for col in TARGETS:
        frame[col] = frame[col].astype("Int64")
    return frame


def empty_labels():
    d = pd.DataFrame({"patient_id": row_ids})
    for target in TARGETS:
        d[target] = pd.Series(pd.NA, index=k.index, dtype="Int64")
    d["anemia_class"] = pd.Series(pd.NA, index=k.index, dtype="string")
    d["deficiency_cause"] = pd.Series(pd.NA, index=k.index, dtype="string")
    return d


def candidate_categories(d):
    """Candidate categories only, within measured iron/B12/folate scope."""
    positives = d[DEFICITS].eq(1).fillna(False)
    count = positives.sum(axis=1)
    anemic = d.anemia.eq(1)
    no_anemia = d.anemia.eq(0)
    for target, yes, no, cause in [
        ("iron_deficiency", "iron_deficiency_anemia", "latent_deficiency", "iron_deficiency"),
        ("B12_deficiency", "B12_deficiency_anemia", "B12_deficiency_no_anemia", "B12_deficiency"),
        ("folate_deficiency", "folate_deficiency_anemia", "folate_deficiency_no_anemia", "folate_deficiency"),
    ]:
        only = positives[target] & count.eq(1)
        d.loc[only & anemic, "anemia_class"] = yes
        d.loc[only & no_anemia, "anemia_class"] = no
        d.loc[only, "deficiency_cause"] = cause
    mixed = count.ge(2)
    d.loc[mixed, "mixed_deficiency"] = 1
    d.loc[mixed & anemic, "anemia_class"] = "mixed_deficiency"
    combination = positives.apply(lambda row: "_".join(name for name, active in
                                    zip(["iron", "B12", "folate"], row) if active), axis=1)
    d.loc[mixed, "deficiency_cause"] = combination.loc[mixed]
    return integer_targets(d)


def known_counts(d):
    return {target: {"positive": int(d[target].eq(1).sum()), "negative": int(d[target].eq(0).sum()),
                     "unknown": int(d[target].isna().sum())} for target in TARGETS}


def record_stats(series):
    numeric = pd.to_numeric(series, errors="coerce")
    quantiles = numeric.quantile([.01, .05, .25, .5, .75, .95, .99])
    q1, q3 = quantiles.loc[.25], quantiles.loc[.75]
    spread = q3 - q1
    tail = numeric.lt(q1 - 3 * spread) | numeric.gt(q3 + 3 * spread)
    return {"missing": int(series.isna().sum()), "non_numeric": int((numeric.isna() & series.notna()).sum()),
            "non_finite": int((~np.isfinite(numeric) & numeric.notna()).sum()),
            "negative": int(numeric.lt(0).sum()), "zero": int(numeric.eq(0).sum()),
            "min": float(numeric.min()), "max": float(numeric.max()),
            "quantiles": {str(q): float(v) for q, v in quantiles.items()},
            "far_tukey_tail_count": int(tail.sum()),
            "top_repeated_values": [{"value": float(v), "count": int(n)} for v, n in numeric.value_counts().head(3).items()]}


# Exact source redundancy and post-hoc reconstruction; not diagnostic validation.
flag_mismatch = {name: int(k[name].ne(k.All_Class.eq(code).astype(int)).sum())
                 for name, code in SOURCE_FLAGS.items()}
assert not any(flag_mismatch.values())
anemia = k.HGB.lt(np.where(k.GENDER.eq(0), 12, 13))
source_iron = k.FERRITTE.lt(15) | k.TSD.lt(20)
source_B12 = k.B12.lt(200)
source_folate = k.FOLATE.lt(4)
reconstructed = np.select([~anemia, source_iron, source_folate, source_B12], [0, 2, 3, 4], default=1)
assert np.array_equal(reconstructed, k.All_Class.to_numpy())

duplicate_hash = k[raw_features].apply(
    lambda row: hashlib.sha256(json.dumps(row.tolist(), separators=(",", ":")).encode()).hexdigest(), axis=1)
repeated = duplicate_hash.duplicated(keep="first")
group_conflicts = k.assign(group=duplicate_hash).groupby("group").All_Class.nunique()

quality = pd.DataFrame({"patient_id": row_ids, "source_excel_row": np.arange(2, len(k) + 2),
                        "source_class": k.All_Class, "duplicate_group_id": duplicate_hash,
                        "repeated_feature_vector": repeated, "source_class_rule_match": reconstructed == k.All_Class})
flags = {
    "review_MCHC_gt_400_g_L": k.MCHC.gt(40),
    "review_MCH_gt_50_pg": k.MCH.gt(50),
    "review_WBC_gt_50_candidate_10e9_L": k.WBC.gt(50),
    "review_PLT_gt_1000_10e9_L": k.PLT.gt(1000),
    "review_Hb_lt_50_g_L": k.HGB.lt(5),
    "review_Hb_gt_200_g_L": k.HGB.gt(20),
    "review_ferritin_gt_5000_ug_L": k.FERRITTE.gt(5000),
    "review_B12_gt_2000_candidate_pg_mL": k.B12.gt(2000),
    "review_WBC_differential_sum_delta_gt_0_3": (k.WBC - k[["NE#", "LY#", "MO#", "EO#", "BA#"]].sum(axis=1)).abs().gt(.3),
}
for name, flag in flags.items():
    quality[name] = flag
quality["review_flags"] = pd.DataFrame(flags).apply(lambda row: ";".join(row.index[row].tolist()), axis=1)
quality["needs_quality_review"] = quality.review_flags.ne("")
quality["severe_CBC_artifact_candidate"] = k.MCHC.gt(60) | k.MCH.gt(60)
csv_write("row_audit.csv", quality)
review = pd.concat([quality.loc[:, ["patient_id", "source_excel_row", "review_flags", "severe_CBC_artifact_candidate"]], k], axis=1)
csv_write("rows_for_review.csv", review.loc[quality.needs_quality_review])

# Conservative direct source transfer: no deficiency negatives or forced multiclass.
partial = empty_labels()
partial["anemia"] = k.All_Class.ne(0).astype("Int64")
for target, code in [("iron_deficiency", 2), ("B12_deficiency", 4), ("folate_deficiency", 3)]:
    partial.loc[k.All_Class.eq(code), target] = 1
csv_write("labels_source_partial.csv", partial)

# Unit-normalized copies retain all records and never modify source values in-place.
verified = pd.DataFrame({col: pd.Series(np.nan, index=k.index) for col in FEATURES})
for entry in UNIT_MAP:
    if entry["unit_conversion_allowed"]:
        verified[entry["target"]] = k[entry["column"]] * entry["factor_to_case"]
candidate = verified.copy()
candidate["sex"] = k.GENDER.map({0: "F", 1: "M"})
candidate["RBC"] = k.RBC
candidate["WBC"] = k.WBC
candidate["vitamin_B12"] = k.B12
candidate["TIBC"] = k.SDTSD * .1791
candidate["TSAT"] = k.TSD
candidate["UIBC"] = candidate.TIBC - candidate.serum_iron
assumptions = {
    "sex": "0=F, 1=M inferred from published sex counts and complete Hb/class agreement.",
    "RBC": "Assume 10^12/L, not erroneous paper 10^3/mL; supported by RBC/Hb/HCT/MCV identities.",
    "WBC": "Assume 10^9/L (10^3/µL), not paper 10^3/mL; scale remains unconfirmed by original author.",
    "vitamin_B12": "Assume pg/mL, not paper ng/mL; supported by class-4 range and reconstructed <200 source rule, not independently confirmed.",
    "TIBC": "Assume SDTSD is TIBC µg/dL; x0.1791 to µmol/L.",
    "TSAT": "Assume TSD is percentage; exact 100*SD/SDTSD identity.",
    "UIBC": "Derived TIBC - serum_iron from assumed same-panel values; not an original measurement.",
}


def integrated(features, labels):
    result = features.copy()
    for column in ["patient_id", *TARGETS, "anemia_class", "deficiency_cause"]:
        result[column] = labels[column]
    return result[CASE_COLUMNS]


csv_write("case_units_verified_source_partial.csv", integrated(verified, partial))
csv_write("case_units_candidate_source_partial.csv", integrated(candidate, partial))
csv_write("case_units_candidate_source_partial_deduplicated.csv", integrated(candidate, partial).loc[~repeated])

# Source-compatible weak rules: negative means the rule is false, not a excluded disease.
weak = empty_labels()
weak["anemia"] = anemia.astype("Int64")
for target, signal in zip(DEFICITS, [source_iron, source_B12, source_folate]):
    weak[target] = signal.astype("Int64")
weak = candidate_categories(weak)
csv_write("labels_source_rules_weak.csv", weak)

# Higher-specificity positive signals; unknown is not transformed into zero.
positive = partial.copy()
low = {"iron_deficiency": k.FERRITTE.lt(15), "B12_deficiency": k.B12.lt(180), "folate_deficiency": k.FOLATE.lt(3)}
for target, flag in low.items():
    positive.loc[flag, target] = 1
positive = candidate_categories(positive)
csv_write("labels_source_plus_low_candidates.csv", positive)

origins = pd.DataFrame({"patient_id": row_ids})
for target, code in [("anemia", None), ("iron_deficiency", 2), ("B12_deficiency", 4), ("folate_deficiency", 3)]:
    if target == "anemia":
        origins[target + "_source_partial_origin"] = "source_class"
        origins[target + "_source_plus_low_origin"] = "source_class_Hb_rule_agreement"
        continue
    from_source = k.All_Class.eq(code)
    signal = low[target]
    origins[target + "_source_partial_origin"] = np.where(from_source, "source_class", "unknown")
    origins[target + "_source_plus_low_origin"] = np.select(
        [from_source & signal, from_source, signal], ["source_and_low_biomarker", "source_class", "low_biomarker_candidate"], default="unknown")
    origins[target + "_source_rules_origin"] = "reconstructed_source_rule_proxy"
for target in ["B6_deficiency", "copper_deficiency", "inflammation_anemia"]:
    origins[target + "_origin"] = "unknown_no_assay_or_diagnosis"
origins["source_rules_mixed_origin"] = np.where(weak.mixed_deficiency.eq(1).fillna(False), "at_least_two_rule_positive_targets", "unknown")
origins["source_plus_low_mixed_origin"] = np.where(positive.mixed_deficiency.eq(1).fillna(False), "at_least_two_positive_evidence_targets", "unknown")
origins["candidate_class_status"] = "unvalidated_assumed_units_three_nutrient_scope"
csv_write("label_origins.csv", origins)

masks = pd.DataFrame({"patient_id": row_ids})
for name, frame in [("source_partial", partial), ("source_rules_weak", weak), ("source_plus_low", positive)]:
    for target in TARGETS:
        masks[name + "__" + target + "__known"] = frame[target].notna().astype(int)
    masks[name + "__class_candidate_present"] = frame.anemia_class.notna().astype(int)
    masks[name + "__multiclass_training_approved"] = 0
csv_write("label_masks.csv", masks)

comparisons = []
for target in FEATURES:
    if target == "sex" or candidate[target].isna().all():
        continue
    ext = candidate[target].dropna()
    own = case[target].dropna()
    comparisons.append({"feature": target, "case_unit": UNITS[target], "case_measured": len(own),
                        "external_measured": len(ext), "case_min": own.min(), "case_median": own.median(),
                        "case_max": own.max(), "external_min": ext.min(), "external_median": ext.median(),
                        "external_max": ext.max(), "outside_case_observed_range": int((ext.lt(own.min()) | ext.gt(own.max())).sum()),
                        "external_unit_is_assumed": target in assumptions})
comparison = pd.DataFrame(comparisons)
csv_write("feature_distribution_comparison.csv", comparison)

agreements = []
for name, target, predicted, available in [
    ("source_iron_rule", "iron_deficiency", case.ferritin.lt(15) | case.TSAT.lt(20), case.ferritin.notna() & case.TSAT.notna()),
    ("source_B12_rule", "B12_deficiency", case.vitamin_B12.lt(200), case.vitamin_B12.notna()),
    ("source_folate_rule", "folate_deficiency", case.folate.lt(4), case.folate.notna()),
    ("low_ferritin_signal", "iron_deficiency", case.ferritin.lt(15), case.ferritin.notna()),
    ("low_B12_signal", "B12_deficiency", case.vitamin_B12.lt(180), case.vitamin_B12.notna()),
    ("low_folate_signal", "folate_deficiency", case.folate.lt(3), case.folate.notna()),
]:
    actual = case.loc[available, target]
    pred = predicted.loc[available]
    tp = int((actual.eq(1) & pred).sum())
    fp = int((actual.eq(0) & pred).sum())
    fn = int((actual.eq(1) & ~pred).sum())
    tn = int((actual.eq(0) & ~pred).sum())
    disagreements = case.loc[available & case[target].ne(predicted.astype(int)), "anemia_class"].value_counts().to_dict()
    agreements.append({"rule": name, "target": target, "measured_case_rows": int(available.sum()),
                       "TP": tp, "FP": fp, "FN": fn, "TN": tn,
                       "agreement": (tp + tn) / len(actual), "precision": tp / (tp + fp),
                       "recall": tp / (tp + fn), "disagreement_case_classes": disagreements,
                       "interpretation": "Descriptive agreement with partially synthetic case labels on measured subset; not clinical or held-out model accuracy. Signal-negative does not become a conservative deficiency-negative label."})
csv_write("rule_agreement_with_case.csv", pd.DataFrame(agreements).drop(columns="disagreement_case_classes"))

profile_results = {}
for name, d in [("source_partial", partial), ("source_rules_weak", weak), ("source_plus_low", positive)]:
    npos = d[DEFICITS].eq(1).fillna(False).sum(axis=1)
    profile_results[name] = {"binary_labels": known_counts(d), "candidate_classes": d.anemia_class.value_counts().to_dict(),
                             "unknown_class": int(d.anemia_class.isna().sum()),
                             "multiple_deficits_with_anemia": int((npos.ge(2) & anemia).sum()),
                             "multiple_deficits_without_anemia": int((npos.ge(2) & ~anemia).sum())}

audit = {
    "version": VERSION, "checked_on": "2026-10-03", "source_sha256": sha(RAW),
    "rows": len(k), "raw_feature_columns": raw_features, "raw_target_columns": raw_targets,
    "missing_cells": int(k.isna().sum().sum()), "blank_string_cells": int(k.astype(str).apply(lambda c: c.str.strip().eq("")).sum().sum()),
    "numeric_column_stats": {name: record_stats(k[name]) for name in k.columns},
    "source_class_counts": {str(code): {"name": CLASS_NAMES[code], "rows": int(k.All_Class.eq(code).sum())} for code in CLASS_NAMES},
    "one_hot_mismatches": flag_mismatch,
    "duplicate_feature_rows_after_first": int(repeated.sum()), "unique_feature_vectors": int(duplicate_hash.nunique()),
    "duplicate_groups_with_conflicting_classes": int(group_conflicts.gt(1).sum()),
    "reconstructed_source_class_matches": int((reconstructed == k.All_Class).sum()),
    "reconstruction_disclaimer": "Post-hoc explanatory equivalence on this file; not proof of authors' actual labeling code, clinical diagnosis or generalization.",
    "source_iron_label_ferritin_ge_15_and_TSAT_lt_20": int((k.All_Class.eq(2) & k.FERRITTE.ge(15) & k.TSD.lt(20)).sum()),
    "source_non_anemic_any_source_rule_deficit": int((k.All_Class.eq(0) & (source_iron | source_B12 | source_folate)).sum()),
    "source_non_anemic_any_low_biomarker_signal": int((k.All_Class.eq(0) & pd.DataFrame(low).any(axis=1)).sum()),
    "raw_low_biomarker_multiple_with_anemia": int((pd.DataFrame(low).sum(axis=1).ge(2) & anemia).sum()),
    "raw_low_biomarker_multiple_without_anemia": int((pd.DataFrame(low).sum(axis=1).ge(2) & ~anemia).sum()),
    "review_rule_counts": {name: int(flag.sum()) for name, flag in flags.items()},
    "rows_needing_quality_review_union": int(quality.needs_quality_review.sum()),
    "severe_CBC_artifact_candidates": int(quality.severe_CBC_artifact_candidate.sum()),
    "CBC_identity_max_errors_raw_units": {
        "MCH_vs_10Hb_over_RBC": float((k.MCH - 10 * k.HGB / k.RBC).abs().max()),
        "MCHC_vs_100Hb_over_HCT": float((k.MCHC - 100 * k.HGB / k.HCT).abs().max()),
        "HCT_vs_RBC_MCV_over_10": float((k.HCT - k.RBC * k.MCV / 10).abs().max()),
        "TSD_vs_100SD_over_SDTSD": float((k.TSD - 100 * k.SD / k.SDTSD).abs().max()),
        "WBC_vs_differential_sum": float((k.WBC - k[["NE#", "LY#", "MO#", "EO#", "BA#"]].sum(axis=1)).abs().max()),
    },
    "source_anemia_fraction": float(anemia.mean()), "case_anemia_fraction": float(case.anemia.mean()),
    "verified_feature_count": int(verified.notna().any().sum()),
    "candidate_feature_count_including_derived_UIBC": int(candidate.notna().any().sum()),
    "candidate_absent_features": candidate.columns[candidate.isna().all()].tolist(),
    "candidate_input_missing_fraction": float(candidate.isna().mean().mean()),
    "unit_assumptions": assumptions, "profiles": profile_results, "case_rule_agreement": agreements,
    "training_ready": False,
}
json_write("audit.json", audit)

rules = {
    "version": VERSION, "status": "research_candidate_not_clinically_approved", "source_sha256": sha(RAW),
    "source": {"url": "https://data.mendeley.com/datasets/dt89jydgnv/1", "version": 1,
               "license": "CC BY 4.0", "authors": ["Serhat Kılıçarslan", "Mete Celik", "Safak Şahin"]},
    "normalization": {"verified": [r for r in UNIT_MAP if r["unit_conversion_allowed"]], "candidate_assumptions": assumptions},
    "anemia": {"input": "hemoglobin", "unit": "g/L", "female_lt": 120, "male_lt": 130,
               "sex_encoding_assumption": {"0": "F", "1": "M"}, "missing_input_result": None},
    "reconstructed_source_rule_order": ["if not anemic: 0", "else if ferritin<15 or TSAT<20: 2",
                                         "else if folate<4: 3", "else if B12<200: 4", "else: 1"],
    "profiles": {
        "source_partial": {"deficiency_positive_codes": {"iron_deficiency": 2, "B12_deficiency": 4, "folate_deficiency": 3},
                           "other_deficiency_values": None, "multiclass": None, "negative_one_hot_is_negative_deficiency": False},
        "source_rules_weak": {"iron_deficiency_proxy": "ferritin<15 µg/L OR TSAT<20%", "B12_deficiency_proxy": "B12<200 pg/mL",
                              "folate_deficiency_proxy": "folate<4 ng/mL", "negative_meaning": "Rule-false, not confirmed exclusion of disease.",
                              "requires_unit_assumptions": True, "rule_origin": "Post-hoc source reconstruction, not a validated clinical guideline."},
        "source_plus_low": {"positive_when": "Source corresponding deficiency class OR respective low biomarker signal.",
                            "iron_signal": "ferritin<15 µg/L", "iron_reference": WHO_IRON,
                            "B12_signal": "B12<180 pg/mL; assumes source unit", "B12_reference": NICE,
                            "folate_signal": "serum folate<3 ng/mL", "folate_reference": WHO_FOLATE,
                            "negative_deficiency_values": None, "all_other_deficits": None,
                            "biomarker_vs_causation": "Low measured nutrient signal does not by itself establish the cause of anemia."},
    },
    "mixed": {"one": "At least two positive iron/B12/folate targets in the chosen candidate profile.",
              "zero": None, "reason": "B6 and copper remain unknown; absence of a measured combination does not exclude mixed deficiency."},
    "candidate_multiclass": {"one_known_positive": "Use nutrient-specific anemia/no-anemia candidate; unmeasured additional deficits not excluded.",
                             "two_or_more_and_anemia": "mixed_deficiency candidate", "two_or_more_without_anemia": None,
                             "no_positive": None, "no_anemia_no_deficiency": None, "anemia_other": None,
                             "B6_deficiency": None, "copper_deficiency": None, "inflammation_anemia": None,
                             "cause_strings": "Known measured combination only; may be incomplete. Triple iron_B12_folate is not present in current case CSV.",
                             "training_approved": False},
    "quality": {"policy": "Flag and preserve; no automatic winsorization, deletion or diagnostic interpretation of extremes.",
                "review_thresholds": list(flags), "duplicates": "Group by complete raw feature vector before splitting; ID is an Excel-row reference, not a known patient ID."},
    "missing": {"csv": "empty cell", "semantic": "unknown", "loss_mask": "0 for unknown, 1 for observed/proposed target; candidate class approval is a separate flag"},
}
json_write("rules.json", rules)

manifest = {"version": VERSION, "source_sha256": sha(RAW), "case_sha256": sha(CASE),
            "dictionary_sha256": sha(ROOT / "data/feature_dictionary.json"), "script_sha256": sha(Path(__file__)),
            "unit_mapping_sha256": sha(ROOT / "data/external/unit_mapping.json"),
            "pandas_version": pd.__version__, "numpy_version": np.__version__, "files": []}
for path in sorted(OUT.iterdir()):
    if path.suffix in {".csv", ".json"} and path.name != "manifest.json":
        manifest["files"].append({"path": path.name, "bytes": path.stat().st_size, "sha256": sha(path)})
json_write("manifest.json", manifest)
print(json.dumps({key: audit[key] for key in ["rows", "missing_cells", "duplicate_feature_rows_after_first",
                 "reconstructed_source_class_matches", "rows_needing_quality_review_union", "profiles"]}, ensure_ascii=False, indent=2))
