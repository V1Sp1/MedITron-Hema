from auth_helpers import authenticate
"""Real trained weights, missing panels and honest startup/inference failures."""

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.model_service import DEFAULT_MODEL_BUNDLE, TARGET_CODES, ModelService
from ml_baselines.predict import predict

CBC = {"age_years": 42, "sex": "F", "hemoglobin": 108, "RBC": 4.0,
       "hematocrit": 33, "MCV": 78, "MCH": 25, "MCHC": 320,
       "RDW": 17, "platelets": 320, "WBC": 6.5}
EXTENDED = {**CBC, "ferritin": 9, "TSAT": 10, "CRP": 1,
            "vitamin_B12": 340, "folate": 12}


class ModelAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        # No skipped tests or mock classifier: the actual default artifact must load.
        cls.app = create_app(data_dir=cls.root, ocr_mode="off")
        cls.client = TestClient(cls.app, base_url="http://localhost")
        cls.client.__enter__()
        authenticate(cls.app, cls.client)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.temp.cleanup()

    def request(self, inputs, role="doctor"):
        response = self.client.post(f"/api/{role}/predict", json={"inputs": inputs})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_default_bundle_loaded_and_versioned(self):
        status = self.client.get("/api/models/status").json()
        self.assertTrue(status["connected"], status)
        self.assertEqual(status["state"], "ready")
        self.assertFalse(status["calibrated"])
        self.assertTrue(status["modelVersion"].startswith("hema-baseline-v1-"))
        self.assertEqual(status["selectedModels"]["anemia_class"]["primary"], "extra_trees")
        health = self.client.get("/api/health").json()
        self.assertEqual(health["modelVersion"], status["modelVersion"])
        self.assertTrue(health["modelConnected"])

    def test_panel_routing_scores_equal_research_inference_and_roles(self):
        for inputs, panel in [(CBC, "cbc"), (EXTENDED, "primary")]:
            raw = predict(self.app.state.model.bundle, inputs)
            doctor = self.request(inputs)
            patient = self.request(inputs, "patient")
            self.assertEqual(doctor["modelPanel"], panel)
            self.assertEqual(doctor["source"], "api")
            for key in ("prediction", "modelVerdict", "selectedModels"):
                self.assertEqual(doctor[key], patient[key])
            # Parallel tree summation can vary in the final floating-point bit.
            for key in ("deficiencyProbabilities", "anemiaProbabilities"):
                self.assertEqual([i["code"] for i in doctor[key]], [i["code"] for i in patient[key]])
                for left, right in zip(doctor[key], patient[key]):
                    self.assertAlmostEqual(left["probability"], right["probability"], places=12)
            for item in doctor["deficiencyProbabilities"]:
                target = next(t for t, code in TARGET_CODES.items() if code == item["code"])
                self.assertAlmostEqual(item["probability"], raw["scores"][target]["values"]["1"], places=12)
            self.assertAlmostEqual(sum(i["probability"] for i in doctor["anemiaProbabilities"]), 1)
            self.assertIsNone(doctor["inputs"]["vitamin_B6"])

    def test_hb_only_abstains_and_recommendations_suppressed(self):
        report = self.request({"age_years": 42, "sex": "F", "hemoglobin": 108})
        self.assertTrue(report["anemia"])
        self.assertTrue(report["modelConnected"])
        self.assertEqual(report["modelDecisionState"], "suppressed_sparse")
        self.assertIsNone(report["prediction"]["code"])
        self.assertIsNone(report["deficiencies"])
        self.assertEqual(report["deficiencyProbabilities"], [])
        recommendations = self.client.post("/api/recommendations", json={"reportId": report["reportId"], "audience": "doctor"}).json()
        self.assertEqual(recommendations["verdictContext"]["modelState"], "suppressed_sparse")
        self.assertTrue(recommendations["insufficientData"]["active"])
        self.assertFalse({"iron", "B12", "folate", "B6", "copper", "mixed", "inflammation"} & {i["topic"] for i in recommendations["items"]})

    def test_real_positive_verdict_activates_matching_draft_phrases(self):
        report = self.request(EXTENDED)
        self.assertEqual(report["modelDecisionState"], "evaluated")
        self.assertEqual(report["prediction"]["code"], "iron_deficiency_anemia")
        self.assertEqual(report["deficiencies"], ["iron"])
        self.assertEqual(report["modelVerdict"]["deficits"], ["iron"])
        phrases = report["recommendationSnapshot"]
        self.assertIn("iron", {item["topic"] for item in phrases["items"]})
        self.assertEqual(phrases["status"], "draft")

    def test_all_12_class_branches_match_structured_decisions_in_both_roles(self):
        # Controlled scores exercise the production adapter without retraining or
        # selecting examples from holdout to tune the decision policy.
        template = predict(self.app.state.model.bundle, EXTENDED)
        cases = [
            ("no_anemia_no_deficiency", 131, [], False, None),
            ("latent_deficiency", 131, ["iron"], False, "iron"),
            ("iron_deficiency_anemia", 108, ["iron"], False, "iron"),
            ("B12_deficiency_anemia", 108, ["B12"], False, "B12"),
            ("B12_deficiency_no_anemia", 131, ["B12"], False, "B12"),
            ("folate_deficiency_anemia", 108, ["folate"], False, "folate"),
            ("folate_deficiency_no_anemia", 131, ["folate"], False, "folate"),
            ("B6_deficiency", 108, ["B6"], False, "B6"),
            ("copper_deficiency", 131, ["copper"], False, "copper"),
            ("inflammation_anemia", 108, [], True, "inflammation"),
            ("mixed_deficiency", 108, ["iron", "B12"], False, "mixed"),
            ("anemia_other", 108, [], False, "other"),
        ]
        specific = {"iron", "B12", "folate", "B6", "copper", "inflammation", "mixed", "other"}
        for kind, hb, deficits, inflammation, topic in cases:
            raw = deepcopy(template)
            raw["scores"]["anemia_class"]["values"] = {code: float(code == kind)
                                                          for code in raw["scores"]["anemia_class"]["values"]}
            for target, code in TARGET_CODES.items():
                positive = float(code in deficits)
                raw["scores"][target]["values"] = {"0": 1-positive, "1": positive}
            raw["scores"]["inflammation_anemia"]["values"] = {"0": float(not inflammation), "1": float(inflammation)}
            for role in ["patient", "doctor"]:
                with self.subTest(kind=kind, role=role), patch.object(self.app.state.model, "infer", return_value=raw):
                    report = self.request({**EXTENDED, "hemoglobin": hb}, role)
                    self.assertEqual(report["modelDecisionState"], "evaluated")
                    phrases = report["recommendationSnapshot"]
                    active = {i["topic"] for i in phrases["items"]}
                    self.assertEqual(active & specific, {topic} if topic else set())
                    self.assertTrue(all(i["id"].startswith(role+'.') for i in phrases["items"]))
                    self.assertNotIn("model_absent", {i["topic"] for i in phrases["conclusions"]})

    def test_other_class_with_positive_target_is_suppressed_and_reason_saved(self):
        raw = predict(self.app.state.model.bundle, EXTENDED)
        raw["scores"]["anemia_class"]["values"] = {code: float(code == "anemia_other")
                                                      for code in raw["scores"]["anemia_class"]["values"]}
        with patch.object(self.app.state.model, "infer", return_value=raw):
            report = self.request(EXTENDED)
        self.assertEqual(report["modelDecisionState"], "inconsistent")
        self.assertIn("class_deficits_mismatch", report["decisionReasonCodes"])
        phrases = report["recommendationSnapshot"]
        self.assertTrue({"iron", "other"}.isdisjoint({i["topic"] for i in phrases["items"]}))
        limited = next(i for i in phrases["conclusions"] if i["topic"] == "model_limited")
        self.assertIn("class_deficits_mismatch", limited["reasonCodes"])

    def test_normal_hb_cannot_be_overridden_and_reports_expire_on_restart(self):
        report = self.request({**EXTENDED, "hemoglobin": 120})
        self.assertFalse(report["anemia"])
        self.assertEqual(report["anemiaProbabilities"], [])
        self.assertFalse(report["modelVerdict"]["inflammation"])
        self.assertEqual(self.client.get(f'/api/reports/{report["reportId"]}').json(), report)
        with TestClient(create_app(data_dir=self.root, model_bundle=None), base_url="http://localhost") as restarted:
            restarted.cookies.update(self.client.cookies)
            self.assertEqual(restarted.get(f'/api/reports/{report["reportId"]}').status_code, 404)

    def test_uncertain_or_inconsistent_class_does_not_trigger_specific_phrases(self):
        raw = predict(self.app.state.model.bundle, EXTENDED)
        raw["scores"]["anemia_class"]["values"] = {code: float(code == "B12_deficiency_no_anemia")
                                                         for code in raw["scores"]["anemia_class"]["values"]}
        with patch.object(self.app.state.model, "infer", return_value=raw):
            report = self.request(EXTENDED)
        self.assertEqual(report["modelDecisionState"], "inconsistent")
        self.assertIsNone(report["prediction"]["code"])
        self.assertEqual(report["modelVerdict"]["status"], "uncertain")
        self.assertEqual(report["recommendationSnapshot"]["verdictContext"]["modelState"], "inconsistent")

    def test_inference_failure_returns_503_and_creates_no_report(self):
        before = self.app.state.store.db.execute("SELECT COUNT(*) FROM objects WHERE kind='report'").fetchone()[0]
        with patch.object(self.app.state.model, "infer", side_effect=RuntimeError("test failure")):
            response = self.client.post("/api/doctor/predict", json={"inputs": EXTENDED})
        self.assertEqual(response.status_code, 503)
        after = self.app.state.store.db.execute("SELECT COUNT(*) FROM objects WHERE kind='report'").fetchone()[0]
        self.assertEqual(before, after)

    def test_missing_or_incompatible_weights_truthful_and_no_silent_rule_fallback(self):
        import joblib
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bad = root / "bad.joblib"
            joblib.dump({"schema_version": "unsupported"}, bad)
            for path in (root / "missing.joblib", bad):
                with TestClient(create_app(data_dir=root, model_bundle=path), base_url="http://localhost") as client:
                    authenticate(client.app, client)
                    health = client.get("/api/health").json()
                    self.assertFalse(health["modelConnected"])
                    self.assertEqual(health["status"], "degraded")
                    self.assertEqual(client.get("/api/models/status").json()["errorCode"], "untrusted_weights" if path.exists() else "load_failed")
                    self.assertEqual(client.post("/api/doctor/predict", json={"inputs": EXTENDED}).status_code, 503)

    def test_schema_and_dictionary_mismatch_rejected(self):
        bundle = self.app.state.model.bundle
        for changes in ({"features": list(reversed(bundle["features"]))}, {"dictionary_sha256": "wrong"},
                        {"labels": {**bundle["labels"], "iron_deficiency": [1, 0]}}):
            with self.assertRaises(ValueError):
                ModelService._validate_bundle({**bundle, **changes})
