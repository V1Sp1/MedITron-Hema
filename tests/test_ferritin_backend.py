"""Actual locked weights in the API; marker forecasts never substitute diagnoses."""
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from auth_helpers import authenticate
from backend.app import create_app
from backend.ferritin_service import FerritinService
from backend.screening import screen
from ml_baselines.biochemical import predict_biochemical

CBC = {"age_years": 40, "sex": "F", "hemoglobin": 110., "RBC": 4., "hematocrit": 33.,
       "MCV": 82., "MCH": 27.5, "MCHC": 333., "RDW": 17., "platelets": 350., "WBC": 7.}
EXTENDED = {**CBC, "CRP": 3., "creatinine": 70., "albumin": 40., "LDH": 180.}


class FerritinServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = FerritinService()
        assert cls.service.bundle is not None, cls.service.status()

    def test_both_routes_match_cli_scores_and_locked_thresholds(self):
        for inputs, route in [(CBC, "cbc"), (EXTENDED, "extended")]:
            actual = self.service.evaluate(inputs, "not_pregnant")
            ref = predict_biochemical(self.service.bundle, inputs, "not_pregnant")["outcomes"]["low_ferritin"]
            self.assertEqual(actual["route"], route)
            self.assertAlmostEqual(actual["score"], ref["score"], places=14)
            self.assertEqual(actual["decisionThreshold"], ref["threshold"])
            self.assertEqual(actual["screenPositive"], ref["research_screen_positive"])
            self.assertFalse(actual["clinicalValidated"])
            self.assertFalse(actual["markerUsedAsPredictor"])

    def test_measured_ferritin_never_calls_model_even_outside_scope(self):
        for marker in (0., 9., 100.):
            with patch.object(self.service.bundle["heads"][("low_ferritin", "cbc")], "predict_score", side_effect=AssertionError("No measured-marker inference")):
                value = self.service.evaluate({**CBC, "sex": "M", "age_years": 60, "ferritin": marker})
            self.assertEqual(value["status"], "observed")
            self.assertEqual(value["observedValue"], marker)
            self.assertNotIn("score", value)
            self.assertNotIn("screenPositive", value)

    def test_population_boundaries_and_unknown_pregnancy(self):
        for age in (18, 44):
            self.assertEqual(self.service.evaluate({**CBC, "age_years": age})["status"], "unknown_pregnancy")
            self.assertEqual(self.service.evaluate({**CBC, "age_years": age}, "not_pregnant")["status"], "research_prediction")
        for age in (45, 49):
            self.assertEqual(self.service.evaluate({**CBC, "age_years": age})["status"], "research_prediction")
        for inputs, pregnancy in [({**CBC, "age_years": 17}, "not_pregnant"), ({**CBC, "age_years": 50}, "not_pregnant"),
                                   ({**CBC, "sex": "M"}, "not_pregnant"), (CBC, "pregnant")]:
            result = self.service.evaluate(inputs, pregnancy)
            self.assertEqual(result["status"], "outside_scope")
            self.assertNotIn("score", result)

    def test_supported_lab_count_and_nutrient_panels_never_select_route(self):
        sparse = {"age_years": 40, "sex": "F", "hemoglobin": 110, "MCV": 82, "MCH": 27, "RBC": 4,
                  "vitamin_B12": 300, "folate": 12, "TSAT": 20, "Ret_He": 28}
        result = self.service.evaluate(sparse, "not_pregnant")
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["availableLabCount"], 4)
        result = self.service.evaluate({**sparse, "RDW": 17}, "not_pregnant")
        self.assertEqual(result["route"], "cbc")
        self.assertEqual(result["availableLabCount"], 5)
        ref = self.service.evaluate({k: v for k, v in {**sparse, "RDW": 17}.items() if k in CBC}, "not_pregnant")
        self.assertAlmostEqual(result["score"], ref["score"], places=14)

    def test_runtime_failure_has_no_score_or_clinical_verdict(self):
        with patch.object(self.service.bundle["heads"][("low_ferritin", "cbc")], "predict_score", side_effect=RuntimeError("failure")):
            result = self.service.apply(screen(CBC, "doctor"), "not_pregnant")
        self.assertEqual(result["ferritinScreening"]["status"], "unavailable")
        self.assertNotIn("score", result["ferritinScreening"])
        self.assertIsNone(result["modelVerdict"])

    def test_disabled_untrusted_and_failed_gate_are_explicit(self):
        self.assertEqual(FerritinService(None).evaluate(CBC, "not_pregnant")["status"], "disabled")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "unknown.joblib"
            path.write_bytes(b"untrusted serialization")
            with patch("ml_baselines.biochemical_predict.joblib.load") as deserialize:
                self.assertEqual(FerritinService(path).status()["state"], "unavailable")
                deserialize.assert_not_called()
        bundle = deepcopy(self.service.bundle)
        bundle["validation"]["low_ferritin/cbc"]["passed"] = False
        with patch("ml_baselines.biochemical_predict.load_candidate", return_value=bundle):
            self.assertIsNone(FerritinService().bundle)


class FerritinAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.app = create_app(data_dir=Path(cls.temp.name), ocr_mode="off")
        cls.client = TestClient(cls.app, base_url="http://localhost")
        cls.client.__enter__()
        authenticate(cls.app, cls.client)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.temp.cleanup()

    def request(self, inputs=CBC, pregnancy="not_pregnant", audience="doctor"):
        response = self.client.post(f"/api/{audience}/predict", json={"inputs": inputs, "pregnancyStatus": pregnancy})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_default_configuration_and_both_roles(self):
        status = self.client.get("/api/models/status").json()
        self.assertEqual(status["modelConfiguration"], "baseline_v1_plus_ferritin_v4")
        self.assertEqual(status["ferritinScreening"]["state"], "ready")
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        for role in ("patient", "doctor"):
            report = self.request(audience=role)
            self.assertEqual(report["ferritinScreening"]["status"], "research_prediction")
            self.assertNotIn("pregnancyStatus", report["inputs"])
            self.assertEqual(report["pregnancyStatus"], "not_pregnant")
            self.assertFalse(report["clinicalUseEnabled"])
            self.assertNotIn("low_B12", report)
            self.assertNotIn("low_PLP", report)

    def test_v4_does_not_change_v1_decisions_or_phrases(self):
        a, b = self.request(pregnancy="unknown"), self.request()
        self.assertEqual(a["ferritinScreening"]["status"], "unknown_pregnancy")
        self.assertEqual(b["ferritinScreening"]["status"], "research_prediction")
        for key in ("modelVerdict", "prediction", "deficiencies", "decisionReasonCodes", "recommendationSnapshot"):
            self.assertEqual(a[key], b[key], key)
        for key in ("deficiencyScores", "anemiaScores"):
            for left, right in zip(a[key], b[key]):
                self.assertAlmostEqual(left["score"], right["score"], places=12)

    def test_observed_value_is_saved_without_a_prediction(self):
        report = self.request({**EXTENDED, "ferritin": 9}, pregnancy="unknown")
        self.assertEqual(report["ferritinScreening"]["status"], "observed")
        self.assertNotIn("score", report["ferritinScreening"])
        self.assertEqual(self.client.get(f'/api/reports/{report["reportId"]}').json(), report)

    def test_default_unknown_and_invalid_context(self):
        response = self.client.post("/api/patient/predict", json={"inputs": CBC})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ferritinScreening"]["status"], "unknown_pregnancy")
        for pregnancy in (None, False, "no", 0):
            self.assertEqual(self.client.post("/api/patient/predict", json={"inputs": CBC, "pregnancyStatus": pregnancy}).status_code, 422)
        self.assertEqual(self.client.post("/api/patient/predict", json={"inputs": {**CBC, "pregnancyStatus": "not_pregnant"}}).status_code, 422)

    def test_laboratory_api_same_model_without_persistence(self):
        from backend.privacy import POLICY_VERSION
        self.app.state.auth.create_organization("ferritin-test", "Synthetic lab", "test@example.invalid")
        identifier, token = self.app.state.auth.create_api_key("ferritin-test", "ferritin test")
        before = self.app.state.store.db.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        response = self.client.post("/api/integration/predict", headers={"Authorization": "Bearer " + token}, json={
            "policyVersion": POLICY_VERSION, "dataKind": "synthetic", "researchOnly": True,
            "inputs": EXTENDED, "pregnancyStatus": "not_pregnant"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ferritinScreening"]["route"], "extended")
        self.assertFalse(response.json()["stored"])
        self.assertEqual(self.app.state.store.db.execute("SELECT COUNT(*) FROM objects").fetchone()[0], before)
        self.app.state.auth.revoke_api_key(identifier)

    def test_missing_supplement_is_degraded_but_core_works(self):
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_app(data_dir=Path(folder), ferritin_bundle=Path(folder)/"missing.joblib"), base_url="http://localhost") as client:
                authenticate(client.app, client)
                self.assertEqual(client.get("/api/health").json()["status"], "degraded")
                response = client.post("/api/patient/predict", json={"inputs": CBC, "pregnancyStatus": "not_pregnant"})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["modelConnected"])
                self.assertEqual(response.json()["ferritinScreening"]["status"], "unavailable")
                self.assertNotIn("score", response.json()["ferritinScreening"])


if __name__ == "__main__":
    unittest.main()
