import json
from pathlib import Path
import unittest
import tempfile
from copy import deepcopy

import joblib
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from auth_helpers import authenticate

from backend.app import create_app
from backend.model_service import DEFAULT_MODEL_BUNDLE, ModelService
from backend.privacy import POLICY_VERSION
from ml_baselines.calibration import ScoreCalibrator, calibrate_scores, probability_metrics
from ml_baselines.calibrate import external_subset, ROOT
from ml_baselines.predict import predict
from scripts.prepare_nhanes import convert


class CalibrationTests(unittest.TestCase):
    def test_temperature_preserves_rank_and_normalization(self):
        p = np.array([[.1, .7, .2], [.5, .2, .3]])
        q = ScoreCalibrator("temperature", 2).transform(p)
        np.testing.assert_array_equal(q.argmax(axis=1), p.argmax(axis=1))
        np.testing.assert_allclose(q.sum(axis=1), 1)
        self.assertTrue(np.all(q >= 0))

    def test_sigmoid_fits_unseen_synthetic_scores_monotonically(self):
        score = np.linspace(.02, .98, 400)
        y = (np.random.default_rng(10).random(400) < score**2).astype(int)
        p = np.column_stack([1-score, score])
        calibrator = ScoreCalibrator.fit("sigmoid_logit", p, y, [0,1])
        q = calibrator.transform(p)
        self.assertGreater(calibrator.slope, 0)
        self.assertTrue(np.all(np.diff(q[:,1]) >= 0))
        self.assertLess(probability_metrics(y, q, [0,1])["log_loss"], probability_metrics(y, p, [0,1])["log_loss"])
        with self.assertRaises(ValueError):
            ScoreCalibrator.fit("sigmoid_logit", p, np.zeros(400), [0,1])

    def test_inflammation_normal_hb_remains_zero_after_calibration(self):
        frame = pd.DataFrame({"sex": [0., 1., np.nan], "hemoglobin": [140., 100., 140.]})
        q = calibrate_scores(ScoreCalibrator("sigmoid_logit", 1, 2), np.array([[1,0],[.2,.8],[.3,.7]]), frame, "inflammation_anemia")
        np.testing.assert_array_equal(q[0], [1,0])
        self.assertGreater(q[2,1], 0)

    def test_verified_transfer_keeps_unknowns_and_deduplicates(self):
        frame, _ = external_subset(ROOT / "data/external/processed/kilicarslan")
        self.assertTrue(frame.duplicate_group_id.is_unique)
        self.assertFalse(frame.needs_quality_review.any())
        self.assertTrue(frame[["age_years","sex","vitamin_B12","RBC","WBC","TIBC","TSAT","copper_deficiency","B6_deficiency","anemia_class"]].isna().all().all())
        self.assertEqual(set(frame.iron_deficiency.dropna()), {1})

    def test_documented_units_and_unknown_categorical_codes(self):
        def rule(factor):
            return {"unit_conversion_allowed": True, "unit_status": "documented", "factor_to_case": factor, "offset_to_case": 0}
        np.testing.assert_allclose(convert(pd.Series([12., np.nan]), rule(10)), [120.,np.nan])
        self.assertAlmostEqual(convert(pd.Series([250.]), rule(.001))[0], .25)
        self.assertAlmostEqual(convert(pd.Series([.5]), rule(10))[0], 5.)
        with self.assertRaises(ValueError):
            convert(pd.Series([1]), {**rule(1), "unit_status": "assumed"})
        with self.assertRaises(ValueError):
            convert(pd.Series([3]), {**rule(1), "categorical_mapping": {"1":"M","2":"F"}})


@unittest.skipUnless((ROOT / "experiments/calibrated_v3/split_audit.json").exists(), "Run nested experiment first")
class CalibrationArtifactTests(unittest.TestCase):
    def test_every_inner_and_outer_patient_split_is_disjoint(self):
        audit = json.loads((ROOT / "experiments/calibrated_v3/split_audit.json").read_text())
        splits = pd.read_csv(ROOT / "experiments/calibrated_v3/splits.csv")
        heldout = set(splits.loc[splits.partition.eq("holdout"), "row_index"])
        self.assertEqual(len(audit), 14*5*3)
        for split in audit:
            learn, calibrate, valid = (set(split[k]) for k in ["base_training_rows","calibration_rows","outer_validation_rows"])
            self.assertFalse(learn & calibrate)
            self.assertFalse(learn & valid)
            self.assertFalse(calibrate & valid)
            self.assertFalse((learn | calibrate | valid) & heldout)

    def test_nhanes_has_no_inferred_diagnoses(self):
        folder = ROOT / "data/external/processed/nhanes_case_units_v1"
        for cycle, expected in [("nhanes_2003_2004",5351),("nhanes_2011_2012",5807)]:
            features = pd.read_csv(folder / f"{cycle}_features.csv")
            labels = pd.read_csv(folder / f"{cycle}_unknown_labels.csv")
            masks = pd.read_csv(folder / f"{cycle}_target_masks.csv")
            self.assertEqual(len(features), expected)
            self.assertTrue(features.patient_id.is_unique)
            self.assertTrue(labels.drop(columns=["patient_id","anemia_by_case_rule"]).isna().all().all())
            self.assertEqual(masks.filter(like="known_").sum().sum(), 0)
            self.assertTrue(features.indirect_bilirubin.isna().all())

    def test_runtime_uses_saved_scalers_and_retained_raw_heads_in_both_routes(self):
        candidate = ROOT / "experiments/calibrated_v3/models/selected.joblib"
        bundle = joblib.load(candidate)
        previous = joblib.load(DEFAULT_MODEL_BUNDLE)
        rows = pd.read_csv(ROOT / "experiments/calibrated_v3/splits.csv")
        self.assertEqual(set(bundle["training_rows"]), set(rows.loc[rows.partition.eq("development"), "row_index"]))
        extended = {"age_years":42, "sex":"F", "hemoglobin":108., "RBC":4., "MCV":78., "MCH":25., "RDW":17., "ferritin":9., "folate":12.}
        cbc = {k:v for k,v in extended.items() if k not in {"ferritin","folate"}}
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(data_dir=Path(folder), model_bundle=candidate, ocr_mode="off")
            with TestClient(app, base_url="http://localhost") as client:
                authenticate(app, client)
                acknowledgement = client.post("/api/privacy/acknowledge", json={"dataKind":"synthetic", "researchOnly":True, "policyVersion":POLICY_VERSION})
                self.assertEqual(acknowledgement.status_code, 200, acknowledgement.text)
                status = client.get("/api/models/status").json()
                self.assertTrue(status["connected"], status)
                self.assertEqual(status["modelFamily"], "calibrated_v3")
                self.assertFalse(status["calibrated"])
                self.assertTrue(status["calibrationApplied"])
                self.assertEqual(len(status["calibration"]["appliedHeads"]), 10)
                self.assertFalse(status["calibration"]["clinicalValidated"])
                for inputs, route in [(extended,"primary"),(cbc,"cbc")]:
                    raw, result = predict(previous, inputs), predict(bundle, inputs)
                    for target in bundle["labels"]:
                        p = np.array(list(raw["scores"][target]["values"].values()))[None,:]
                        expected = bundle["calibrators"][(target,route)].transform(p)[0]
                        np.testing.assert_allclose(list(result["scores"][target]["values"].values()), expected, rtol=1e-10)
                    response = client.post("/api/doctor/predict", json={"inputs":inputs})
                    self.assertEqual(response.status_code, 200, response.text)
                    report = response.json()
                    self.assertEqual(report["modelPanel"], route)
                    self.assertTrue(report["calibrationApplied"])
                    self.assertEqual(report["scoreMeaning"], "partially_calibrated_research_score")
                    self.assertFalse(report["clinicalUseEnabled"])
                    for target, code in [("iron_deficiency","iron"),("B12_deficiency","B12"),("folate_deficiency","folate")]:
                        item = next(v for v in report["deficiencyProbabilities"] if v["code"] == code)
                        self.assertAlmostEqual(item["probability"], result["scores"][target]["values"]["1"], places=12)
                    self.assertEqual(client.get("/api/reports/"+report["reportId"]).json(), report)
                sparse = client.post("/api/doctor/predict", json={"inputs":{"age_years":42,"sex":"F","hemoglobin":108}}).json()
                self.assertEqual(sparse["modelDecisionState"], "suppressed_sparse")
                self.assertEqual(sparse["deficiencyProbabilities"], [])
                no_anemia = predict(bundle, {**extended, "hemoglobin":140})
                self.assertEqual(no_anemia["scores"]["inflammation_anemia"]["values"]["1"], 0.)

    def test_incomplete_or_misleading_calibration_bundle_is_rejected(self):
        bundle = joblib.load(ROOT / "experiments/calibrated_v3/models/selected.joblib")
        broken = deepcopy(bundle)
        broken["calibrators"].pop(("iron_deficiency","primary"))
        with self.assertRaises(ValueError):
            ModelService._validate_bundle(broken)
        broken = deepcopy(bundle)
        broken["calibrators"][("iron_deficiency","primary")].slope = float("nan")
        with self.assertRaises(ValueError):
            ModelService._validate_bundle(broken)
        broken = deepcopy(bundle)
        broken["calibrated"] = True
        with self.assertRaises(ValueError):
            ModelService._validate_bundle(broken)
