import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from ml_baselines.core import CBC, FEATURES, PANELS
from ml_baselines.external_validate import (ROOT, OUTPUT, REFERENCES, bootstrap_weights,
    input_view, load_model, make_references, mapping_2005, measure, routes, scores, sha)
from ml_baselines.predict import predict


class ExternalValidationTests(unittest.TestCase):
    def test_reference_unknowns_and_assay_specific_boundary(self):
        frame = pd.DataFrame({'ferritin': [14.,15.,np.nan], 'vitamin_B12': [199.,200.,np.nan],
                              'folate': [1.9,2.,np.nan], 'vitamin_B6': [19.,20.,np.nan]})
        result = make_references(frame, REFERENCES)
        np.testing.assert_array_equal(result.iloc[0], [1,1,1,1])
        np.testing.assert_array_equal(result.iloc[1], [0,0,0,0])
        self.assertTrue(result.iloc[2].isna().all())

    def test_reference_removed_before_inference_and_auto_routes(self):
        frame = pd.DataFrame(1., index=[0,1], columns=FEATURES)
        x = input_view(frame, 'reference_panel_withheld', 'iron')
        self.assertTrue(x[PANELS['iron']].isna().all().all())
        self.assertEqual(frame.ferritin.iloc[0], 1.)
        cbc = input_view(frame, 'cbc_only', 'iron')
        self.assertTrue(cbc[[k for k in FEATURES if k not in CBC]].isna().all().all())
        np.testing.assert_array_equal(routes(cbc), ['cbc','cbc'])
        np.testing.assert_array_equal(routes(frame), ['primary','primary'])

    def test_mapping_uses_documented_plp_and_does_not_invent_mma(self):
        mapping = {rule['target']: rule for rule in mapping_2005()}
        self.assertEqual(mapping['vitamin_B6']['column'], 'LBXPLP')
        self.assertEqual(mapping['hemoglobin']['factor_to_case'], 10)
        self.assertEqual(mapping['CRP']['factor_to_case'], 10)
        self.assertNotIn('MMA', mapping)
        self.assertTrue(all(rule['unit_status'] == 'documented' for rule in mapping.values()))

    def test_hash_checked_before_deserialization(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'untrusted.joblib'
            path.write_bytes(b'not a trusted pickle')
            with self.assertRaisesRegex(ValueError, 'before deserialization'):
                load_model({'path': str(path), 'sha256': '0'*64})

    def test_cluster_bootstrap_preserves_strata_and_confusion_metrics(self):
        meta = pd.DataFrame({'stratum': [1,1,1,2,2], 'psu': [1,1,2,1,2]})
        weights, inverse = bootstrap_weights(meta, 100, 20261005)
        self.assertEqual(inverse[0], inverse[1])
        np.testing.assert_array_equal(weights[:,:2].sum(axis=1), np.full(100,2))
        np.testing.assert_array_equal(weights[:,2:].sum(axis=1), np.full(100,2))
        result = measure([0,1,0,1,1], np.array([.1,.8,.2,.9,.1]), inverse, weights, .5)
        self.assertEqual(result['tp'], 2)
        self.assertEqual(result['fn'], 1)
        self.assertAlmostEqual(result['sensitivity'], 2/3)
        self.assertAlmostEqual(result['F1'], .8)


@unittest.skipUnless((OUTPUT / 'metrics.csv').exists(), 'Run frozen external assessment first')
class ExternalArtifactTests(unittest.TestCase):
    def test_frozen_weights_and_unknown_clinical_labels(self):
        protocol = json.loads((OUTPUT / 'protocol.json').read_text())
        self.assertEqual(sha(OUTPUT / 'protocol.json'), (OUTPUT / 'protocol.sha256').read_text().strip())
        for item in protocol['models'].values():
            self.assertEqual(sha(ROOT / item['path']), item['sha256'])
        truth = pd.read_csv(OUTPUT / 'clinical_labels_unknown.csv')
        self.assertTrue(truth.filter(regex='^known_').eq(0).all().all())
        self.assertTrue(truth.drop(columns=['patient_id',*truth.filter(regex='^known_').columns]).isna().all().all())
        metrics = pd.read_csv(OUTPUT / 'metrics.csv')
        self.assertEqual(len(metrics), 4*3*4)
        self.assertTrue((metrics.n == metrics.tn+metrics.fp+metrics.fn+metrics.tp).all())
        self.assertTrue((metrics.n == metrics.cbc_routed_count+metrics.primary_routed_count).all())
        decision = json.loads((OUTPUT / 'release_decision.json').read_text())
        self.assertFalse(decision['clinicalValidated'])
        self.assertFalse(decision['weights_changed'])

    def test_batch_predictions_match_research_inference_for_both_routes(self):
        protocol = json.loads((OUTPUT / 'protocol.json').read_text())
        from ml_baselines.core import encode
        all_features = pd.read_csv(OUTPUT / 'features.csv')
        f = all_features.loc[all_features[[k for k in CBC if k not in {'age_years','sex'}]].notna().sum(axis=1).ge(5)].iloc[:2]
        for item in protocol['models'].values():
            bundle = load_model(item)
            for view in ['available', 'cbc_only']:
                frame = input_view(encode(f), view, 'iron')
                batch = scores(bundle, frame, 'iron_deficiency')
                for j in range(len(frame)):
                    values = f.iloc[j].reindex(FEATURES).to_dict()
                    if view == 'cbc_only':
                        values = {k:v for k,v in values.items() if k in CBC}
                    values = {k: (None if pd.isna(v) else v) for k,v in values.items()}
                    one = predict(bundle, values)['scores']['iron_deficiency']['values']['1']
                    self.assertAlmostEqual(batch[j], one, places=12)


if __name__ == '__main__':
    unittest.main()
