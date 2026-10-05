from auth_helpers import authenticate, acknowledge
import hashlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from backend.app import MB, create_app
from backend.features import ROOT, convert_value, draft_inputs, prepare_observation
from backend.parser_service import run_parser
from lab_parser import parse_batch


def pdf(lines):
    stream = io.BytesIO()
    page = canvas.Canvas(stream)
    for index, line in enumerate(lines):
        page.drawString(40, 790 - 20 * index, line)
    page.save()
    return stream.getvalue()


VALUES = {"sex": "F", "age_years": 42, "hemoglobin": 108}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(data_dir=self.root, ocr_mode="off", model_bundle=None)
        self.client = TestClient(self.app, base_url="http://localhost")
        self.client.__enter__()
        authenticate(self.app, self.client)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def upload(self, *contents):
        return self.client.post("/api/observations/pdf", files=[("files", (f"lab-{i}.pdf", content, "application/pdf"))
                                                            for i, content in enumerate(contents)])

    def test_site_catalog_and_status(self):
        self.assertIn("Hema", self.client.get("/").text)
        health = self.client.get("/api/health")
        self.assertEqual(health.headers["cache-control"], "no-store")
        self.assertFalse(health.json()["modelConnected"])
        items = self.client.get("/api/features").json()["items"]
        self.assertEqual(len(items), 37)
        self.assertNotIn("anemia", [item["name"] for item in items])
        self.assertFalse(self.client.get("/api/models/status").json()["connected"])
        upload_schema = self.client.get("/openapi.json").json()["paths"]["/api/observations/pdf"]["post"]["requestBody"]
        self.assertIn("multipart/form-data", upload_schema["content"])

    def test_both_report_roles_boundaries_and_no_fake_model(self):
        for role, sex, hb, expected in [("patient", "F", 120, False), ("doctor", "F", 119.9, True),
                                       ("doctor", "M", 130, False), ("patient", "M", 129.9, True)]:
            result = self.client.post(f"/api/{role}/predict", json={"inputs": {**VALUES, "sex": sex, "hemoglobin": hb}})
            self.assertEqual(result.status_code, 200, result.text)
            report = result.json()
            self.assertEqual(report["audience"], role)
            self.assertEqual(report["anemia"], expected)
            self.assertFalse(report["modelConnected"])
            self.assertIsNone(report["prediction"])
            self.assertEqual(report["deficiencyProbabilities"], [])
            self.assertIsNone(report["inputs"]["ferritin"])
            self.assertEqual(self.client.get(f'/api/reports/{report["reportId"]}').json(), report)
            recs = self.client.post("/api/recommendations", json={"reportId": report["reportId"], "audience": role}).json()
            self.assertEqual([item["topic"] for item in recs["items"]], ["follow_up"])
            self.assertEqual(recs["status"], "draft")
            self.assertTrue(recs["insufficientData"]["active"])

    def test_invalid_inputs_and_target_labels_rejected(self):
        for extra in [{"anemia": 1}, {"patient_id": 1}, {"ferritin": True}, {"ferritin": -2},
                      {"age_years": 17}, {"age_years": 21.5}, {"sex": []}, {"sex": "X"},
                      {"hemoglobin": None}, {"hemoglobin": "108"}, {"hemoglobin": 10**500}]:
            response = self.client.post("/api/doctor/predict", json={"inputs": {**VALUES, **extra}})
            self.assertEqual(response.status_code, 422, response.text)
        response = self.client.post("/api/doctor/predict", json={"inputs": VALUES, "reviewedObservation": "yes"})
        self.assertEqual(response.status_code, 422)

    def test_pdf_to_review_to_saved_report_keeps_original(self):
        original = pdf(["Hb 10.8 g/dL", "Ferritin 9 ng/mL"])
        response = self.upload(original)
        self.assertEqual(response.status_code, 200, response.text)
        extracted = response.json()
        self.assertEqual(extracted["inputs"]["hemoglobin"], 108)
        self.assertEqual(extracted["inputs"]["ferritin"], 9)
        self.assertNotIn("_files", extracted["observation"])
        m = extracted["observation"]["measurements"][0]
        self.assertEqual((m["value"], m["unit"], m["normalized_value"]), (10.8, "g/dL", 108))
        self.assertEqual(m["source"]["page"], 1)
        obs_id = extracted["observationId"]
        download = self.client.get(f"/api/observations/{obs_id}/documents/doc-1")
        self.assertEqual(hashlib.sha256(download.content).hexdigest(), hashlib.sha256(original).hexdigest())
        body = {"inputs": {**extracted["inputs"], **VALUES}, "observationId": obs_id}
        self.assertEqual(self.client.post("/api/patient/predict", json=body).status_code, 409)
        body.update(reviewedObservation=True, observationRevision=1)
        body["inputs"]["hemoglobin"] = 109  # user correction, distinct from raw extraction
        result = self.client.post("/api/patient/predict", json=body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["observationRevision"], 2)
        self.assertEqual(self.client.post("/api/patient/predict", json=body).status_code, 409)
        observation = self.client.get(f"/api/observations/{obs_id}").json()
        self.assertEqual(observation["inputs"]["hemoglobin"], 109)
        self.assertEqual(observation["observation"]["measurements"][0]["value"], 10.8)
        self.assertEqual(observation["observation"]["review_status"], "confirmed_inputs")
        reviewed = self.client.put(f"/api/observations/{obs_id}/review", json={"revision": 2, "inputs": VALUES})
        self.assertEqual(reviewed.json()["revision"], 3)

    def test_batch_different_dates_repeats_and_bounds_not_selected(self):
        response = self.upload(pdf(["Date of collection: 01.09.2026", "Hb 110 g/L"]),
                               pdf(["Date of collection: 01.10.2026", "Hb 130 g/L", "Ferritin <5 ng/mL"]))
        self.assertEqual(response.status_code, 200, response.text)
        obs = response.json()
        self.assertEqual(len(obs["observation"]["documents"]), 2)
        self.assertEqual(len(obs["observation"]["measurements"]), 3)
        self.assertIsNone(obs["inputs"]["hemoglobin"])
        self.assertIsNone(obs["inputs"]["ferritin"])
        self.assertIn("repeated_analytes_preserved", obs["observation"]["issues"])

    def test_upload_errors_clean_staged_files(self):
        self.assertEqual(self.upload(b"not pdf").status_code, 422)
        self.assertEqual(self.upload(b"%PDF-broken").status_code, 422)
        self.assertEqual(self.client.post("/api/observations/pdf").status_code, 422)
        self.assertEqual(self.upload(*[pdf(["Hb 130 g/L"])] * 11).status_code, 400)
        self.assertEqual(self.upload(b"%PDF-" + b"0" * (20 * MB)).status_code, 413)
        self.assertEqual(list((self.root / "uploads").glob("*")), [])
        response = self.client.post("/api/observations/pdf", headers={"Content-Length": str(62 * MB)})
        self.assertEqual(response.status_code, 413)

    def test_rejected_remote_origin_hosts_and_private_storage(self):
        self.assertEqual(self.client.post("/api/doctor/predict", json={"inputs": VALUES},
                                         headers={"Origin": "https://example.com"}).status_code, 403)
        self.assertEqual(self.client.get("/api/health", headers={"Host": "example.com"}).status_code, 400)
        for path in ["/data/local/hema.sqlite3", "/../data/local/hema.sqlite3", "/api/reports/not-a-uuid"]:
            self.assertEqual(self.client.get(path).status_code, 404)
        cors = self.client.options("/api/doctor/predict", headers={"Origin": "http://127.0.0.1:5173",
                                    "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type"})
        self.assertEqual(cors.headers["access-control-allow-origin"], "http://127.0.0.1:5173")

    def test_medical_storage_is_volatile_across_restart(self):
        report = self.client.post("/api/doctor/predict", json={"inputs": VALUES}).json()
        with TestClient(create_app(data_dir=self.root), base_url="http://localhost") as second:
            second.cookies.update(self.client.cookies)
            self.assertEqual(second.get(f'/api/reports/{report["id"]}').status_code, 404)
        self.assertFalse((self.root/'hema.sqlite3').exists())

    def test_optional_reviewed_phrase_file_and_role(self):
        path = self.root / "test-phrases.json"
        path.write_text(json.dumps({"version": "test-only", "reviewed": True, "items": [
            {"id": "fixture", "text": "Test fixture, not a clinical recommendation", "audience": "doctor", "when": {"anemia": True}}]}))
        with TestClient(create_app(data_dir=self.root, phrase_file=path), base_url="http://localhost") as client:
            authenticate(client.app, client)
            report = client.post("/api/doctor/predict", json={"inputs": VALUES}).json()
            body = {"reportId": report["id"], "audience": "doctor"}
            self.assertEqual(len(client.post("/api/recommendations", json=body).json()["items"]), 1)
            self.assertEqual(client.post("/api/recommendations", json={**body, "audience": "patient"}).status_code, 409)
            path.write_text('{"reviewed":false}')
            self.assertEqual(client.post("/api/recommendations", json=body).status_code, 503)
            path.write_text('[]')
            self.assertEqual(client.post("/api/recommendations", json=body).status_code, 503)

    def test_timeout_releases_lock_and_removes_files(self):
        def timeout(*args, **kwargs):
            raise TimeoutError("Test timeout")
        with TestClient(create_app(data_dir=self.root, parser=timeout), base_url="http://localhost") as client:
            acknowledge(client)
            for _ in range(2):
                response = client.post("/api/observations/pdf", files={"files": ("lab.pdf", pdf(["Hb 130 g/L"]))})
                self.assertEqual(response.status_code, 504)
        self.assertEqual(list((self.root / "uploads").glob("*")), [])

    @unittest.skipUnless((ROOT / "tmp/public_samples/helix_checkup.pdf").exists(), "Public fixture not downloaded")
    def test_public_helix_through_actual_worker(self):
        original = (ROOT / "tmp/public_samples/helix_checkup.pdf").read_bytes()
        result = self.upload(original)
        self.assertEqual(result.status_code, 200, result.text)
        obs = result.json()
        self.assertEqual(len(obs["observation"]["documents"][0]["pages"]), 16)
        self.assertEqual(len(obs["observation"]["measurements"]), 57)
        self.assertIsNotNone(obs["inputs"]["hemoglobin"])
        self.assertTrue(any("неизвестна дата" in w for w in obs["warnings"]))


class UnitTests(unittest.TestCase):
    def test_scaling_and_incompatible_units(self):
        for key, value, unit, expected in [("hemoglobin", 10.8, "g/dL", 108), ("ferritin", 9, "ng/mL", 9),
                                          ("MMA", 300, "nmol/L", .3), ("CRP", .5, "mg/dL", 5),
                                          ("hematocrit", .4, "L/L", 40), ("TSH", 2, "µIU/mL", 2)]:
            self.assertEqual(convert_value(key, value, unit)[0], expected)
        for key, unit in [("vitamin_B6", "ng/mL"), ("creatinine", "mg/dL"), ("hemoglobin", None)]:
            self.assertIsNone(convert_value(key, 2, unit))

    def test_stale_and_future_results_stay_in_observation_but_not_form(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.pdf"
            path.write_bytes(pdf(["Hb 130 g/L"]))
            observation = prepare_observation(parse_batch([path], ocr_mode="off").to_dict())
        for freshness in ("stale", "future_date"):
            item = deepcopy(observation)
            item["measurements"][0]["freshness"] = freshness
            self.assertIsNone(draft_inputs(item)["hemoglobin"])
            self.assertEqual(item["measurements"][0]["value"], 130)


if __name__ == "__main__":
    unittest.main()
