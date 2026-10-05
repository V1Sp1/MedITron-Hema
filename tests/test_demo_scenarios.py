"""Examples must reflect real inference, never assigned diagnoses or invented scores."""
import csv
import json
import unittest
from pathlib import Path

from backend.model_service import DEFAULT_MODEL_BUNDLE, ModelService
from backend.recommendations import phrases_for_report
from backend.screening import screen
from scripts.build_demo_scenarios import build_examples

ROOT = Path(__file__).resolve().parents[1]


class DemoExamplesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = ModelService(DEFAULT_MODEL_BUNDLE)
        cls.examples = build_examples(cls.service)

    def test_saved_examples_match_actual_inference_and_selector(self):
        saved = json.loads((ROOT / 'examples/full-demo/reports.json').read_text('utf-8'))
        self.assertEqual([x['id'] for x in saved], [x['id'] for x in self.examples])
        for item in saved:
            doctor = item['reports']['doctor']
            actual = self.service.apply(screen(doctor['inputs'], 'doctor'))
            for key in ('prediction', 'modelVerdict', 'modelDecisionState', 'decisionReasonCodes', 'modelVersion'):
                self.assertEqual(doctor[key], actual[key], (item['id'], key))
            for key in ('deficiencyScores', 'anemiaScores'):
                self.assertEqual(len(doctor[key]), len(actual[key]))
                for persisted, computed in zip(doctor[key], actual[key]):
                    self.assertEqual(persisted['code'], computed['code'])
                    self.assertAlmostEqual(persisted['score'], computed['score'], delta=1e-12)
            for role, report in item['reports'].items():
                expected = phrases_for_report({**actual, 'audience': role})
                self.assertEqual(report['recommendations'], expected['items'])
                self.assertEqual(report['insufficientData'], expected['insufficientData'])
                self.assertEqual(report['recommendationConclusions'], expected['conclusions'])
                self.assertEqual(report['audience'], role)
                self.assertEqual(report['source'], 'demo')

    def test_generation_requires_real_model_and_does_not_fallback(self):
        unavailable = ModelService(ROOT / 'not_a_model.joblib')
        with self.assertRaisesRegex(RuntimeError, 'no synthetic-score fallback'):
            build_examples(unavailable)

    def test_csv_has_only_features_and_preserves_missing_values(self):
        with (ROOT / 'examples/full-demo/input_panels.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 15)
        self.assertEqual(float(rows[-1]['hemoglobin']), 108)
        self.assertEqual(rows[-1]['ferritin'], '')
        self.assertEqual(len(rows[0]), 37)
        self.assertEqual(float(rows[0]['ferritin']), 6)


if __name__ == '__main__':
    unittest.main()
