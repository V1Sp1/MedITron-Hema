import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from ml_baselines.biochemical import (ENDPOINTS,CONFIGS,BiochemicalClassifier,feature_names,
                                     predictor_frame,drop_observed,predict_biochemical)
from ml_baselines.core import FEATURES,CBC,encode,ROOT
from ml_baselines.nhanes_v4 import new_mapping,pregnancy_exclusion
from ml_baselines.train_biochemical import OUTPUT,threshold_for_specificity,statistics
from ml_baselines.external_validate import sha,load_model


class BiochemicalTests(unittest.TestCase):
    def test_whitelist_hides_all_target_markers_and_service_fields(self):
        frame = pd.DataFrame(1.,index=[0,1],columns=FEATURES+['patient_id','low_PLP','B6_deficiency'])
        for endpoint in ENDPOINTS:
            for route in ['extended','cbc']:
                x = predictor_frame(frame,endpoint,route)
                self.assertEqual(list(x),feature_names(endpoint,route))
                for marker in ['ferritin','vitamin_B12','vitamin_B6','folate','MMA','homocysteine']:
                    self.assertNotIn(marker,x)
                self.assertNotIn('patient_id',x)

    def test_train_only_imputation_and_serialized_classifier(self):
        x = pd.DataFrame(np.nan,index=range(100),columns=CBC)
        x['age_years'] = 45.
        x['sex'] = 0.
        x['hemoglobin'] = np.linspace(80,150,100)
        x['MCV'] = 85.
        y = np.array([1]*50+[0]*50)
        model = BiochemicalClassifier('low_ferritin','cbc',CONFIGS[0],10).fit(x,y)
        median = model.estimator.named_steps['preprocess'].statistics_[CBC.index('hemoglobin')]
        self.assertAlmostEqual(median,115.)
        test = x.iloc[:3].copy()
        test['hemoglobin'] = 500.
        score = model.predict_score(test)
        self.assertAlmostEqual(model.estimator.named_steps['preprocess'].statistics_[CBC.index('hemoglobin')],115.)
        import joblib
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'m.joblib'
            joblib.dump(model,path)
            np.testing.assert_allclose(joblib.load(path).predict_score(test),score)

    def test_threshold_respects_both_views_including_ties(self):
        y = np.array([0]*10+[1]*3)
        views = {'a':np.array([.1]*8+[.4,.9]+[.2,.5,.95]),
                 'b':np.array([.2]*8+[.6,.8]+[.4,.7,.9])}
        t = threshold_for_specificity(y,views)
        self.assertGreater(t,.6)
        for p in views.values():
            self.assertGreaterEqual(statistics(y,p,t)['specificity'],.9)

    def test_dropout_keeps_demographics_unknowns_and_marker_schema(self):
        x = pd.DataFrame(1.,index=[4,8],columns=CBC)
        x.loc[4,'WBC'] = np.nan
        x.loc[8,'age_years'] = 90.
        a = drop_observed(x,42)
        np.testing.assert_array_equal(a[['age_years','sex']],x[['age_years','sex']])
        self.assertTrue(pd.isna(a.loc[4,'WBC']))
        np.testing.assert_array_equal(a,drop_observed(x,42))
        self.assertEqual(predictor_frame(x,'low_B12','cbc').age_years.iloc[1],80.)

    def test_pregnancy_unknown_not_negative_and_b12_corrected_column(self):
        f = pd.DataFrame({'sex':['F','F','F','M'],'age_years':[30,30,60,30]})
        m = pd.DataFrame({'pregnancy_code':[1,np.nan,np.nan,np.nan]})
        np.testing.assert_array_equal(pregnancy_exclusion(f,m),[True,True,False,False])
        mapping = {r['target']:r for r in new_mapping('nhanes_2013_2014')}
        self.assertEqual(mapping['vitamin_B12']['column'],'LBDB12')
        self.assertNotIn('CRP',mapping)
        self.assertTrue(all(r['unit_status'] == 'documented' for r in mapping.values()))

    def test_inference_abstains_outside_population_and_sparse(self):
        result = predict_biochemical({'heads':{}},{'age_years':40,'sex':'F','hemoglobin':110})
        self.assertTrue(all(v['status']=='outside_evaluated_population_or_unknown_scope' for v in result['outcomes'].values()))
        result = predict_biochemical({'heads':{}},{'age_years':60,'sex':'M','hemoglobin':110})
        self.assertEqual(result['outcomes']['low_ferritin']['status'],'outside_evaluated_population_or_unknown_scope')
        self.assertEqual(result['outcomes']['low_B12']['status'],'insufficient_supported_data')
        with self.assertRaises(ValueError):
            predict_biochemical({}, {'patient_id':1})

    def test_candidate_weight_hash_is_checked_before_pickle(self):
        from unittest.mock import patch
        from ml_baselines.biochemical_predict import load_candidate
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'unknown.joblib'
            path.write_bytes(b'untrusted serialization')
            trust = Path(folder)/'trust.json'
            trust.write_text(json.dumps({'bundles':{str(path):{'sha256':'0'*64}}}))
            with patch('ml_baselines.biochemical_predict.joblib.load') as deserialize:
                with self.assertRaisesRegex(ValueError,'before deserialization'):
                    load_candidate(path,trust)
                deserialize.assert_not_called()


@unittest.skipUnless((OUTPUT/'external_metrics.csv').exists(),'Run v4 experiment first')
class BiochemicalArtifactTests(unittest.TestCase):
    @unittest.skipUnless((OUTPUT/'split_audit.json').exists(),'Row-level split audits are excluded from the research handoff')
    def test_psu_patient_and_test_source_disjointness(self):
        for fold in json.loads((OUTPUT/'split_audit.json').read_text()):
            self.assertFalse(set(fold['learn_patients']) & set(fold['valid_patients']))
            self.assertFalse(set(fold['learn_groups']) & set(fold['valid_groups']))
            self.assertTrue(all('nhanes_2007_2008' not in p and 'nhanes_2013_2014' not in p for p in fold['learn_patients']+fold['valid_patients']))
        lock = json.loads((OUTPUT/'selection_lock.json').read_text())
        self.assertFalse(lock['test_results_seen'])
        self.assertEqual(sha(OUTPUT/'models/selected.joblib'),lock['model_sha256'])
        self.assertEqual(sha(OUTPUT/'selection.json'),lock['selection_sha256'])

    @unittest.skipUnless((OUTPUT/'nhanes_2007_2008_test_features.csv').exists(),'Row-level medical data are excluded from the research handoff')
    def test_real_single_prediction_matches_saved_policy_without_marker_leak(self):
        lock = json.loads((OUTPUT/'selection_lock.json').read_text())
        bundle = load_model({'path':str(OUTPUT/'models/selected.joblib'),'sha256':lock['model_sha256']})
        f = pd.read_csv(OUTPUT/'nhanes_2007_2008_test_features.csv')
        f = f.loc[f.sex.eq('F') & f.age_years.between(18,49) & f[CBC[2:]].notna().sum(axis=1).ge(5)].iloc[0]
        inputs = {k:(None if pd.isna(v) else v if k == 'sex' else float(v)) for k,v in f.reindex(FEATURES).items()}
        a = predict_biochemical(bundle,inputs,'not_pregnant')
        b = predict_biochemical(bundle,{**inputs,'ferritin':10000.,'vitamin_B6':10000.},'not_pregnant')
        for e in ['low_ferritin','low_PLP']:
            self.assertAlmostEqual(a['outcomes'][e]['score'],b['outcomes'][e]['score'])
            route = a['outcomes'][e]['route']
            expected = bundle['heads'][(e,route)].predict_score(encode(pd.DataFrame([inputs])))[0]
            self.assertAlmostEqual(a['outcomes'][e]['score'],expected)
        self.assertFalse(a['clinicalValidated'])
        self.assertEqual(bundle['model_family'],'biochemical_v4')

    def test_separate_trust_registry_and_external_evidence(self):
        from ml_baselines.biochemical_predict import load_candidate
        bundle = load_candidate()
        self.assertTrue(bundle['validation']['low_ferritin/extended']['passed'])
        self.assertFalse(bundle['validation']['low_B12/cbc']['passed'])
        clinical = json.loads((ROOT/'backend/data/model_trust.json').read_text())['sha256']
        self.assertNotIn('experiments/biochemical_v4/models/selected.joblib',clinical)


if __name__ == '__main__':
    unittest.main()
