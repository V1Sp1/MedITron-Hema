"""Models of low biochemical markers, kept separate from clinical deficiency heads."""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.impute import SimpleImputer, MissingIndicator
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.preprocessing import StandardScaler

from .core import CBC, FEATURES, LABS, encode

ENDPOINTS = {
    'low_ferritin': {'marker': 'ferritin', 'cutoff': 15., 'unit': 'µg/L', 'legacy_target': 'iron_deficiency',
                    'train_cycles': ['nhanes_2003_2004','nhanes_2005_2006'], 'test_cycle': 'nhanes_2007_2008', 'minimum_age': 18},
    'low_B12': {'marker': 'vitamin_B12', 'cutoff': 200., 'unit': 'pg/mL', 'legacy_target': 'B12_deficiency',
               'train_cycles': ['nhanes_2011_2012'], 'test_cycle': 'nhanes_2013_2014', 'minimum_age': 20},
    'low_PLP': {'marker': 'vitamin_B6', 'cutoff': 20., 'unit': 'nmol/L', 'legacy_target': 'B6_deficiency',
               'train_cycles': ['nhanes_2005_2006'], 'test_cycle': 'nhanes_2007_2008', 'minimum_age': 18},
}
CONFIGS = [
    {'name': 'logistic_values', 'algorithm': 'logistic', 'mask': False, 'augment': False},
    {'name': 'logistic_mask', 'algorithm': 'logistic', 'mask': True, 'augment': False},
    {'name': 'catboost', 'algorithm': 'catboost', 'augment': False},
    {'name': 'catboost_dropout', 'algorithm': 'catboost', 'augment': True},
]


def feature_names(endpoint, route):
    if endpoint not in ENDPOINTS or route not in {'extended','cbc'}:
        raise ValueError('Unknown biochemical head')
    return CBC + ([] if route == 'cbc' else ['creatinine','albumin','LDH'] + ([] if endpoint == 'low_B12' else ['CRP']))


def predictor_frame(frame, endpoint, route):
    """Discard all markers/identifiers/targets before any fit or inference."""
    result = frame.reindex(columns=feature_names(endpoint, route)).copy()
    result['age_years'] = result.age_years.clip(upper=80)
    return result


def drop_observed(frame, seed, fraction=.3):
    result = frame.copy()
    rng = np.random.default_rng(seed)
    labs = [name for name in result if name not in {'age_years','sex'}]
    for idx in result.index:
        available = [name for name in labs if pd.notna(result.at[idx,name])]
        if available:
            remove = rng.choice(available, int(np.ceil(len(available)*fraction)), replace=False)
            result.loc[idx,remove] = np.nan
    return result


@dataclass
class BiochemicalClassifier:
    endpoint: str
    route: str
    config: dict
    seed: int
    estimator: object = None

    def fit(self, frame, labels):
        x = predictor_frame(frame, self.endpoint, self.route)
        y = np.asarray(labels, dtype=int)
        if set(np.unique(y)) != {0,1}:
            raise ValueError('Known positive and negative marker references are required')
        if self.config['augment']:
            views = [x, drop_observed(x, self.seed)]
            if self.route == 'extended':
                cbc = x.copy()
                cbc.loc[:,[name for name in x if name not in CBC]] = np.nan
                views.append(cbc)
            x = pd.concat(views, ignore_index=True)
            y = np.tile(y, len(views))
        if self.config['algorithm'] == 'catboost':
            self.estimator = CatBoostClassifier(iterations=200, depth=4, learning_rate=.05,
                l2_leaf_reg=5, loss_function='Logloss', random_seed=self.seed, thread_count=4,
                verbose=False, allow_writing_files=False)
        else:
            values = SimpleImputer(strategy='median', keep_empty_features=True)
            prep = FeatureUnion([('values',values),('missing',MissingIndicator(features='all'))]) if self.config['mask'] else values
            self.estimator = Pipeline([('preprocess',prep), ('scale',StandardScaler()),
                ('model',LogisticRegression(C=.5, max_iter=2000, random_state=self.seed))])
        self.estimator.fit(x,y)
        return self

    def predict_score(self, frame):
        x = predictor_frame(frame, self.endpoint, self.route)
        return self.estimator.predict_proba(x)[:,1]


def predict_biochemical(bundle, inputs, pregnancy='unknown'):
    if set(inputs)-set(FEATURES):
        raise ValueError('Only the 37 laboratory/demographic features are accepted')
    if pregnancy not in {'unknown','not_pregnant','pregnant'}:
        raise ValueError('Unknown pregnancy status')
    for name, value in inputs.items():
        if name == 'sex':
            if value not in {'F','M',None}:
                raise ValueError('sex must be F/M/null')
        elif value is not None and (type(value) not in {int,float} or not np.isfinite(value) or value < 0):
            raise ValueError(f'{name}: expected nonnegative finite number or null')
    frame = encode(pd.DataFrame([inputs]))
    result = {'model_family':'biochemical_v4', 'research_only':True, 'clinicalValidated':False,
              'scoreMeaning':'research_score_for_low_biochemical_marker', 'outcomes':{},
              'warnings':['Низкий биомаркер и клинический диагноз — разные цели. Низкий score не исключает дефицит.',
                          'Модели проверены на отдельных циклах NHANES; перенос в клиническую популяцию не установлен.']}
    age, sex = inputs.get('age_years'), inputs.get('sex')
    for endpoint, rule in ENDPOINTS.items():
        unsupported = (age is None or sex not in {'F','M'} or age < rule['minimum_age'] or age > 120
                       or pregnancy == 'pregnant' or (sex == 'F' and 18 <= age <= 44 and pregnancy == 'unknown')
                       or (endpoint == 'low_ferritin' and (sex != 'F' or age > 49)))
        if unsupported:
            result['outcomes'][endpoint] = {'status':'outside_evaluated_population_or_unknown_scope'}
            continue
        extras = [name for name in feature_names(endpoint,'extended') if name not in CBC]
        route = 'extended' if frame[extras].notna().any(axis=1).iloc[0] else 'cbc'
        x = predictor_frame(frame,endpoint,route)
        lab_count = int(x[[name for name in x if name in LABS]].notna().sum(axis=1).iloc[0])
        if lab_count < 5:
            result['outcomes'][endpoint] = {'status':'insufficient_supported_data','available_lab_count':lab_count}
            continue
        head = bundle['heads'][(endpoint,route)]
        score = float(head.predict_score(frame)[0])
        threshold = bundle['thresholds'][(endpoint,route)]
        marker = inputs.get(rule['marker'])
        result['outcomes'][endpoint] = {'status':'research_prediction', 'route':route,
            'model':head.config['name'], 'score':score, 'threshold':threshold,
            'research_screen_positive':bool(score >= threshold),
            'external_gate_passed':bundle.get('validation',{}).get(f'{endpoint}/{route}',{}).get('passed',False),
            'marker_used_as_predictor':False, 'marker_observed':marker,
            'observed_marker_below_cutoff':None if marker is None else bool(marker < rule['cutoff']),
            'reference_cutoff':rule['cutoff'], 'unit':rule['unit'], 'available_lab_count':lab_count}
    return result
