"""Frozen, inference-only external biochemical benchmark; never invent clinical labels.

Run `freeze` before `evaluate`. Model, mapping and code hashes are verified before
deserializing trusted weights. No fitting or threshold selection occurs here.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from .core import ROOT, FEATURES, LABS, CBC, PANELS, TARGETS, encode, target_probabilities
from .calibration import calibrate_scores
from scripts.prepare_nhanes import convert

OUTPUT = ROOT / 'experiments/external_validation_v1'
NUTRITION = 'https://stacks.cdc.gov/view/cdc/11790/cdc_11790_DS1.pdf'
REFERENCES = {
    'low_ferritin': {'target': 'iron_deficiency', 'marker': 'ferritin', 'cutoff': 15., 'panel': 'iron', 'unit': 'µg/L'},
    'low_B12': {'target': 'B12_deficiency', 'marker': 'vitamin_B12', 'cutoff': 200., 'panel': 'B12', 'unit': 'pg/mL'},
    'low_folate': {'target': 'folate_deficiency', 'marker': 'folate', 'cutoff': 2., 'panel': 'folate', 'unit': 'ng/mL'},
    'low_PLP': {'target': 'B6_deficiency', 'marker': 'vitamin_B6', 'cutoff': 20., 'panel': 'B6', 'unit': 'nmol/L'},
}
CODE = ['ml_baselines/external_validate.py', 'ml_baselines/core.py', 'ml_baselines/advanced.py',
        'ml_baselines/calibration.py', 'scripts/prepare_nhanes.py', 'scripts/fetch_nhanes_validation.py']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def mapping_2005():
    old = json.loads((ROOT / 'data/external/unit_mapping.json').read_text())['sources']['nhanes_2003_2004']['entries']
    tables = {'DEMO_C': 'DEMO_D', 'L25_C': 'CBC_D', 'L40_C': 'BIOPRO_D',
              'L40FE_C': 'FETIB_D', 'L43_C': 'VIT_B6_D', 'L11_C': 'CRP_D'}
    special = {'ferritin': 'FERTIN_D', 'sTfR': 'TFR_D', 'vitamin_B12': 'B12_D',
               'folate': 'FOLATE_D', 'homocysteine': 'HCY_D'}
    entries = []
    for original in old:
        if original['target'] == 'MMA':
            continue  # Not measured in these 2005–2006 components.
        rule = deepcopy(original)
        rule['table'] = special.get(rule['target'], tables.get(rule['table']))
        rule['source_url'] = f"https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2005/DataFiles/{rule['table']}.htm"
        if rule['target'] == 'vitamin_B6':
            rule.update(column='LBXPLP', notes='Serum PLP, HPLC; not the 2003 enzymatic assay.')
        if rule['target'] == 'homocysteine':
            rule['notes'] = 'Use published reagent-adjusted plasma result; do not adjust twice.'
        if rule['target'] in {'ferritin', 'sTfR'}:
            rule['notes'] = 'Adults: measured only in women aged 12–49. Not general adult coverage.'
        entries.append(rule)
    return entries


def freeze(output):
    if output.exists():
        raise ValueError('Use a new experiment directory; protocol cannot be overwritten')
    output.mkdir(parents=True)
    trust = json.loads((ROOT / 'backend/data/model_trust.json').read_text())['sha256']
    models = {name: {'path': f'experiments/{name}/models/selected.joblib',
                     'sha256': trust[f'experiments/{name}/models/selected.joblib']}
              for name in ['baseline_v1', 'robust_v2', 'calibrated_v3']}
    for item in models.values():
        if sha(ROOT / item['path']) != item['sha256']:
            raise ValueError('Trusted model hash differs')
    protocol = {
        'version': 'external-biochemical-validation-1', 'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
        'champion': 'baseline_v1', 'comparators': ['robust_v2', 'calibrated_v3'], 'models': models,
        'threshold': .5, 'seed': 20261005, 'bootstrap_replicates': 500,
        'primary_cohort': 'nhanes_2005_2006', 'clinical_truth_available': False,
        'endpoints': REFERENCES, 'reference_source': NUTRITION, 'comparison': 'strictly_less_than',
        'views': ['available', 'reference_panel_withheld', 'cbc_only'],
        'primary_view': 'reference_panel_withheld',
        'eligibility': 'age>=18; exclude known pregnancy and women 18–44 with unknown pregnancy; >=5 measured input labs per view',
        'negative_reference_meaning': 'marker not below cutoff; does NOT mean no clinical deficiency',
        'routing': 'per patient: CBC iff all observed labs belong to CBC; otherwise primary, as in predict(auto)',
        'incorporation_bias': 'available includes the reference analyte, so is a concordance check only',
        'intervals': '500 bootstrap resamples of PSUs within each stratum, unweighted benchmark; not population prevalence',
        'release_policy': 'No promotion, retraining, recalibration or threshold tuning from biochemical proxies. Clinical validation of 12 etiologies remains required.',
        'excluded_candidate': 'transfer_v3 used Kılıçarslan for training; not evaluated as independent',
        'mapping_2005': mapping_2005(),
        'case_sha256': sha(ROOT / 'data/case/deficiency_anemia.csv'),
        'dictionary_sha256': sha(ROOT / 'data/feature_dictionary.json'),
        'code_sha256': {name: sha(ROOT / name) for name in CODE},
    }
    write_json(output / 'protocol.json', protocol)
    (output / 'protocol.sha256').write_text(sha(output / 'protocol.json') + '\n')
    snapshot = output / 'source_snapshot'
    for name in CODE:
        dest = snapshot / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / name).read_bytes())
    print('Protocol frozen:', sha(output / 'protocol.json'))


def load_locked(output):
    if sha(output / 'protocol.json') != (output / 'protocol.sha256').read_text().strip():
        raise ValueError('Protocol hash differs')
    p = json.loads((output / 'protocol.json').read_text())
    for name, digest in p['code_sha256'].items():
        if sha(ROOT / name) != digest:
            raise ValueError(f'Frozen code changed: {name}')
    for name, digest in [('data/case/deficiency_anemia.csv', p['case_sha256']), ('data/feature_dictionary.json', p['dictionary_sha256'])]:
        if sha(ROOT / name) != digest:
            raise ValueError(f'Frozen source changed: {name}')
    return p


def load_model(item):
    raw = (ROOT / item['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != item['sha256']:
        raise ValueError('Model hash differs before deserialization')
    return joblib.load(io.BytesIO(raw))


def make_references(features, endpoints):
    result = pd.DataFrame(index=features.index)
    for name, rule in endpoints.items():
        marker = features[rule['marker']]
        result[name] = np.where(marker.notna(), marker.lt(rule['cutoff']).astype(float), np.nan)
    return result


def input_view(features, view, panel):
    frame = features.copy()
    if view == 'reference_panel_withheld':
        frame.loc[:, PANELS[panel]] = np.nan
    elif view == 'cbc_only':
        frame.loc[:, [name for name in FEATURES if name not in CBC]] = np.nan
    elif view != 'available':
        raise ValueError('Unknown view')
    return frame


def routes(frame):
    extended = [name for name in LABS if name not in CBC]
    return np.where(frame[extended].notna().any(axis=1), 'primary', 'cbc')


def scores(bundle, frame, target):
    output = np.empty(len(frame))
    route = routes(frame)
    for panel in ['primary', 'cbc']:
        mask = route == panel
        if not mask.any():
            continue
        part = frame.loc[mask]
        name = bundle['selected'][target][panel]
        labels = bundle['labels'][target]
        p = target_probabilities(bundle['models'][(name, target)], part, bundle['configs'][name], labels, target)
        calibrator = bundle.get('calibrators', {}).get((target, panel))
        if calibrator is not None:
            p = calibrate_scores(calibrator, p, part, target)
        output[mask] = p[:, labels.index(1)]
    if not np.isfinite(output).all() or (output < 0).any() or (output > 1).any():
        raise ValueError('Invalid model score')
    return output


def fingerprints(frame):
    # Exact shared CBC vector including sex/age; no fuzzy patient matching.
    return pd.util.hash_pandas_object(frame[CBC], index=False)


def prepare(p, output):
    folder = ROOT / 'data/external/raw/nhanes_2005_2006'
    download = json.loads((folder / 'download_manifest.json').read_text())
    for item in download:
        if sha(ROOT / item['path']) != item['sha256']:
            raise ValueError('Downloaded source changed')
    tables = {Path(r['path']).stem: pd.read_sas(ROOT / r['path'], format='xport').set_index('SEQN', verify_integrity=True) for r in download}
    demo = tables['DEMO_D']
    unknown = demo.RIAGENDR.eq(2) & demo.RIDAGEYR.between(18, 44) & ~demo.RIDEXPRG.isin([1, 2])
    eligible = demo.RIDAGEYR.ge(18) & ~demo.RIDEXPRG.eq(1) & ~unknown
    index = demo.index[eligible]
    result = pd.DataFrame(np.nan, index=index, columns=FEATURES)
    result['sex'] = pd.Series(index=index, dtype=object)
    for rule in p['mapping_2005']:
        result[rule['target']] = convert(tables[rule['table']][rule['column']].reindex(index), rule)
    numeric = result.drop(columns='sex').to_numpy(dtype=float)
    if np.isinf(numeric).any() or (numeric < 0).any():
        raise ValueError('Invalid normalized feature')
    case = encode(pd.read_csv(ROOT / 'data/case/deficiency_anemia.csv'))
    encoded = encode(result)
    complete_cbc = encoded[CBC].notna().all(axis=1)
    overlap = complete_cbc & fingerprints(encoded).isin(fingerprints(case.loc[case[CBC].notna().all(axis=1)]))
    duplicated = result.duplicated(FEATURES) & result[LABS].notna().any(axis=1)
    keep = ~overlap & ~duplicated
    ids = pd.Series([f'nhanes_2005_2006_SEQN_{int(i)}' for i in index], index=index)
    meta = pd.DataFrame({'patient_id': ids, 'SEQN': index.astype(int), 'pregnancy_code': demo.RIDEXPRG.reindex(index),
                         'age_topcoded': demo.RIDAGEYR.reindex(index).ge(85),
                         'stratum': demo.SDMVSTRA.reindex(index), 'psu': demo.SDMVPSU.reindex(index),
                         'WTMEC2YR': demo.WTMEC2YR.reindex(index), 'excluded_case_cbc_match': overlap,
                         'excluded_duplicate': duplicated})
    if meta[['stratum', 'psu']].isna().any().any():
        raise ValueError('Missing survey design')
    meta.to_csv(output / 'cohort_audit.csv', index=False)
    result = result.loc[keep].reset_index(drop=True)
    meta = meta.loc[keep].reset_index(drop=True)
    features = pd.concat([meta[['patient_id']], result], axis=1)
    features.to_csv(output / 'features.csv', index=False)
    references = make_references(result, p['endpoints'])
    pd.concat([meta[['patient_id']], references], axis=1).to_csv(output / 'biochemical_references.csv', index=False)
    unknown_labels = pd.DataFrame({'patient_id': meta.patient_id})
    for target in TARGETS:
        unknown_labels[target] = np.nan
        unknown_labels[f'known_{target}'] = 0
    unknown_labels.to_csv(output / 'clinical_labels_unknown.csv', index=False)
    audit = {'source_rows': len(demo), 'adults': int(demo.RIDAGEYR.ge(18).sum()),
             'known_pregnancy_adults_excluded': int((demo.RIDAGEYR.ge(18) & demo.RIDEXPRG.eq(1)).sum()),
             'unknown_pregnancy_women_18_44_excluded': int(unknown.sum()), 'eligible_before_duplicates': len(index),
             'case_exact_complete_cbc_matches': int(overlap.sum()), 'duplicates_excluded': int(duplicated.sum()),
             'final_rows': len(result), 'normalization_entries': len(p['mapping_2005']),
             'source_sha256': {item['path']: item['sha256'] for item in download},
             'biochemical_positive_counts': {name: int(references[name].eq(1).sum()) for name in references},
             'clinical_known_cells': 0,
             'identity_limit': 'Distinct public survey source; no common patient registry. Exact CBC fingerprint audit cannot establish identity.'}
    write_json(output / 'cohort_manifest.json', audit)
    return result, meta, references


def bootstrap_weights(meta, replicates, seed):
    # Resample sampled PSUs inside each stratum; preserve within-PSU correlation.
    keys = list(zip(meta.stratum, meta.psu))
    groups = sorted(set(keys))
    inverse = np.array([groups.index(key) for key in keys])
    rng = np.random.default_rng(seed)
    weights = np.zeros((replicates, len(groups)))
    for stratum in sorted(set(meta.stratum)):
        cols = [i for i, key in enumerate(groups) if key[0] == stratum]
        sampled = rng.choice(cols, size=(replicates, len(cols)), replace=True)
        for col in cols:
            weights[:, col] = (sampled == col).sum(axis=1)
    return weights, inverse


def confusion_metrics(counts):
    tn, fp, fn, tp = np.asarray(counts).T
    def ratio(a, b):
        return np.divide(a, b, out=np.full(np.shape(a), np.nan, dtype=float), where=b > 0)
    return {'sensitivity': ratio(tp, tp+fn), 'specificity': ratio(tn, tn+fp),
            'precision': ratio(tp, tp+fp), 'F1': ratio(2*tp, 2*tp+fp+fn)}


def measure(y, p, cluster, weights, threshold):
    y = np.asarray(y, dtype=int)
    pred = p >= threshold
    cell = 2*y + pred.astype(int)
    counts = np.bincount(cell, minlength=4)
    metrics = {key: float(value) for key, value in confusion_metrics(counts).items()}
    per_cluster = np.zeros((weights.shape[1], 4))
    np.add.at(per_cluster, (cluster, cell), 1)
    replicates = confusion_metrics(weights @ per_cluster)
    for key, values in replicates.items():
        valid = values[np.isfinite(values)]
        lo, hi = np.quantile(valid, [.025, .975]) if len(valid) else [np.nan, np.nan]
        metrics.update({f'{key}_ci_low': lo, f'{key}_ci_high': hi, f'{key}_bootstrap_valid': len(valid)})
    clipped = np.clip(p, 1e-7, 1-1e-7)
    metrics.update(n=len(y), positives=int(y.sum()), negatives=int((1-y).sum()),
                   tn=int(counts[0]), fp=int(counts[1]), fn=int(counts[2]), tp=int(counts[3]),
                   Brier=float(np.mean((p-y)**2)),
                   log_loss=float(-np.mean(y*np.log(clipped)+(1-y)*np.log(1-clipped))),
                   AUROC=float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else np.nan,
                   AP=float(average_precision_score(y, p)) if y.sum() else np.nan,
                   mean_score=float(p.mean()), reference_positive_fraction=float(y.mean()),
                   adequate_positive_count=bool(y.sum() >= 30))
    bins = np.minimum((p*10).astype(int), 9)
    metrics['ECE10'] = sum(np.mean(bins == b)*abs(p[bins == b].mean()-y[bins == b].mean())
                           for b in range(10) if (bins == b).any())
    return metrics


def evaluate(output):
    p = load_locked(output)
    if (output / 'metrics.csv').exists():
        raise ValueError('Results already exist; do not overwrite frozen assessment')
    features, meta, references = prepare(p, output)
    frame = encode(features)
    weights, cluster = bootstrap_weights(meta, p['bootstrap_replicates'], p['seed'])
    bundles = {name: load_model(item) for name, item in p['models'].items()}
    case = pd.read_csv(ROOT / 'data/case/deficiency_anemia.csv')
    train_rows = bundles[p['champion']]['training_rows']
    priors = {r['target']: float(case.iloc[train_rows][r['target']].mean()) for r in p['endpoints'].values()}
    rows, subgroup_rows, predictions = [], [], []
    for endpoint, rule in p['endpoints'].items():
        for view in p['views']:
            x = input_view(frame, view, rule['panel'])
            eligible = references[endpoint].notna() & x[LABS].notna().sum(axis=1).ge(5)
            y = references.loc[eligible, endpoint].astype(int).to_numpy()
            route = routes(x.loc[eligible])
            for model in [*bundles, 'case_training_prior']:
                s = scores(bundles[model], x.loc[eligible], rule['target']) if model in bundles else np.full(len(y), priors[rule['target']])
                fields = {'model': model, 'endpoint': endpoint, 'target': rule['target'], 'view': view,
                          'marker_available_count': int(references[endpoint].notna().sum()),
                          'excluded_sparse': int((references[endpoint].notna() & ~eligible).sum()),
                          'cbc_routed_count': int((route == 'cbc').sum()), 'primary_routed_count': int((route == 'primary').sum())}
                rows.append({**fields, **measure(y, s, cluster[eligible], weights, p['threshold'])})
                predictions.append(pd.DataFrame({'patient_id': meta.loc[eligible, 'patient_id'].to_numpy(),
                                                  'model': model, 'endpoint': endpoint, 'view': view,
                                                  'reference': y, 'score': s, 'route': route}))
                submeta = features.loc[eligible]
                subgroup = {'female': submeta.sex.eq('F'), 'male': submeta.sex.eq('M'),
                            'age18_49': submeta.age_years.between(18,49), 'age50_79': submeta.age_years.between(50,79),
                            'age80plus': submeta.age_years.ge(80),
                            'Hb_low_by_case': submeta.hemoglobin.lt(np.where(submeta.sex.eq('F'),120,130)),
                            'Hb_not_low_by_case': submeta.hemoglobin.ge(np.where(submeta.sex.eq('F'),120,130))}
                for name, mask in subgroup.items():
                    mask = mask.to_numpy()
                    if mask.any():
                        subgroup_rows.append({**fields, 'subgroup': name,
                                              **measure(y[mask], s[mask], cluster[eligible][mask], weights, p['threshold'])})
            print(endpoint, view, len(y), 'positive', int(y.sum()), flush=True)
    pd.DataFrame(rows).to_csv(output / 'metrics.csv', index=False)
    pd.DataFrame(subgroup_rows).to_csv(output / 'subgroup_metrics.csv', index=False)
    pd.concat(predictions, ignore_index=True).to_csv(output / 'predictions.csv', index=False)
    shift = []
    for name in FEATURES:
        a, b = encode(case)[name], frame[name]
        shift.append({'feature': name, 'case_observed': int(a.notna().sum()), 'external_observed': int(b.notna().sum()),
                      'case_missing_fraction': float(a.isna().mean()), 'external_missing_fraction': float(b.isna().mean()),
                      'case_median': a.median(), 'external_median': b.median(),
                      'case_p01': a.quantile(.01), 'case_p99': a.quantile(.99),
                      'external_p01': b.quantile(.01), 'external_p99': b.quantile(.99)})
    pd.DataFrame(shift).to_csv(output / 'distribution_shift.csv', index=False)
    decision = {'default_model': p['champion'], 'default_sha256': p['models'][p['champion']]['sha256'],
                'weights_changed': False, 'threshold_changed': False, 'external_biochemical_benchmark_completed': True,
                'clinicalValidated': False, 'independentClinicalValidation': False,
                'etiology_classes_independently_validated': 0, 'reason': p['release_policy'],
                'protocol_sha256': sha(output / 'protocol.json')}
    write_json(output / 'release_decision.json', decision)
    write_json(output / 'artifact_hashes.json', {str(path.relative_to(output)): sha(path)
               for path in sorted(output.rglob('*')) if path.is_file() and path.name != 'artifact_hashes.json'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['freeze', 'evaluate'])
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    (freeze if args.stage == 'freeze' else evaluate)(args.output)


if __name__ == '__main__':
    main()
