"""Experimental estimators: train-only preprocessing and patient-level masking."""

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import MissingIndicator, SimpleImputer
from sklearn.pipeline import FeatureUnion, Pipeline

from .core import CBC, FEATURES, LABS, PANELS, scenario

CONFIGS_V2 = [
    {"name": "augmented_catboost", "kind": "catboost", "panel": "extended", "augment": True},
    {"name": "balanced_catboost", "kind": "catboost", "panel": "extended", "augment": True, "balanced": True},
    {"name": "augmented_trees", "kind": "trees", "panel": "extended", "augment": True},
    {"name": "panel_expert", "kind": "catboost", "panel": "extended", "augment": True, "expert": True},
    {"name": "balanced_catboost_cbc", "kind": "catboost", "panel": "cbc", "augment": True, "balanced": True},
    {"name": "balanced_trees_cbc", "kind": "trees", "panel": "cbc", "augment": True, "balanced": True},
]
CONFIGS_V2 = [{"algorithm": "advanced", **config} for config in CONFIGS_V2]


def augment_patients(frame, y, seed):
    """All derived views belong only to the patients in the supplied training fold."""
    rng = np.random.default_rng(seed)
    views = [frame.copy()]
    for lo, hi in ((.15, .40), (.40, .70)):
        masked = frame.copy()
        for idx in frame.index:
            present = [key for key in LABS if pd.notna(frame.at[idx, key])]
            count = int(np.ceil(len(present) * rng.uniform(lo, hi)))
            if count:
                masked.loc[idx, rng.choice(present, count, replace=False)] = np.nan
        views.append(masked)
    views.append(scenario(frame, "cbc_only", seed))
    removed = frame.copy()
    panels = list(PANELS.values())
    for idx in frame.index:
        removed.loc[idx, panels[int(rng.integers(len(panels)))]] = np.nan
    views.append(removed)
    return pd.concat(views, ignore_index=True), np.tile(np.asarray(y), len(views))


def engineered(frame, config, target):
    if config["panel"] == "cbc":
        columns = CBC
    elif config.get("expert") and target != "anemia_class":
        group = {"iron_deficiency": "iron", "B12_deficiency": "B12", "folate_deficiency": "folate",
                 "B6_deficiency": "B6", "copper_deficiency": "copper", "inflammation_anemia": "inflammation"}[target]
        columns = list(dict.fromkeys([*CBC, *PANELS[group], "CRP", "ESR"]))
    else:
        columns = FEATURES
    result = frame.reindex(columns=columns).copy()
    threshold = result.sex.map({0.: 120., 1.: 130.})
    result["hemoglobin_margin"] = result.hemoglobin - threshold
    # Fixed arithmetic transforms, with no learned clinical thresholds or label inputs.
    for key in ("ferritin", "serum_iron", "vitamin_B12", "active_B12", "MMA", "homocysteine",
                "folate", "vitamin_B6", "copper", "CRP", "ESR", "reticulocytes", "LDH"):
        if key in result:
            result[f"log1p_{key}"] = np.log1p(result[key])
    for numerator, denominator in (("serum_iron", "TIBC"), ("hemoglobin", "RBC"), ("MCV", "MCH")):
        if numerator in result and denominator in result:
            result[f"ratio_{numerator}_{denominator}"] = result[numerator] / result[denominator].where(result[denominator] > 0)
    return result.replace([np.inf, -np.inf], np.nan)


class AdvancedClassifier:
    """Serializable wrapper; receives only whitelisted raw numeric features at inference."""
    def __init__(self, config, target, seed):
        self.config, self.target, self.seed = config, target, seed

    def fit(self, frame, y):
        if self.config.get("augment"):
            frame, y = augment_patients(frame, y, self.seed + 200)
        x = engineered(frame, self.config, self.target)
        labels, counts = np.unique(y, return_counts=True)
        weights = {label: float(np.sqrt(len(y) / (len(labels) * n))) for label, n in zip(labels, counts)} if self.config.get("balanced") else None
        if self.config["kind"] == "catboost":
            self.model = CatBoostClassifier(iterations=350, depth=4, learning_rate=.05, l2_leaf_reg=8,
                                            loss_function="MultiClass" if self.target == "anemia_class" else "Logloss",
                                            class_weights=weights, random_seed=self.seed, thread_count=4,
                                            verbose=False, allow_writing_files=False)
        else:
            preprocessing = FeatureUnion([("values", SimpleImputer(strategy="median", keep_empty_features=True)),
                                          ("missing", MissingIndicator(features="all"))])
            trees = ExtraTreesClassifier(n_estimators=300, max_depth=12, min_samples_leaf=3,
                                          max_features=.8, class_weight=weights, random_state=self.seed, n_jobs=4)
            self.model = Pipeline([("preprocess", preprocessing), ("model", trees)])
        self.model.fit(x, y)
        self.classes_ = self.model.classes_
        return self

    def predict_proba(self, frame):
        return self.model.predict_proba(engineered(frame, self.config, self.target))


def hb_consistent(probabilities, frame, labels):
    """Condition class scores on known Hb/sex using case-label semantics, not diagnosis."""
    normal_classes = {"no_anemia_no_deficiency", "latent_deficiency", "B12_deficiency_no_anemia", "folate_deficiency_no_anemia"}
    anemia_classes = {"iron_deficiency_anemia", "B12_deficiency_anemia", "folate_deficiency_anemia",
                      "inflammation_anemia", "mixed_deficiency", "anemia_other"}
    known = frame.sex.notna() & frame.hemoglobin.notna()
    anemia = frame.hemoglobin < frame.sex.map({0.: 120., 1.: 130.})
    allowed = np.ones_like(probabilities, dtype=bool)
    for col, label in enumerate(labels):
        if label in normal_classes:
            allowed[known.to_numpy() & anemia.to_numpy(), col] = False
        elif label in anemia_classes:
            allowed[known.to_numpy() & ~anemia.to_numpy(), col] = False
    result = probabilities * allowed
    empty = result.sum(axis=1) == 0
    result[empty] = allowed[empty].astype(float)
    return result / result.sum(axis=1, keepdims=True)
