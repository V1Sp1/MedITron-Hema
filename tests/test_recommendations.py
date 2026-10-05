from auth_helpers import authenticate
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.data_sufficiency import assess_data
from backend.recommendations import phrase_catalog, phrases_for_report
from backend.screening import screen


MINIMAL = {"age_years": 42, "sex": "F", "hemoglobin": 108}
BASIC = {**MINIMAL, "RBC": 4.1, "hematocrit": 34, "MCV": 82, "MCH": 26.3,
         "MCHC": 318, "RDW": 15.2, "platelets": 280, "WBC": 6.2}
BROADER = {**BASIC, "ferritin": 9, "vitamin_B12": 300, "folate": 8, "CRP": 2}


def report(inputs=BROADER, role="doctor", verdict=None):
    result = screen(inputs, role)
    if verdict is not None:
        result.update(modelConnected=True, modelVersion="test-fixture-not-a-real-model", modelVerdict=verdict)
    return result


def topics(response, kind="items"):
    return [item["topic"] for item in response[kind]]


class SelectionTests(unittest.TestCase):
    def test_catalogs_have_20_distinct_role_specific_draft_phrases(self):
        patient, doctor = (phrase_catalog(role) for role in ["patient", "doctor"])
        for catalog in [patient, doctor]:
            self.assertEqual(len(catalog["items"]), 20)
            self.assertEqual(len({row["id"] for row in catalog["items"]}), 20)
            self.assertEqual(catalog["clinicalReviewStatus"], "draft")
        for p, d in zip(patient["items"], doctor["items"]):
            self.assertEqual(p["when"], d["when"])
            self.assertNotEqual(p["text"], d["text"])

    def test_core_missing_does_not_produce_anemia_or_negative_deficit_claim(self):
        for role in ["patient", "doctor"]:
            data = assess_data({}, role)
            self.assertFalse(data["canAssessAnemia"])
            self.assertEqual(data["laboratoryCount"], 0)
            self.assertEqual(data["level"], "insufficient")
            self.assertIsNone(data["modelConfidence"])

    def test_coverage_counts_labs_only_preserves_zero_and_not_all_35_required(self):
        self.assertEqual(assess_data(MINIMAL, "patient")["laboratoryCount"], 1)
        self.assertEqual(assess_data({**MINIMAL, "ferritin": 0}, "patient")["laboratoryCount"], 2)
        self.assertEqual(assess_data(BASIC, "patient")["level"], "limited")
        data = assess_data(BROADER, "patient")
        self.assertEqual(data["level"], "broader")
        self.assertLess(data["laboratoryCount"], 35)
        self.assertFalse(data["clinicalValidated"])

    def test_active_b12_is_alternative_not_request_to_order_every_test(self):
        inputs = {**BROADER, "vitamin_B12": None, "active_B12": 75, "folate": None}
        result = phrases_for_report(report(inputs))
        gap = next(item for item in result["insufficientData"]["items"] if item["topic"] == "vitamin_gap")
        self.assertEqual(gap["suggestedFeatures"], ["folate"])
        inputs["folate"] = 8
        self.assertEqual(assess_data(inputs, "doctor")["level"], "broader")

    def test_b12_alternatives_apply_to_specific_phrases_and_context(self):
        for role in ["patient", "doctor"]:
            for present, absent in [("active_B12", "vitamin_B12"), ("vitamin_B12", "active_B12")]:
                inputs = {**BROADER, present: 75, absent: None}
                result = phrases_for_report(report(inputs, role, {"deficits": ["B12"]}))
                specific = next(item for item in result["items"] if item["topic"] == "B12")
                self.assertTrue({"vitamin_B12", "active_B12"}.isdisjoint(specific["suggestedFeatures"]))
                self.assertIn("vitamin_context", topics(result))
                result = phrases_for_report(report(inputs, role, {"deficits": ["folate"]}))
                specific = next(item for item in result["items"] if item["topic"] == "folate")
                self.assertNotIn(absent, specific["suggestedFeatures"])

    def test_latent_phrase_includes_equality_at_both_hb_thresholds(self):
        for sex, hb in [("F", 120), ("M", 130)]:
            result = phrases_for_report(report({**BROADER, "sex": sex, "hemoglobin": hb},
                                               "patient", {"deficits": ["iron"]}))
            text = next(item["text"] for item in result["conclusions"] if item["topic"] == "latent")
            self.assertIn("не ниже порога", text)

    def test_expanded_investigation_fields_exclude_existing_values_for_both_roles(self):
        for role in ["patient", "doctor"]:
            for deficit in ["iron", "B12", "folate", "B6", "copper"]:
                result = phrases_for_report(report(role=role, verdict={"deficits": [deficit]}))
                specific = next(item for item in result["items"] if item["topic"] == deficit)
                self.assertIn("medical_team", specific["sourceIds"])
                self.assertTrue(specific["suggestedFeatures"])
                self.assertTrue(set(specific["suggestedFeatures"]).isdisjoint(BROADER))
                all_present = {**BROADER, **{key: 1 for key in specific["suggestedFeatures"]}}
                complete = phrases_for_report(report(all_present, role, {"deficits": [deficit]}))
                phrase = next(item for item in complete["items"] if item["topic"] == deficit)
                self.assertEqual(phrase["suggestedFeatures"], [])

    def test_normal_hb_wording_includes_equal_threshold_and_does_not_claim_upper_normality(self):
        for role in ["patient", "doctor"]:
            for sex, hb in [("F", 120), ("M", 130), ("F", 200)]:
                result = phrases_for_report(report({**BROADER, "sex": sex, "hemoglobin": hb}, role))
                phrase = next(item for item in result["conclusions"] if item["topic"] == "hb_not_low")
                self.assertIn("не ниже", phrase["text"])
                self.assertNotIn("выше порог", phrase["text"])
                self.assertNotIn("в норме", phrase["text"])

    def test_conflicting_independent_targets_never_select_specific_phrases(self):
        fixtures = [
            (BROADER, {"anemiaClass": "anemia_other", "deficits": ["iron"]}, "class_deficits_mismatch"),
            (BROADER, {"anemiaClass": "anemia_other", "inflammation": True}, "class_inflammation_mismatch"),
            ({**BROADER, "hemoglobin": 120}, {"anemiaClass": "latent_deficiency", "deficits": []}, "latent_without_deficit"),
            ({**BROADER, "hemoglobin": 120}, {"deficits": ["B12"], "inflammation": True}, "inflammation_without_anemia"),
        ]
        for role in ["patient", "doctor"]:
            for inputs, verdict, reason in fixtures:
                result = phrases_for_report(report(inputs, role, verdict))
                self.assertEqual(result["verdictContext"]["modelState"], "inconsistent")
                self.assertIn(reason, result["verdictContext"]["reasonCodes"])
                limited = next(i for i in result["conclusions"] if i["topic"] == "model_limited")
                self.assertIn(reason, limited["reasonCodes"])
                self.assertTrue({"iron", "B12", "folate", "B6", "copper", "mixed", "inflammation", "other"}.isdisjoint(topics(result)))

    def test_empty_structured_verdict_is_not_an_evaluated_decision(self):
        result = phrases_for_report(report(verdict={}))
        self.assertEqual(result["verdictContext"]["modelState"], "missing_verdict")
        self.assertIn("model_absent", topics(result, "conclusions"))

    def test_sparse_report_separates_hb_conclusion_from_data_gaps(self):
        result = phrases_for_report(report(MINIMAL))
        self.assertTrue(result["insufficientData"]["active"])
        self.assertIn("sparse", topics(result["insufficientData"]))
        self.assertIn("hb_low", topics(result, "conclusions"))
        self.assertIn("model_absent", topics(result, "conclusions"))
        self.assertEqual(topics(result), ["follow_up"])

    def test_feature_presence_does_not_infer_deficits_from_values(self):
        result = phrases_for_report(report({**BROADER, "ferritin": 0, "vitamin_B12": 0}))
        self.assertIn("ferritin_context", topics(result))
        self.assertIn("vitamin_context", topics(result))
        self.assertNotIn("iron", topics(result))
        self.assertNotIn("B12", topics(result))

    def test_each_single_deficit_branch_uses_explicit_verdict(self):
        for deficit in ["iron", "B12", "folate", "B6", "copper"]:
            for role in ["patient", "doctor"]:
                result = phrases_for_report(report(role=role, verdict={"deficits": [deficit]}))
                self.assertIn(deficit, topics(result))
                self.assertNotIn("mixed", topics(result))
                self.assertTrue(all(item["id"].startswith(role+".") for item in result["items"]))

    def test_all_pair_combinations_get_mixed_message_and_names(self):
        for deficits in [["iron", "B12"], ["iron", "folate"], ["B12", "folate"], ["B6", "copper"]]:
            result = phrases_for_report(report(verdict={"anemiaClass": "mixed_deficiency", "deficits": deficits}))
            self.assertIn("mixed", topics(result))
            self.assertTrue(set(deficits).isdisjoint(topics(result)))
            text = next(item["text"] for item in result["items"] if item["topic"] == "mixed")
            self.assertNotIn("{", text)
            self.assertLessEqual(len(result["items"]), 4)

    def test_deficit_without_anemia_including_latent_ambiguous_class(self):
        inputs = {**BROADER, "hemoglobin": 131}
        for verdict in [{"anemiaClass": "B12_deficiency_no_anemia"}, {"anemiaClass": "latent_deficiency"}]:
            result = phrases_for_report(report(inputs, verdict=verdict))
            self.assertIn("latent", topics(result, "conclusions"))
            self.assertNotIn("hb_low", topics(result, "conclusions"))
        latent = phrases_for_report(report(inputs, verdict={"anemiaClass": "latent_deficiency"}))
        self.assertNotIn("iron", topics(latent))  # no silent mapping from the old dataset

    def test_inflammation_can_coexist_with_iron_and_other_does_not_mean_excluded_causes(self):
        result = phrases_for_report(report(verdict={"deficits": ["iron"], "inflammation": True}))
        self.assertIn("iron", topics(result))
        self.assertIn("inflammation", topics(result))
        result = phrases_for_report(report(verdict={"anemiaClass": "anemia_other"}))
        self.assertIn("other", topics(result))

    def test_sparse_uncertain_out_of_scope_and_inconsistent_suppress_specific_messages(self):
        fixtures = [(MINIMAL, {"deficits": ["iron"]}, "suppressed_sparse"),
                    (BROADER, {"deficits": ["iron"], "status": "uncertain"}, "uncertain"),
                    (BROADER, {"deficits": ["iron"], "status": "out_of_scope"}, "out_of_scope"),
                    ({**BROADER, "hemoglobin": 131}, {"anemiaClass": "iron_deficiency_anemia"}, "inconsistent"),
                    (BROADER, {"anemiaClass": "B12_deficiency_anemia", "deficits": ["iron"]}, "inconsistent")]
        for inputs, verdict, state in fixtures:
            result = phrases_for_report(report(inputs, verdict=verdict))
            self.assertEqual(result["verdictContext"]["modelState"], state)
            self.assertNotIn("iron", topics(result))
            self.assertNotIn("B12", topics(result))
            self.assertIn("model_limited", topics(result, "conclusions"))

    def test_probabilities_free_text_and_disconnected_model_are_not_verdicts(self):
        fixture = report()
        fixture.update(prediction={"label": "iron_deficiency_anemia"},
                       deficiencyProbabilities=[{"label": "iron", "probability": .99}],
                       modelVerdict={"deficits": ["iron"]})
        result = phrases_for_report(fixture)
        self.assertNotIn("iron", topics(result))
        fixture.update(modelConnected=True, modelVersion="test")
        fixture.pop("modelVerdict")
        self.assertEqual(phrases_for_report(fixture)["verdictContext"]["modelState"], "missing_verdict")


class RecommendationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(data_dir=Path(self.temp.name))
        self.client = TestClient(self.app, base_url="http://localhost")
        self.client.__enter__()
        authenticate(self.app, self.client)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_catalog_and_preflight_with_empty_or_invalid_inputs(self):
        for role in ["doctor", "patient"]:
            self.assertEqual(len(self.client.get('/api/recommendations/catalog', params={"audience": role}).json()["items"]), 20)
        self.assertEqual(self.client.get('/api/recommendations/catalog', params={"audience": "unknown"}).status_code, 422)
        data = self.client.post('/api/data-sufficiency', json={"inputs": {}, "audience": "patient"}).json()
        self.assertFalse(data["canAssessAnemia"])
        for bad in [{"anemia": 1}, {"ferritin": True}, {"sex": []}, {"age_years": 17}]:
            self.assertEqual(self.client.post('/api/data-sufficiency', json={"inputs": bad}).status_code, 422)

    def test_preview_is_explicit_simulation_not_stored_or_connected_model(self):
        payload = {"audience": "doctor", "inputs": BROADER, "verdict": {"deficits": ["iron", "B12"]}}
        response = self.client.post('/api/recommendations/preview', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["simulation"])
        self.assertFalse(result["modelConnected"])
        self.assertIsNone(result["reportId"])
        self.assertIn("mixed", topics(result))
        count = self.app.state.store.db.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        self.assertEqual(count, 0)
        empty = self.client.post('/api/recommendations/preview', json={"inputs": {}}).json()
        self.assertIsNone(empty["anemia"])

    def test_invalid_decisions_rejected_and_real_prediction_cannot_accept_verdict(self):
        for verdict in [{"deficits": ["iron", "iron"]}, {"deficits": ["unknown"]},
                        {"anemiaClass": "unsupported"}, {"inflammation": "yes"}, {"probability": .9}]:
            self.assertEqual(self.client.post('/api/recommendations/preview', json={"inputs": BROADER, "verdict": verdict}).status_code, 422)
        self.assertEqual(self.client.post('/api/doctor/predict', json={"inputs": BROADER, "verdict": {"deficits": ["iron"]}}).status_code, 422)

    def test_recommendations_snapshot_survives_catalog_change_and_role_checked(self):
        saved = self.client.post('/api/patient/predict', json={"inputs": MINIMAL}).json()
        self.assertEqual(saved["dataSufficiency"]["level"], "insufficient")
        body = {"reportId": saved["reportId"], "audience": "patient"}
        initial = self.client.post('/api/recommendations', json=body).json()
        with patch('backend.recommendations.phrase_catalog', side_effect=ValueError('catalog changed')):
            self.assertEqual(self.client.post('/api/recommendations', json=body).json(), initial)
        self.assertEqual(self.client.post('/api/recommendations', json={**body, "audience": "doctor"}).status_code, 409)
        self.assertEqual(self.client.get(f'/api/reports/{saved["reportId"]}').json(), saved)

    def test_old_report_without_snapshot_gets_current_draft_with_honest_model_state(self):
        # Preserve the server-issued access owner; historical ownerless records
        # are intentionally inaccessible under the current privacy policy.
        old = self.client.post('/api/doctor/predict', json={"inputs": MINIMAL}).json()
        key = old['reportId']
        old = self.app.state.store.get('report', key)
        old.pop('recommendationSnapshot', None)
        self.app.state.store.delete('report', key)
        self.app.state.store.put('report', old)
        response = self.client.post('/api/recommendations', json={"reportId": key, "audience": "doctor"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "draft")
        self.assertTrue(response.json()["insufficientData"]["active"])


if __name__ == '__main__':
    unittest.main()
