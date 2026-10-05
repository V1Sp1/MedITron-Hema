import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from ml_baselines.advanced import AdvancedClassifier, CONFIGS_V2, augment_patients, engineered, hb_consistent
from ml_baselines.core import FEATURES, encode
from ml_baselines.improve import choose
from ml_baselines.improvement_report import RELEASE_POLICY, release_checks


class ImprovementTests(unittest.TestCase):
    def test_augmented_views_use_only_training_patients_and_observed_values(self):
        frame = encode(pd.DataFrame([{"age_years": 31, "sex": "F", "hemoglobin": 110, "ferritin": 0},
                                     {"age_years": 65, "sex": "M", "hemoglobin": 140, "vitamin_B12": 300}]))
        out, y = augment_patients(frame, np.array([1, 0]), 42)
        self.assertEqual(out.shape, (10, len(FEATURES)))
        np.testing.assert_array_equal(y, [1, 0] * 5)
        for j in range(len(out)):
            original = frame.iloc[j % 2]
            for key in FEATURES:
                if pd.notna(out.iloc[j][key]):
                    self.assertEqual(out.iloc[j][key], original[key])
        again, _ = augment_patients(frame, np.array([1, 0]), 42)
        pd.testing.assert_frame_equal(out, again)
        self.assertEqual(out.iloc[0].ferritin, 0)

    def test_engineering_propagates_missing_and_does_not_admit_labels(self):
        frame = encode(pd.DataFrame([{"sex": "F", "hemoglobin": 110, "RBC": 0, "ferritin": 0},
                                     {"sex": None, "hemoglobin": 130, "ferritin": None}]))
        frame["anemia_class"] = "never_a_feature"
        result = engineered(frame, CONFIGS_V2[0], "anemia_class")
        self.assertEqual(result.loc[0, "hemoglobin_margin"], -10)
        self.assertEqual(result.loc[0, "log1p_ferritin"], 0)
        self.assertTrue(pd.isna(result.loc[0, "ratio_hemoglobin_RBC"]))
        self.assertTrue(pd.isna(result.loc[1, "hemoglobin_margin"]))
        self.assertTrue(pd.isna(result.loc[1, "log1p_ferritin"]))
        self.assertNotIn("anemia_class", result)
        expert = engineered(frame, next(c for c in CONFIGS_V2 if c["name"] == "panel_expert"), "B6_deficiency")
        self.assertIn("vitamin_B6", expert)
        self.assertNotIn("ferritin", expert)

    def test_hb_constraint_boundaries_unknown_values_and_ambiguous_classes(self):
        labels = ["iron_deficiency_anemia", "no_anemia_no_deficiency", "B6_deficiency", "copper_deficiency"]
        frame = encode(pd.DataFrame([{"sex": "F", "hemoglobin": 120}, {"sex": "M", "hemoglobin": 129.9},
                                     {"sex": None, "hemoglobin": 100}, {"sex": "F", "hemoglobin": None}]))
        p = np.full((4, 4), .25)
        out = hb_consistent(p, frame, labels)
        self.assertEqual(out[0, 0], 0)
        self.assertEqual(out[1, 1], 0)
        self.assertTrue((out[:, 2:] > 0).all())
        np.testing.assert_allclose(out[2:], p[2:])
        np.testing.assert_allclose(out.sum(axis=1), 1)
        fallback = hb_consistent(np.array([[1., 0, 0, 0]]), frame.iloc[:1], labels)
        self.assertEqual(fallback[0, 0], 0)
        self.assertTrue(np.isfinite(fallback).all())
        np.testing.assert_allclose(fallback.sum(), 1)

    def test_preprocessing_learns_train_only_and_saved_estimator_replays(self):
        frame = encode(pd.DataFrame([{"sex": "F", "age_years": 31, "hemoglobin": 110, "ferritin": 10},
                                     {"sex": "M", "age_years": 65, "hemoglobin": 140, "ferritin": 20},
                                     {"sex": "F", "age_years": 35, "hemoglobin": 125}]))
        config = {**next(c for c in CONFIGS_V2 if c["kind"] == "trees"), "augment": False}
        model = AdvancedClassifier(config, "iron_deficiency", 42).fit(frame, np.array([1, 0, 0]))
        columns = engineered(frame, config, "iron_deficiency").columns
        imputer = model.model.named_steps["preprocess"].transformer_list[0][1]
        index = list(columns).index("ferritin")
        self.assertEqual(imputer.statistics_[index], 15)
        validation = frame.copy();validation["ferritin"] = 10000
        prediction = model.predict_proba(validation)
        self.assertEqual(imputer.statistics_[index], 15)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.joblib"
            joblib.dump(model, path)
            np.testing.assert_allclose(joblib.load(path).predict_proba(validation), prediction)

    def test_selection_rejects_precision_regression_and_tiny_gains(self):
        rows = []
        for name, f1, precision, specificity in [("old", .6, .9, .98), ("false_positives", .8, .6, .9), ("tiny", .605, .9, .98), ("better", .7, .9, .98)]:
            for view in ["available", "drop30", "drop60", "cbc_only"]:
                rows.append({"config": name, "target": "folate_deficiency", "scenario": view,
                             "f1": f1, "precision": precision, "specificity": specificity})
        cv = pd.DataFrame(rows)
        configs = {name: {"panel": "cbc"} for name in cv.config.unique()}
        selected, score, _ = choose(cv, configs, "old", "folate_deficiency", "primary")
        self.assertEqual(selected, "better")
        self.assertAlmostEqual(score, .7)
        without = cv[~cv.config.eq("better")]
        self.assertEqual(choose(without, configs, "old", "folate_deficiency", "cbc")[0], "old")

    def test_release_guard_blocks_regression_without_changing_selection(self):
        from ml_baselines.core import TARGETS
        rows = [{"partition": part, "mode": mode, "scenario": view, "target": target, "delta_f1": .02}
                for part in ("development_oof", "reused_holdout") for target in TARGETS
                for mode, views in (("primary", ["available", "drop30", "drop60"]), ("cbc", ["cbc_only"])) for view in views]
        comparison = pd.DataFrame(rows)
        deployment = pd.DataFrame([{"partition": "reused_holdout", "scenario": "available", "version": version,
                                    "accepted_class_accuracy": .96, "coverage": coverage}
                                   for version, coverage in (("baseline_v1", .50), ("robust_v2", .60))])
        self.assertTrue(release_checks(comparison, deployment, RELEASE_POLICY)["promote_default"])
        mask = comparison.partition.eq("reused_holdout") & comparison.target.eq("anemia_class") & comparison.scenario.eq("available")
        comparison.loc[mask, "delta_f1"] = -.04
        result = release_checks(comparison, deployment, RELEASE_POLICY)
        self.assertFalse(result["promote_default"])
        self.assertFalse(result["candidate_selection_changed"])
