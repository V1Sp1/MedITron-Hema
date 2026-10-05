"""Nested calibration of frozen v1 heads and a separate partial-label transfer ablation."""

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold

from .calibration import ScoreCalibrator, calibrate_scores, probability_metrics, reliability_bins
from .core import FEATURES, ROOT, SCENARIOS, SELECTION_SCENARIOS, TARGETS, augment, encode, fit, make_model, model_inputs, scenario, target_probabilities
from .improve import dump

POLICY = {"minimum_relative_log_loss_gain": .02, "maximum_brier_increase": .001,
          "maximum_ece_increase": .01, "maximum_f1_loss": .02,
          "external_weight_ratio": .10, "external_minimum_mean_f1_gain": .01,
          "default_promotion_requires_fresh_validation": True}
EXTERNAL_TARGETS = ["iron_deficiency", "B12_deficiency", "folate_deficiency"]


def select_method(rows, target, mode):
    views = SELECTION_SCENARIOS if mode == "primary" else ["cbc_only"]
    r = rows[rows.target.eq(target) & rows.route.eq(mode) & rows.scenario.isin(views)]
    baseline = r[r.method.eq("identity")].set_index("scenario")
    eligible = []
    for method in sorted(set(r.method)-{"identity"}):
        candidate = r[r.method.eq(method)].set_index("scenario")
        if (candidate.log_loss.mean() <= baseline.log_loss.mean() * (1-POLICY["minimum_relative_log_loss_gain"])
            and candidate.brier.mean() <= baseline.brier.mean()+POLICY["maximum_brier_increase"]
            and candidate.ece_10.mean() <= baseline.ece_10.mean()+POLICY["maximum_ece_increase"]
            and (candidate.f1-baseline.f1).min() >= -POLICY["maximum_f1_loss"]):
            eligible.append((candidate.log_loss.mean(), method))
    return min(eligible)[1] if eligible else "identity"


def external_subset(folder):
    rules = json.loads((folder / "rules.json").read_text())
    table = pd.read_csv(folder / "case_units_verified_source_partial.csv")
    audit = pd.read_csv(folder / "row_audit.csv")
    table = table.merge(audit[["patient_id", "duplicate_group_id", "needs_quality_review"]], on="patient_id", validate="one_to_one")
    # Quarantine previously flagged rows only in this experiment, preserve original copies.
    table = table[~table.needs_quality_review].drop_duplicates("duplicate_group_id").copy()
    allowed = {r["target"] for r in rules["normalization"]["verified"] if r["unit_conversion_allowed"]}
    if table[[k for k in FEATURES if k not in allowed]].notna().any().any():
        raise ValueError("Unverified external feature present")
    for target in EXTERNAL_TARGETS:
        if set(table[target].dropna().unique()) != {1}:
            raise ValueError("Only explicit source positives allowed; unknowns must remain missing")
    return table, rules


def fit_external(config, x, y, external_x, seed):
    # The case-only and transfer comparators use identical patient/augmentation weights.
    if config.get("augment"):
        x, y = augment(x, y, seed)
        external_x, external_y = augment(external_x, np.ones(len(external_x), dtype=int), seed+10)
    else:
        external_y = np.ones(len(external_x), dtype=int)
    ratio = POLICY["external_weight_ratio"]
    weights = np.r_[np.ones(len(x)), np.full(len(external_x), ratio*len(x)/len(external_x))]
    all_x = pd.concat([x, external_x], ignore_index=True)
    model = make_model(config, seed)
    model.fit(model_inputs(all_x, config), np.r_[y, external_y], sample_weight=weights)
    return model


def run(output, baseline, external):
    if output.exists():
        raise ValueError("Use a new immutable experiment directory")
    old = json.loads((baseline / "manifest.json").read_text())
    source = ROOT / "data/case/deficiency_anemia.csv"
    if hashlib.sha256(source.read_bytes()).hexdigest() != old["source_sha256"]:
        raise ValueError("Audited case source changed")
    data = pd.read_csv(source)
    splits = pd.read_csv(baseline / "splits.csv")
    if not np.array_equal(splits.row_index, np.arange(len(data))):
        raise ValueError("Invalid frozen split")
    dev = splits.loc[splits.partition.eq("development"), "row_index"].to_numpy()
    held = splits.loc[splits.partition.eq("holdout"), "row_index"].to_numpy()
    folds = splits.loc[dev, "fold"].to_numpy()
    x = encode(data)
    labels = old["labels"]
    views = {s: scenario(x, s, old["seed"]+100+i) for i, s in enumerate(SCENARIOS)}
    bundle = joblib.load(baseline / "models/selected.joblib")
    output.mkdir(parents=True)
    (output / "models").mkdir()
    snapshot = output / "source_snapshot"
    snapshot.mkdir()
    for path in Path(__file__).parent.glob("*.py"):
        shutil.copyfile(path, snapshot / path.name)
    splits.to_csv(output / "splits.csv", index=False)
    ext, ext_rules = external_subset(external)
    external_ids = {t: ext.loc[ext[t].eq(1), "patient_id"].tolist() for t in EXTERNAL_TARGETS}
    dump(output / "external_training_rows.json", external_ids)
    manifest = {"model_family": "calibrated_v3", "base_family": "baseline_v1", "started_at": datetime.now().astimezone().isoformat(),
                "seed": old["seed"], "outer_folds": old["folds"], "inner_folds": 3, "policy": POLICY,
                "source_sha256": old["source_sha256"], "dictionary_sha256": old["dictionary_sha256"],
                "split_sha256": hashlib.sha256((output / "splits.csv").read_bytes()).hexdigest(),
                "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshot.glob("*.py")},
                "versions": {k: importlib.metadata.version(k) for k in ["numpy", "pandas", "scikit-learn", "catboost", "scipy", "joblib"]},
                "development_n": len(dev), "reused_holdout_n": len(held), "holdout_is_reused": True,
                "clinical_validated": False, "binary_threshold": .5,
                "primary_calibration_views": SELECTION_SCENARIOS, "cbc_calibration_views": ["cbc_only"],
                "protocol": "Each outer training subset generates inner OOF scores; calibrators never see outer validation patients or labels. Each patient has total calibration weight 1 across views. Frozen v1 base architecture selection used all development folds earlier; residual selection optimism remains.",
                "external": {"source": ext_rules["source"], "normalization": "verified_only_10_features", "target_profile": "source_partial_positive_only",
                             "n_positive": {t: len(ids) for t, ids in external_ids.items()}, "unknown_loss_weight": 0,
                             "excluded_quality_flags": True, "deduplication": "full_raw_feature_vector_group", "calibration_source": False,
                             "files_sha256": {name: hashlib.sha256((external/name).read_bytes()).hexdigest() for name in ["rules.json", "case_units_verified_source_partial.csv", "row_audit.csv"]}},
                "warning": "Case labels are partly synthetic; positive-only external source labels are weak transferred labels. Calibration does not establish clinical probability. No external negative or multiclass labels are inferred."}
    dump(output / "manifest.json", manifest)
    cache, audit, rows, bins, calibrators = {}, [], [], [], {}
    started = time.monotonic()
    for target in TARGETS:
        y = data.iloc[dev][target].to_numpy()
        for mode in ("primary", "cbc"):
            name = bundle["selected"][target][mode]
            config = bundle["configs"][name]
            selected_views = SELECTION_SCENARIOS if mode == "primary" else ["cbc_only"]
            methods = ["identity", "temperature"] + (["sigmoid_logit"] if target != "anemia_class" else [])
            predictions = {(m, v): np.zeros((len(dev), len(labels[target]))) for m in methods for v in SCENARIOS}
            for fold in range(old["folds"]):
                train, valid = np.flatnonzero(folds != fold), np.flatnonzero(folds == fold)
                inner_p = {v: np.zeros((len(train), len(labels[target]))) for v in selected_views}
                cv = StratifiedKFold(3, shuffle=True, random_state=old["seed"]+1000+fold)
                for inner_fold, (learn, calibrate) in enumerate(cv.split(train, data.iloc[dev[train]].anemia_class)):
                    learn_rows, calibration_rows = dev[train[learn]], dev[train[calibrate]]
                    audit.append({"target": target, "route": mode, "outer_fold": fold, "inner_fold": inner_fold,
                                  "base_training_rows": learn_rows.tolist(), "calibration_rows": calibration_rows.tolist(),
                                  "outer_validation_rows": dev[valid].tolist()})
                    model = fit(config, x.iloc[learn_rows], y[train[learn]], old["seed"]+2000+fold*3+inner_fold, target == "anemia_class")
                    for view in selected_views:
                        inner_p[view][calibrate] = target_probabilities(model, views[view].iloc[calibration_rows], config, labels[target], target)
                pooled = np.concatenate([inner_p[v] for v in selected_views])
                pooled_y = np.tile(y[train], len(selected_views))
                weights = np.full(len(pooled_y), 1/len(selected_views))
                fitted = {m: ScoreCalibrator.fit(m, pooled, pooled_y, labels[target], weights) for m in methods}
                model = fit(config, x.iloc[dev[train]], y[train], old["seed"]+fold, target == "anemia_class")
                for view in SCENARIOS:
                    raw = target_probabilities(model, views[view].iloc[dev[valid]], config, labels[target], target)
                    for method in methods:
                        predictions[(method, view)][valid] = calibrate_scores(fitted[method], raw, views[view].iloc[dev[valid]], target)
            for (method, view), p in predictions.items():
                key = f"{mode}__{target}__{method}__{view}"
                cache[key] = p
                rows.append({"target": target, "route": mode, "method": method, "scenario": view, "partition": "nested_development_oof", **probability_metrics(y, p, labels[target])})
                bins.extend({"target": target, "route": mode, "method": method, "scenario": view, "partition": "nested_development_oof", **b} for b in reliability_bins(y, p, labels[target]))
            decision = select_method(pd.DataFrame(rows), target, mode)
            # Final scaler fitted on five-fold development OOF, never fitted case predictions.
            pooled = np.concatenate([predictions[("identity", v)] for v in selected_views])
            fitted = ScoreCalibrator.fit(decision, pooled, np.tile(y, len(selected_views)), labels[target], np.full(len(y)*len(selected_views), 1/len(selected_views)))
            calibrators[(target, mode)] = fitted
            print(f"Calibration {target:22s} {mode:7s} selected={decision:14s} ({time.monotonic()-started:.1f}s)", flush=True)
    np.savez_compressed(output / "oof_probabilities.npz", **cache)
    dump(output / "split_audit.json", audit)
    pd.DataFrame(rows).to_csv(output / "development_metrics.csv", index=False)
    choices = {t: {mode: asdict(calibrators[(t, mode)]) for mode in ("primary", "cbc")} for t in TARGETS}
    # Lock all calibration choices before inspecting the repeated holdout.
    dump(output / "calibration_selection.json", choices)
    calibrated = {**bundle, "model_family": "calibrated_v3", "experiment": str(output), "calibrators": calibrators,
                  "calibration": {"status": "all_heads" if all(c.method != "identity" for c in calibrators.values()) else "partial" if any(c.method != "identity" for c in calibrators.values()) else "none",
                                  "scope": "case_development_oof_with_nested_assessment", "methods": choices, "clinicalValidated": False,
                                  "externalCalibration": False, "independentValidation": False, "binaryThreshold": .5},
                  "calibrated": all(c.method != "identity" for c in calibrators.values())}
    joblib.dump(calibrated, output / "models/selected.joblib", compress=3)
    test_cache = {}
    for target in TARGETS:
        for mode in ("primary", "cbc"):
            name = bundle["selected"][target][mode]
            for view in SCENARIOS:
                raw = target_probabilities(bundle["models"][(name,target)], views[view].iloc[held], bundle["configs"][name], labels[target], target)
                q = calibrate_scores(calibrators[(target,mode)], raw, views[view].iloc[held], target)
                for method, p in [("identity", raw), ("selected", q)]:
                    test_cache[f"{mode}__{target}__{method}__{view}"] = p
                    y = data.iloc[held][target].to_numpy()
                    rows.append({"target": target, "route": mode, "method": method, "scenario": view, "partition": "reused_holdout", **probability_metrics(y, p, labels[target])})
                    bins.extend({"target": target, "route": mode, "method": method, "scenario": view, "partition": "reused_holdout", **b} for b in reliability_bins(y, p, labels[target]))
    np.savez_compressed(output / "holdout_probabilities.npz", **test_cache)
    pd.DataFrame(rows).to_csv(output / "metrics.csv", index=False)
    pd.DataFrame(bins).to_csv(output / "reliability_bins.csv", index=False)
    dump(output / "release_decision.json", {"promote_default": False, "reason": "Fresh independent labeled validation required by prespecified policy; repeated holdout is diagnostic only.", "candidate_selection_changed_after_holdout": False})
    # Separate transfer trial: do not combine with calibration or use proxy labels to calibrate.
    transfer_rows, transfer_cache, external_models = [], {}, {}
    for target in EXTERNAL_TARGETS:
        name = bundle["selected"][target]["primary"]
        config = bundle["configs"][name]
        ext_x = encode(ext[ext[target].eq(1)])
        predicted = {v: np.zeros((len(dev), 2)) for v in SCENARIOS}
        for fold in range(old["folds"]):
            train, valid = dev[folds != fold], np.flatnonzero(folds == fold)
            model = fit_external(config, x.iloc[train], data.iloc[train][target].to_numpy(), ext_x, old["seed"]+fold)
            for view in SCENARIOS:
                predicted[view][valid] = target_probabilities(model, views[view].iloc[dev[valid]], config, labels[target], target)
        for view, p in predicted.items():
            transfer_cache[f"{target}__{view}"] = p
            for method, q in [("case_only", cache[f"primary__{target}__identity__{view}"]), ("external_partial", p)]:
                transfer_rows.append({"target": target, "method": method, "scenario": view, "partition": "development_oof", **probability_metrics(data.iloc[dev][target].to_numpy(), q, labels[target])})
        # Save research weights even when the transfer trial does not pass the fixed guards.
        model = fit_external(config, x.iloc[dev], data.iloc[dev][target].to_numpy(), ext_x, old["seed"])
        external_models[(name,target)] = model
        print(f"Transfer {target:22s} positives={len(ext_x)} ({time.monotonic()-started:.1f}s)", flush=True)
    frame = pd.DataFrame(transfer_rows)
    transfer_selection = {}
    for target in EXTERNAL_TARGETS:
        r = frame[frame.target.eq(target) & frame.scenario.isin(SELECTION_SCENARIOS)]
        a = r[r.method.eq("case_only")].set_index("scenario")
        b = r[r.method.eq("external_partial")].set_index("scenario")
        passed = b.f1.mean() >= a.f1.mean()+POLICY["external_minimum_mean_f1_gain"] and (b.f1-a.f1).min() >= -.02 and b.brier.mean() <= a.brier.mean()+.001
        transfer_selection[target] = {"candidate_passes_development_guards": bool(passed), "mean_f1_delta": float(b.f1.mean()-a.f1.mean()), "mean_brier_delta": float(b.brier.mean()-a.brier.mean()), "deployed": False}
    dump(output / "external_selection.json", transfer_selection)
    # Three positive-only transfer heads, others frozen v1; explicitly uncalibrated separate bundle.
    external_bundle = {**bundle, "models": {**bundle["models"], **external_models}, "experiment": str(output),
                       "model_family": "transfer_v3", "calibrated": False, "external_training": manifest["external"]}
    joblib.dump(external_bundle, output / "models/external_partial.joblib", compress=3)
    for target in EXTERNAL_TARGETS:
        name = bundle["selected"][target]["primary"]
        for view in SCENARIOS:
            p = target_probabilities(external_models[(name,target)], views[view].iloc[held], bundle["configs"][name], labels[target], target)
            transfer_cache[f"holdout__{target}__{view}"] = p
            for method, q in [("case_only", test_cache[f"primary__{target}__identity__{view}"]), ("external_partial", p)]:
                transfer_rows.append({"target": target, "method": method, "scenario": view, "partition": "reused_holdout", **probability_metrics(data.iloc[held][target].to_numpy(), q, labels[target])})
    np.savez_compressed(output / "external_probabilities.npz", **transfer_cache)
    pd.DataFrame(transfer_rows).to_csv(output / "external_metrics.csv", index=False)
    manifest.update(finished_at=datetime.now().astimezone().isoformat(), elapsed_seconds=time.monotonic()-started)
    dump(output / "manifest.json", manifest)
    print(f"Finished {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/calibrated_v3")
    parser.add_argument("--baseline", type=Path, default=ROOT / "experiments/baseline_v1")
    parser.add_argument("--external", type=Path, default=ROOT / "data/external/processed/kilicarslan")
    args = parser.parse_args()
    run(args.output.resolve(), args.baseline.resolve(), args.external.resolve())


if __name__ == "__main__":
    main()
