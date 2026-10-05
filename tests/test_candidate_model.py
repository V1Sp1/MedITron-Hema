import json
import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
from fastapi.testclient import TestClient
from auth_helpers import authenticate

from backend.app import create_app
from backend.features import ROOT
from ml_baselines.predict import predict

CANDIDATE = ROOT / "experiments/robust_v2/models/selected.joblib"


@unittest.skipUnless(CANDIDATE.exists(), "Train the optional robust_v2 candidate first")
class CandidateModelTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / 'experiments/robust_v2/splits.csv').exists(), 'Row-level training artifacts intentionally excluded from research handoff')
    def test_weights_preserve_holdout_isolation_and_frozen_selection(self):
        bundle = joblib.load(CANDIDATE)
        import pandas as pd
        splits = pd.read_csv(ROOT / "experiments/robust_v2/splits.csv")
        development = set(splits.loc[splits.partition.eq("development"), "row_index"])
        heldout = set(splits.loc[splits.partition.eq("holdout"), "row_index"])
        self.assertEqual(set(bundle["training_rows"]), development)
        self.assertFalse(set(bundle["training_rows"]) & heldout)
        selection = json.loads((CANDIDATE.parents[1] / "selection.json").read_text())
        self.assertEqual(bundle["selected"], selection)
        release = json.loads((CANDIDATE.parents[1] / "release_decision.json").read_text())
        self.assertFalse(release["promote_default"])

    def test_candidate_runtime_reports_distinct_family_and_constrained_scores(self):
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_app(data_dir=Path(folder), model_bundle=CANDIDATE), base_url="http://localhost") as client:
                authenticate(client.app, client)
                status = client.get("/api/models/status").json()
                self.assertTrue(status["connected"], status)
                self.assertEqual(status["modelFamily"], "robust_v2")
                self.assertTrue(status["modelVersion"].startswith("hema-robust-v2-"))
                inputs = {"sex": "F", "age_years": 42, "hemoglobin": 108, "RBC": 4, "MCV": 78,
                          "MCH": 25, "RDW": 17, "ferritin": 9, "vitamin_B12": 340, "folate": 12}
                raw = predict(joblib.load(CANDIDATE), inputs)
                probabilities = raw["scores"]["anemia_class"]["values"]
                self.assertEqual(probabilities["no_anemia_no_deficiency"], 0)
                self.assertEqual(probabilities["B12_deficiency_no_anemia"], 0)
                self.assertAlmostEqual(sum(probabilities.values()), 1)
                results = []
                for role in ("doctor", "patient"):
                    response = client.post(f"/api/{role}/predict", json={"inputs": inputs})
                    self.assertEqual(response.status_code, 200, response.text)
                    report = response.json();results.append(report)
                    self.assertEqual(report["modelFamily"], "robust_v2")
                    self.assertTrue(report["classScoresConditionedOnHb"])
                    self.assertFalse(report["calibrated"])
                    self.assertIsNone(report["inputs"]["copper"])
                    self.assertEqual(client.get('/api/reports/'+report["reportId"]).json(), report)
                    for item in report["deficiencyProbabilities"]:
                        self.assertTrue(np.isfinite(item["probability"]))
                self.assertEqual(results[0]["modelVerdict"], results[1]["modelVerdict"])
                self.assertEqual(results[0]["prediction"], results[1]["prediction"])
                cbc = {k: v for k, v in inputs.items() if k not in {"ferritin", "vitamin_B12", "folate"}}
                report = client.post('/api/doctor/predict', json={"inputs": cbc}).json()
                self.assertEqual(report["modelPanel"], "cbc")
                self.assertFalse(report["classScoresConditionedOnHb"])
