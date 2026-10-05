"""Shared feature schema, train-only preprocessing and missing-panel scenarios."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import MissingIndicator, SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
DICTIONARY = json.loads((ROOT / "data/feature_dictionary.json").read_text())
FEATURES = [c['name'] for c in DICTIONARY['columns'] if c['role'] == 'feature']
LABS = [k for k in FEATURES if k not in {'age_years', 'sex'}]
CBC = ['age_years', 'sex', 'hemoglobin', 'RBC', 'hematocrit', 'MCV', 'MCH',
       'MCHC', 'RDW', 'platelets', 'WBC']
TARGETS = ['anemia_class', 'iron_deficiency', 'B12_deficiency', 'folate_deficiency',
           'B6_deficiency', 'copper_deficiency', 'inflammation_anemia']
DEFICIENCIES = TARGETS[1:6]
PANELS = {
    'iron': ['ferritin', 'serum_iron', 'transferrin', 'TIBC', 'UIBC', 'TSAT', 'sTfR', 'Ret_He'],
    'B12': ['vitamin_B12', 'active_B12', 'MMA', 'homocysteine'],
    'folate': ['folate', 'homocysteine'],
    'B6': ['vitamin_B6'],
    'copper': ['copper', 'ceruloplasmin'],
    'inflammation': ['CRP', 'ESR'],
}
SCENARIOS = ['available', 'drop30', 'drop60', 'cbc_only', 'hb_only',
             'no_iron', 'no_B12', 'no_folate', 'no_B6', 'no_copper',
             'no_hemoglobin', 'no_demographics']
SELECTION_SCENARIOS = ['available', 'drop30', 'drop60']
CONFIGS = [
    {'name': 'prior', 'algorithm': 'dummy', 'panel': 'extended'},
    {'name': 'logistic', 'algorithm': 'logistic', 'panel': 'extended'},
    {'name': 'logistic_values', 'algorithm': 'logistic_values', 'panel': 'extended'},
    {'name': 'extra_trees', 'algorithm': 'extra_trees', 'panel': 'extended'},
    {'name': 'catboost', 'algorithm': 'catboost', 'panel': 'extended'},
    {'name': 'catboost_dropout', 'algorithm': 'catboost', 'panel': 'extended', 'augment': True},
    {'name': 'logistic_cbc', 'algorithm': 'logistic', 'panel': 'cbc'},
    {'name': 'extra_trees_cbc', 'algorithm': 'extra_trees', 'panel': 'cbc'},
    {'name': 'catboost_cbc', 'algorithm': 'catboost', 'panel': 'cbc'},
    {'name': 'missingness_only', 'algorithm': 'mask', 'panel': 'extended'},
]


def encode(frame):
    """Whitelist only. Missing sex remains NaN, never a default female value."""
    result = frame.reindex(columns=FEATURES).copy()
    if result.sex.dropna().isin(['F', 'M']).all():
        result['sex'] = result.sex.map({'F': 0., 'M': 1.})
    else:
        raise ValueError('sex must be F, M or missing')
    for key in FEATURES:
        result[key] = pd.to_numeric(result[key], errors='raise').astype(float)
    if np.isinf(result.to_numpy()).any():
        raise ValueError('Non-finite input')
    return result


def scenario(frame, name, seed):
    result = frame.copy()
    if name == 'available':
        return result
    if name in {'drop30', 'drop60'}:
        # Drop a fraction of each patient's actually observed results, excluding demographics.
        rng = np.random.default_rng(seed)
        fraction = .3 if name == 'drop30' else .6
        for idx in result.index:
            present = [k for k in LABS if pd.notna(result.at[idx, k])]
            if present:
                remove = rng.choice(present, size=int(np.ceil(len(present)*fraction)), replace=False)
                result.loc[idx, remove] = np.nan
    elif name in {'cbc_only', 'hb_only'}:
        keep = CBC if name == 'cbc_only' else ['age_years', 'sex', 'hemoglobin']
        result.loc[:, [k for k in FEATURES if k not in keep]] = np.nan
    elif name == 'no_hemoglobin':
        result['hemoglobin'] = np.nan
    elif name == 'no_demographics':
        result[['age_years', 'sex']] = np.nan
    elif name.startswith('no_') and name[3:] in PANELS:
        result.loc[:, PANELS[name[3:]]] = np.nan
    else:
        raise ValueError(f'Unknown scenario: {name}')
    return result


def augment(frame, y, seed):
    views = [frame, scenario(frame, 'drop30', seed), scenario(frame, 'drop60', seed+1),
             scenario(frame, 'cbc_only', seed+2)]
    removed = frame.copy()
    rng = np.random.default_rng(seed+3)
    panels = list(PANELS.values())
    for idx in frame.index:
        removed.loc[idx, panels[int(rng.integers(len(panels)))]] = np.nan
    views.append(removed)
    return pd.concat(views, ignore_index=True), np.tile(np.asarray(y), len(views))


def model_inputs(frame, config):
    result = frame[CBC if config['panel'] == 'cbc' else FEATURES]
    return result.isna().astype(float) if config['algorithm'] == 'mask' else result


def make_model(config, seed, multiclass=False):
    kind = config['algorithm']
    if kind == 'catboost':
        return CatBoostClassifier(iterations=250, depth=4, learning_rate=.06,
                                  l2_leaf_reg=5, loss_function='MultiClass' if multiclass else 'Logloss',
                                  random_seed=seed, thread_count=4, verbose=False,
                                  allow_writing_files=False)
    if kind == 'dummy':
        return DummyClassifier(strategy='prior')
    if kind == 'mask':
        return LogisticRegression(C=1., max_iter=2000)
    values = SimpleImputer(strategy='median', keep_empty_features=True)
    prep = values if kind == 'logistic_values' else FeatureUnion([
        ('values', values), ('missing', MissingIndicator(features='all'))])
    if kind == 'extra_trees':
        estimator = ExtraTreesClassifier(n_estimators=250, max_depth=10,
                                        min_samples_leaf=3, max_features=.8,
                                        random_state=seed, n_jobs=4)
        return Pipeline([('preprocess', prep), ('model', estimator)])
    return Pipeline([('preprocess', prep), ('scale', StandardScaler()),
                     ('model', LogisticRegression(C=1., max_iter=3000))])


def fit(config, frame, y, seed, multiclass=False):
    if config.get('augment'):
        frame, y = augment(frame, y, seed)
    model = make_model(config, seed, multiclass)
    model.fit(model_inputs(frame, config), y)
    return model


def probabilities(model, frame, config, labels):
    raw = model.predict_proba(model_inputs(frame, config))
    ordered = np.zeros((len(frame), len(labels)))
    for j, label in enumerate(model.classes_):
        ordered[:, labels.index(label)] = raw[:, j]
    return ordered


def anemia_status(frame):
    known = frame.sex.notna() & frame.hemoglobin.notna()
    result = pd.Series(np.nan, index=frame.index)
    result.loc[known] = (frame.loc[known, 'hemoglobin'] <
                         np.where(frame.loc[known, 'sex'].eq(0), 120., 130.)).astype(float)
    return result


def target_probabilities(model, frame, config, labels, target):
    p = probabilities(model, frame, config, labels)
    if target == 'anemia_class' and config.get('hb_consistent'):
        from .advanced import hb_consistent
        p = hb_consistent(p, frame, labels)
    if target == 'inflammation_anemia' and config['algorithm'] != 'mask':
        # Label denotes an anemia etiology. With unknown Hb/sex, retain research prediction.
        no_anemia = anemia_status(frame).eq(0)
        p[no_anemia, 0] = 1.
        p[no_anemia, 1] = 0.
    return p
