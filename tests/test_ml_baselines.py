"""Protect patient isolation, train-only preprocessing and partial-input semantics."""

import unittest
import numpy as np
import pandas as pd
from ml_baselines.core import (CONFIGS, DICTIONARY, FEATURES, augment, encode, fit,
                              model_inputs, probabilities, scenario)
from ml_baselines.predict import predict


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.raw=pd.DataFrame([{'sex':'F','age_years':40,'hemoglobin':110,'ferritin':5},
                               {'sex':'M','age_years':60,'hemoglobin':145,'ferritin':40},
                               {'sex':'F','age_years':30,'hemoglobin':130,'ferritin':None},
                               {'sex':'M','age_years':50,'hemoglobin':100,'ferritin':7}])
        self.x=encode(self.raw)

    def test_whitelist_preserves_missing_and_excludes_labels(self):
        raw=self.raw.assign(anemia=1,patient_id='private')
        x=encode(raw)
        self.assertEqual(list(x.columns),FEATURES)
        self.assertTrue(x.copper.isna().all())
        self.assertTrue(encode(pd.DataFrame([{}])).sex.isna().all())

    def test_imputation_fits_only_training_values(self):
        config=next(c for c in CONFIGS if c['name']=='logistic')
        model=fit(config,self.x,np.array([1,0,0,1]),7)
        imputer=model.named_steps['preprocess'].transformer_list[0][1]
        before=imputer.statistics_.copy()
        test=self.x.copy();test['ferritin']=10000
        probabilities(model,test,config,[0,1])
        np.testing.assert_array_equal(before,imputer.statistics_)
        self.assertEqual(before[FEATURES.index('ferritin')],7)

    def test_random_drop_removes_only_observed_values_reproducibly(self):
        a=scenario(self.x,'drop60',9);b=scenario(self.x,'drop60',9)
        pd.testing.assert_frame_equal(a,b)
        self.assertTrue(a.copper.isna().all())
        pd.testing.assert_series_equal(a.age_years,self.x.age_years)
        self.assertLess(a.notna().sum().sum(),self.x.notna().sum().sum())

    def test_cbc_and_named_panels(self):
        self.assertTrue(scenario(self.x,'cbc_only',1).ferritin.isna().all())
        self.assertTrue(scenario(self.x,'no_iron',1).ferritin.isna().all())
        self.assertTrue(scenario(self.x,'no_hemoglobin',1).hemoglobin.isna().all())

    def test_augmentation_never_introduces_other_patients(self):
        train=self.x.iloc[:2];y=np.array([1,0])
        augmented,labels=augment(train,y,8)
        self.assertEqual(len(augmented),10)
        self.assertEqual(set(augmented.age_years),{40.,60.})
        np.testing.assert_array_equal(labels,np.tile(y,5))

    def test_empty_input_abstains_and_targets_are_rejected(self):
        bundle={'features':FEATURES,'dictionary_sha256':DICTIONARY['source']['sha256']}
        result=predict(bundle,{'age_years':40,'sex':'F'})
        self.assertEqual(result['status'],'insufficient_data')
        self.assertIsNone(result['anemia'])
        self.assertEqual(result['scores'],{})
        for inputs in [{'anemia':1},{'hemoglobin':float('inf')},{'hemoglobin':-2},{'sex':'unknown'}]:
            with self.assertRaises(ValueError):predict(bundle,inputs)


if __name__=='__main__':
    unittest.main()
