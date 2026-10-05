"""Second local experiment on the frozen baseline split; select before repeated holdout."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd

from .advanced import AdvancedClassifier, CONFIGS_V2, hb_consistent
from .core import DICTIONARY, FEATURES, ROOT, SCENARIOS, SELECTION_SCENARIOS, TARGETS, encode, scenario, target_probabilities
from .train import metrics


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def choose(cv, configs, incumbent, target, mode):
    views = SELECTION_SCENARIOS if mode == "primary" else ["cbc_only"]
    rows = cv[cv.target.eq(target) & cv.scenario.isin(views)]
    rankings = rows.groupby("config").agg(score=("f1", "mean"), precision=("precision", "mean"),
                                          specificity=("specificity", "mean")).reset_index()
    if mode == "cbc":
        rankings = rankings[rankings.config.map(lambda name: configs[name]["panel"] == "cbc")]
    previous = rankings[rankings.config.eq(incumbent)].iloc[0]
    base_natural = cv[cv.target.eq(target) & cv.config.eq(incumbent) & cv.scenario.eq("available")].iloc[0].f1
    for candidate in rankings.sort_values(["score", "config"], ascending=[False, True]).itertuples():
        natural = cv[cv.target.eq(target) & cv.config.eq(candidate.config) & cv.scenario.eq("available")].iloc[0].f1
        if candidate.config == incumbent:
            return incumbent, float(previous.score), "incumbent_retained"
        if candidate.score < previous.score + .01 or natural < base_natural - .02:
            continue
        if target != "anemia_class" and (candidate.precision < previous.precision - .10 or candidate.specificity < previous.specificity - .02):
            continue
        return candidate.config, float(candidate.score), "development_gain_with_guards"
    return incumbent, float(previous.score), "incumbent_retained"


def run(source, baseline, output):
    if output.exists():
        raise ValueError("Use a new experiment directory; previous experiments are immutable")
    old = json.loads((baseline / "manifest.json").read_text())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != old["source_sha256"] or digest != DICTIONARY["case_source"]["sha256"]:
        raise ValueError("Source differs from audited baseline")
    data = pd.read_csv(source)
    split = pd.read_csv(baseline / "splits.csv")
    if not np.array_equal(split.row_index, np.arange(len(data))) or data[FEATURES].duplicated().any():
        raise ValueError("Split or patient identity requires new audit")
    dev = split.loc[split.partition.eq("development"), "row_index"].to_numpy()
    holdout = split.loc[split.partition.eq("holdout"), "row_index"].to_numpy()
    fold_ids = split.loc[dev, "fold"].to_numpy()
    x = encode(data)
    views = {name: scenario(x, name, old["seed"] + 100 + i) for i, name in enumerate(SCENARIOS)}
    labels = old["labels"]
    previous = json.loads((baseline / "selection.json").read_text())
    configs = {c["name"]: c for c in old["configs"] if c["name"] not in {"prior", "missingness_only"}}
    configs.update({c["name"]: c for c in CONFIGS_V2})
    output.mkdir(parents=True)
    (output / "models").mkdir()
    split.to_csv(output / "splits.csv", index=False)
    manifest = {"model_family": "robust_v2", "source_sha256": digest, "dictionary_sha256": old["dictionary_sha256"],
                "development_n": len(dev), "holdout_n": len(holdout), "baseline": str(baseline),
                "seed": old["seed"], "folds": old["folds"], "new_configs": CONFIGS_V2,
                "started_at": datetime.now().astimezone().isoformat(), "binary_threshold": .5,
                "selection_scenarios": SELECTION_SCENARIOS, "cbc_selection_scenarios": ["cbc_only"],
                "replacement_guards": {"minimum_cv_f1_gain": .01, "maximum_natural_f1_loss": .02,
                                       "maximum_precision_loss": .10, "maximum_specificity_loss": .02},
                "external_training": False, "calibrated": False,
                "holdout_is_reused": True, "holdout_warning": "Previously inspected in v1; repeated comparison is not fresh independent validation.",
                "selection_warning": "Candidate ranking and OOF reporting share folds; selected CV scores can be optimistic.",
                "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")}}
    # Prespecified candidates and decision guards are saved before new fits or holdout reads.
    dump(output / "manifest.json", manifest)
    started = time.monotonic()
    cache = {}
    with np.load(baseline / "oof_probabilities.npz") as stored:
        for name in configs:
            if name not in {c["name"] for c in CONFIGS_V2}:
                for target in TARGETS:
                    for view in SCENARIOS:
                        cache[f"{name}__{target}__{view}"] = stored[f"{name}__{target}__{view}"]
    for config in CONFIGS_V2:
        for target in TARGETS:
            target_start = time.monotonic()
            predictions = {view: np.zeros((len(dev), len(labels[target]))) for view in SCENARIOS}
            for fold in range(old["folds"]):
                train, valid = np.flatnonzero(fold_ids != fold), np.flatnonzero(fold_ids == fold)
                model = AdvancedClassifier(config, target, old["seed"] + fold).fit(x.iloc[dev[train]], data.iloc[dev[train]][target].to_numpy())
                for view in SCENARIOS:
                    predictions[view][valid] = target_probabilities(model, views[view].iloc[dev[valid]], config, labels[target], target)
            for view, scores in predictions.items():
                cache[f"{config['name']}__{target}__{view}"] = scores
            score = metrics(data.iloc[dev][target].to_numpy(), predictions["available"], labels[target], target)[0]["f1"]
            print(f"CV {config['name']:25s} {target:22s} natural F1={score:.3f} ({time.monotonic()-target_start:.1f}s)", flush=True)
    # Fixed Hb conditioning is an additional multiclass candidate, with no learned label rules.
    for name, config in list(configs.items()):
        alias = name + "_hb"
        configs[alias] = {**config, "name": alias, "hb_consistent": True, "base_name": name}
        for view in SCENARIOS:
            raw = cache[f"{name}__anemia_class__{view}"]
            cache[f"{alias}__anemia_class__{view}"] = hb_consistent(raw, views[view].iloc[dev], labels["anemia_class"])
    np.savez_compressed(output / "oof_probabilities.npz", **cache)
    rows, per_class = [], []
    for key, p in cache.items():
        name, target, view = key.split("__")
        result, classes, _ = metrics(data.iloc[dev][target].to_numpy(), p, labels[target], target)
        if target == "anemia_class":
            result.update(precision=np.nan, specificity=np.nan)
        folds = [metrics(data.iloc[dev[fold_ids == fold]][target].to_numpy(), p[fold_ids == fold], labels[target], target)[0]["f1"] for fold in range(old["folds"])]
        rows.append({"config": name, "target": target, "scenario": view, "partition": "development_oof",
                     "fold_f1_std": float(np.std(folds, ddof=1)), **result})
        per_class.extend({"config": name, "target": target, "scenario": view, "partition": "development_oof", **c} for c in classes)
    cv = pd.DataFrame(rows)
    cv.to_csv(output / "development_metrics.csv", index=False)
    selected = {}
    for target in TARGETS:
        selected[target] = {}
        for mode in ("primary", "cbc"):
            name, score, reason = choose(cv, configs, previous[target][mode], target, mode)
            selected[target].update({mode: name, f"{mode}_cv_score": score, f"{mode}_selection_reason": reason})
    dump(output / "selection.json", selected)
    print("Selection locked before repeated holdout", selected, flush=True)
    final_models, old_models = {}, {}
    for target in TARGETS:
        for name in set(selected[target][mode] for mode in ("primary", "cbc")):
            config = configs[name]
            base_name = config.get("base_name", name)
            if base_name in {c["name"] for c in CONFIGS_V2}:
                model = AdvancedClassifier(config, target, old["seed"]).fit(x.iloc[dev], data.iloc[dev][target].to_numpy())
            else:
                if base_name not in old_models:
                    old_models[base_name] = joblib.load(baseline / "models" / f"{base_name}.joblib")["models"]
                model = old_models[base_name][target]
            final_models[(name, target)] = model
    bundle = {"schema_version": "1.0", "model_family": "robust_v2", "models": final_models,
              "selected": selected, "configs": configs, "labels": labels, "features": FEATURES,
              "dictionary_sha256": old["dictionary_sha256"], "training_rows": dev.tolist(),
              "experiment": str(output), "research_only": True, "threshold": .5, "calibrated": False}
    joblib.dump(bundle, output / "models/selected.joblib", compress=3)
    test_cache = {}
    for target in TARGETS:
        for name in set(selected[target][mode] for mode in ("primary", "cbc")):
            for view in SCENARIOS:
                p = target_probabilities(final_models[(name, target)], views[view].iloc[holdout], configs[name], labels[target], target)
                test_cache[f"{name}__{target}__{view}"] = p
                result, classes, _ = metrics(data.iloc[holdout][target].to_numpy(), p, labels[target], target)
                rows.append({"config": name, "target": target, "scenario": view, "partition": "reused_holdout", **result})
                per_class.extend({"config": name, "target": target, "scenario": view, "partition": "reused_holdout", **c} for c in classes)
    pd.DataFrame(rows).to_csv(output / "metrics.csv", index=False)
    pd.DataFrame(per_class).to_csv(output / "per_class.csv", index=False)
    np.savez_compressed(output / "holdout_probabilities.npz", **test_cache)
    manifest.update(elapsed_seconds=time.monotonic()-started, finished_at=datetime.now().astimezone().isoformat())
    dump(output / "manifest.json", manifest)
    from .improvement_report import write_report
    write_report(output, baseline, data, views, dev, holdout)
    print(f"Finished {output} in {manifest['elapsed_seconds']:.1f}s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data/case/deficiency_anemia.csv")
    parser.add_argument("--baseline", type=Path, default=ROOT / "experiments/baseline_v1")
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/robust_v2")
    args = parser.parse_args()
    run(args.source.resolve(), args.baseline.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
