"""Fixed baselines, development CV, selection, then a single holdout evaluation."""

import argparse
from datetime import datetime
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (accuracy_score, average_precision_score, brier_score_loss,
                             confusion_matrix, f1_score, log_loss,
                             precision_recall_fscore_support, roc_auc_score)
from sklearn.model_selection import StratifiedKFold, train_test_split
from .core import (ROOT, CONFIGS, DEFICIENCIES, DICTIONARY, FEATURES, LABS, SCENARIOS,
                   SELECTION_SCENARIOS, TARGETS, encode, fit, scenario, target_probabilities)


def metrics(y, p, labels, target):
    multiclass = target == 'anemia_class'
    predicted = np.asarray(labels)[p.argmax(axis=1)] if multiclass else (p[:, 1] >= .5).astype(int)
    precision, recall, f1, support = precision_recall_fscore_support(y, predicted, labels=labels, zero_division=0)
    result = {'n': len(y), 'accuracy': accuracy_score(y, predicted),
              'macro_f1': float(f1.mean()), 'log_loss': log_loss(y, p, labels=labels),
              'f1': f1_score(y, predicted, average='macro' if multiclass else 'binary', zero_division=0)}
    if not multiclass:
        result.update(positive_n=int(np.asarray(y).sum()), precision=float(precision[1]),
                      recall=float(recall[1]), specificity=float(recall[0]),
                      pr_auc=average_precision_score(y, p[:, 1]),
                      roc_auc=roc_auc_score(y, p[:, 1]), brier=brier_score_loss(y, p[:, 1]))
    else:
        result['multiclass_brier'] = float(np.square(p-(np.asarray(y)[:, None]==labels)).sum(axis=1).mean())
    per_class = [{'label': str(label), 'precision': float(precision[i]), 'recall': float(recall[i]),
                  'f1': float(f1[i]), 'support': int(support[i])} for i, label in enumerate(labels)]
    return result, per_class, predicted


def interval(y, p, labels, target, seed=973, iterations=300):
    """Patient bootstrap; approximate sampling uncertainty, not clinical transportability."""
    rng = np.random.default_rng(seed)
    predicted = np.asarray(labels)[p.argmax(axis=1)] if target == 'anemia_class' else (p[:, 1]>=.5).astype(int)
    values = []
    for _ in range(iterations):
        idx = rng.integers(len(y), size=len(y))
        values.append(f1_score(np.asarray(y)[idx], predicted[idx], labels=labels if target=='anemia_class' else None,
                               average='macro' if target=='anemia_class' else 'binary', zero_division=0))
    lo, hi = np.quantile(values, [.025, .975])
    return {'f1_ci_low': float(lo), 'f1_ci_high': float(hi)}


def run(source, output, seed=20261003, folds=5):
    if output.exists():
        raise ValueError(f'Use a new output directory; refusing to overwrite an experiment: {output}')
    data = pd.read_csv(source)
    feature_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if feature_hash != DICTIONARY['case_source']['sha256']:
        raise ValueError('Input differs from audited case CSV; audit the new source before training.')
    if len(data) != 840 or not data.patient_id.is_unique or data[TARGETS].isna().any().any():
        raise ValueError('Unexpected data shape/IDs/labels')
    # No source IDs or target values enter X. Full-feature duplicate patients require grouped splitting.
    if data[FEATURES].duplicated().any():
        raise ValueError('Repeated feature vectors: grouped splitting is required')
    x = encode(data)
    labels = {t: sorted(data[t].unique().tolist()) for t in TARGETS}
    dev, holdout = train_test_split(np.arange(len(data)), test_size=.2,
                                  stratify=data.anemia_class, random_state=seed)
    dev, holdout = np.sort(dev), np.sort(holdout)
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed+1)
    splits = list(splitter.split(dev, data.iloc[dev].anemia_class))
    fold_ids = np.full(len(data), -1)
    for fold, (_, val) in enumerate(splits):
        fold_ids[dev[val]] = fold
    for t in TARGETS:
        for tr, val in splits:
            if set(data.iloc[dev[tr]][t]) != set(labels[t]) or set(data.iloc[dev[val]][t]) != set(labels[t]):
                raise ValueError(f'Missing class in development fold: {t}')
    output.mkdir(parents=True)
    (output/'models').mkdir()
    pd.DataFrame({'row_index': np.arange(len(data)), 'partition': np.where(fold_ids<0, 'holdout', 'development'),
                  'fold': fold_ids}).to_csv(output/'splits.csv', index=False)
    # Generate once before model fitting, identically for every candidate. Test values never used to fit preprocessing.
    views = {name: scenario(x, name, seed+100+i) for i, name in enumerate(SCENARIOS)}
    manifest = {'experiment_version': '0.1.0', 'started_at': datetime.now().astimezone().isoformat(),
                'source_path': str(source), 'source_sha256': feature_hash,
                'dictionary_sha256': DICTIONARY['source']['sha256'], 'n': len(data),
                'development_n': len(dev), 'holdout_n': len(holdout), 'seed': seed, 'folds': folds,
                'features': FEATURES, 'targets': TARGETS, 'labels': labels,
                'configs': CONFIGS, 'scenarios': SCENARIOS, 'selection_scenarios': SELECTION_SCENARIOS,
                'selection_metric': 'Mean F1 across available/drop30/drop60 development OOF; multiclass macro-F1',
                'threshold': .5, 'hyperparameters': 'Fixed before evaluation; no grid search, early stopping or calibration',
                'external_training': False, 'cpu_threads_per_model': 4,
                'versions': {m: importlib.metadata.version(m) for m in ['numpy','pandas','scikit-learn','catboost','joblib','matplotlib']},
                'python': platform.python_version(), 'platform': platform.platform(),
                'source_code_sha256': {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}}
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    rows, classes, cache, timings = [], [], {}, []
    started = time.monotonic()
    for config in CONFIGS:
        for target in TARGETS:
            task_start = time.monotonic()
            y = data[target].to_numpy()
            oof = {name: np.zeros((len(dev), len(labels[target]))) for name in SCENARIOS}
            fold_scores = {name: [] for name in SCENARIOS}
            for fold, (tr, val) in enumerate(splits):
                model = fit(config, x.iloc[dev[tr]], y[dev[tr]], seed+fold,
                            multiclass=target=='anemia_class')
                for name in SCENARIOS:
                    p = target_probabilities(model, views[name].iloc[dev[val]], config, labels[target], target)
                    oof[name][val] = p
                    fold_scores[name].append(metrics(y[dev[val]], p, labels[target], target)[0]['f1'])
            for name in SCENARIOS:
                result, class_result, _ = metrics(y[dev], oof[name], labels[target], target)
                result.update(config=config['name'], target=target, scenario=name, partition='development_oof',
                              fold_f1_mean=float(np.mean(fold_scores[name])),
                              fold_f1_std=float(np.std(fold_scores[name], ddof=1)))
                rows.append(result)
                classes.extend({'config': config['name'], 'target': target, 'scenario': name,
                                'partition': 'development_oof', **c} for c in class_result)
                cache[f"{config['name']}__{target}__{name}"] = oof[name]
            elapsed = time.monotonic()-task_start
            timings.append({'config':config['name'], 'target':target, 'cv_seconds':elapsed})
            pd.DataFrame(rows).to_csv(output/'metrics.csv', index=False)
            print(f"CV {config['name']:20s} {target:22s} F1={rows[-len(SCENARIOS)]['f1']:.3f} ({elapsed:.1f}s)", flush=True)
    np.savez_compressed(output/'oof_probabilities.npz', **cache)
    cv = pd.DataFrame(rows)
    rankings = cv[cv.scenario.isin(SELECTION_SCENARIOS)].groupby(['target','config']).f1.mean().rename('selection_score').reset_index()
    cbc_ranking = cv[cv.config.str.endswith('_cbc') & cv.scenario.eq('available')][['target','config','f1']].rename(columns={'f1':'selection_score'})
    selected = {}
    for target in TARGETS:
        # Diagnostics/prior cannot be selected as deployable models. Stable tie ordering.
        candidates = rankings[(rankings.target==target)&~rankings.config.isin(['prior','missingness_only'])]
        primary = candidates.sort_values(['selection_score','config'], ascending=[False,True]).iloc[0]
        cbc = cbc_ranking[cbc_ranking.target==target].sort_values(['selection_score','config'], ascending=[False,True]).iloc[0]
        selected[target] = {'primary':primary.config, 'primary_cv_score':float(primary.selection_score),
                            'cbc':cbc.config, 'cbc_cv_score':float(cbc.selection_score)}
    # Persist decisions before any holdout metrics are computed. No test-guided choice.
    (output/'selection.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2))
    rankings.to_csv(output/'selection_scores.csv', index=False)
    final_models = {}
    test_cache = {}
    test_details = {}
    for config in CONFIGS:
        config_models = {}
        for target in TARGETS:
            model = fit(config, x.iloc[dev], data.iloc[dev][target].to_numpy(), seed,
                        multiclass=target=='anemia_class')
            config_models[target] = model
            if config['name'] in {selected[target]['primary'], selected[target]['cbc']}:
                final_models[(config['name'],target)] = model
                for name in SCENARIOS:
                    p = target_probabilities(model, views[name].iloc[holdout], config, labels[target], target)
                    y = data.iloc[holdout][target].to_numpy()
                    result, per_class, predicted = metrics(y, p, labels[target], target)
                    result.update(config=config['name'], target=target, scenario=name, partition='holdout')
                    if name in {'available', 'cbc_only'}:
                        result.update(interval(y, p, labels[target], target))
                    rows.append(result)
                    classes.extend({'config':config['name'], 'target':target,'scenario':name,
                                    'partition':'holdout', **c} for c in per_class)
                    key=f"{config['name']}__{target}__{name}"
                    test_cache[key]=p
                    test_details[key]={'labels':labels[target], 'confusion_matrix':confusion_matrix(y,predicted,labels=labels[target]).tolist()}
        joblib.dump({'schema_version':'1.0','config':config, 'labels':labels,'features':FEATURES,
                     'dictionary_sha256':DICTIONARY['source']['sha256'],
                     'models':config_models, 'training_rows':dev.tolist(), 'research_only':True},
                    output/'models'/f"{config['name']}.joblib", compress=3)
        print(f"Saved {config['name']}; holdout evaluated only if selected", flush=True)
    bundle = {'schema_version':'1.0', 'models':final_models, 'selected':selected,
              'configs':{c['name']:c for c in CONFIGS}, 'labels':labels, 'features':FEATURES,
              'training_rows':dev.tolist(), 'dictionary_sha256':DICTIONARY['source']['sha256'],
              'research_only':True, 'experiment':str(output), 'threshold':.5}
    joblib.dump(bundle, output/'models'/'selected.joblib', compress=3)
    np.savez_compressed(output/'holdout_probabilities.npz', **test_cache)
    (output/'holdout_confusion.json').write_text(json.dumps(test_details, ensure_ascii=False, indent=2))
    pd.DataFrame(rows).to_csv(output/'metrics.csv', index=False)
    pd.DataFrame(classes).to_csv(output/'per_class.csv', index=False)
    pd.DataFrame(timings).to_csv(output/'timings.csv', index=False)
    # Mixed decisions are derived at 0.5, not a probability of the conjunction.
    mixed_rows=[]
    for partition, indices, probabilities in [('development_oof',dev,cache),('holdout',holdout,test_cache)]:
        for name in SCENARIOS:
            positives = sum(probabilities[f"{selected[t]['primary']}__{t}__{name}"][:,1]>=.5 for t in DEFICIENCIES)
            decision = (positives>=2).astype(int)
            y = data.iloc[indices].mixed_deficiency.to_numpy()
            mixed_rows.append({'partition':partition,'scenario':name,'n':len(y), 'positive_n':int(y.sum()),
                               'f1':f1_score(y,decision,zero_division=0),
                               'precision':float(precision_recall_fscore_support(y,decision,labels=[0,1],zero_division=0)[0][1]),
                               'recall':float(precision_recall_fscore_support(y,decision,labels=[0,1],zero_division=0)[1][1])})
    pd.DataFrame(mixed_rows).to_csv(output/'mixed_metrics.csv',index=False)
    # Independent tests for anemia-free patients and class/deficiency agreement.
    subgroup_rows=[]
    for name in SCENARIOS:
        subset=data.iloc[holdout]
        for target in DEFICIENCIES:
            p=test_cache[f"{selected[target]['primary']}__{target}__{name}"]
            for group,mask in [('no_anemia',subset.anemia.eq(0)),('anemia',subset.anemia.eq(1)),
                               ('female',subset.sex.eq('F')),('male',subset.sex.eq('M'))]:
                y=subset.loc[mask,target].to_numpy(); prob=p[mask.to_numpy(),1]
                pr,re,f1,support=precision_recall_fscore_support(y,prob>=.5,labels=[0,1],zero_division=0)
                subgroup_rows.append({'scenario':name,'target':target,'group':group,'n':len(y),
                                      'positive_n':int(y.sum()),'f1':float(f1[1]),'precision':float(pr[1]),'recall':float(re[1])})
    pd.DataFrame(subgroup_rows).to_csv(output/'subgroups.csv',index=False)
    manifest['elapsed_seconds']=time.monotonic()-started
    manifest['finished_at']=datetime.now().astimezone().isoformat()
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    from .report import write_report
    write_report(output)
    print(f"Finished {output} in {manifest['elapsed_seconds']:.1f}s", flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path(DICTIONARY['case_source']['path']))
    parser.add_argument('--output',type=Path,default=ROOT/'experiments'/'baseline_v1')
    args=parser.parse_args()
    run(args.source.resolve(),args.output.resolve())


if __name__=='__main__':
    main()
