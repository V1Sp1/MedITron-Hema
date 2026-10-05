"""v4: real-data marker models, development-only selection, then locked external test."""
import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score, log_loss
from sklearn.model_selection import StratifiedGroupKFold

from .biochemical import ENDPOINTS, CONFIGS, BiochemicalClassifier, feature_names, predictor_frame, drop_observed
from .core import ROOT, FEATURES, LABS, CBC, encode
from .external_validate import sha, write_json, load_model, scores as legacy_scores, bootstrap_weights, measure
from .nhanes_v4 import existing_cohort, new_mapping, test_cohort, audit_test

OUTPUT = ROOT/'experiments/biochemical_v4'
CODE = ['ml_baselines/train_biochemical.py','ml_baselines/biochemical.py','ml_baselines/nhanes_v4.py',
        'ml_baselines/core.py','ml_baselines/advanced.py','ml_baselines/calibration.py',
        'ml_baselines/external_validate.py','scripts/prepare_nhanes.py','scripts/fetch_nhanes_v4.py']


def source_paths():
    result = ['data/case/deficiency_anemia.csv','data/feature_dictionary.json',
              'experiments/external_validation_v1/features.csv','experiments/external_validation_v1/cohort_audit.csv']
    for cycle in ['nhanes_2003_2004','nhanes_2011_2012']:
        for kind in ['features','metadata']:
            result.append(f'data/external/processed/nhanes_case_units_v1/{cycle}_{kind}.csv')
    return result


def freeze(output):
    if output.exists():
        raise ValueError('Use a new output directory')
    output.mkdir(parents=True)
    path = 'experiments/baseline_v1/models/selected.joblib'
    trusted = json.loads((ROOT/'backend/data/model_trust.json').read_text())['sha256'][path]
    if sha(ROOT/path) != trusted:
        raise ValueError('Baseline is not the frozen trusted model')
    protocol = {'version':'biochemical-v4-1','frozen_at_utc':datetime.now(timezone.utc).isoformat(),
        'seed':20261005,'clinical_targets_trained':False,'endpoints':ENDPOINTS,'configs':CONFIGS,
        'routes':['extended','cbc'],'cv_folds':5,'bootstrap_replicates':500,
        'reference_source':'https://stacks.cdc.gov/view/cdc/11790/cdc_11790_DS1.pdf',
        'features':{e:{r:feature_names(e,r) for r in ['extended','cbc']} for e in ENDPOINTS},
        'eligibility':'age>=18 (B12>=20); pregnancy/unknown pregnancy in women 18–44 excluded; iron only F18–49; >=5 measured supported labs, extended needs >=1 supported biochemical predictor',
        'age_transform':'predictor age capped at 80 across all cycles; published source ages preserved',
        'unknown_references':'missing marker excluded for its own endpoint, never labeled negative',
        'development_views':['available','drop30'],'external_views':['available','drop30','cbc_only'],
        'selection':'highest mean AP across development OOF available/drop30; then mean AUROC; then lower log loss',
        'threshold_rule':'lowest threshold giving specificity>=0.90 in BOTH development OOF views; no clinical acceptance claim',
        'grouping':'5-fold StratifiedGroupKFold by cycle/stratum/PSU; all patient copies stay in training partition',
        'external_gate':{'minimum_positives':30,'minimum_specificity':.85,'minimum_F1_gain_vs_v1_tuned':.02,
                         'paired_sensitivity_gain_ci_low_above':0.,'stress_F1_drop_vs_v1_tuned_at_most':.02},
        'release_policy':'Never replace clinical deficiency/anemia-class heads with marker heads. Accepted heads can only be exposed as a separate research biochemical predictor.',
        'baseline':{'path':path,'sha256':trusted},
        'test_mapping':{cycle:new_mapping(cycle) for cycle in ['nhanes_2007_2008','nhanes_2013_2014']},
        'test_source_sha256':{str(path.relative_to(ROOT)):sha(path)
            for cycle in ['nhanes_2007_2008','nhanes_2013_2014']
            for path in sorted((ROOT/'data/external/raw'/cycle).glob('*')) if path.is_file()},
        'source_sha256':{name:sha(ROOT/name) for name in source_paths()},
        'code_sha256':{name:sha(ROOT/name) for name in CODE}}
    write_json(output/'protocol.json',protocol)
    (output/'protocol.sha256').write_text(sha(output/'protocol.json')+'\n')
    for name in CODE:
        dest = output/'source_snapshot'/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes((ROOT/name).read_bytes())
    print('v4 protocol frozen',sha(output/'protocol.json'),flush=True)


def locked(output):
    if sha(output/'protocol.json') != (output/'protocol.sha256').read_text().strip():
        raise ValueError('Protocol changed')
    p = json.loads((output/'protocol.json').read_text())
    for name,digest in {**p['source_sha256'],**p['code_sha256']}.items():
        if sha(ROOT/name) != digest:
            raise ValueError(f'Frozen dependency changed: {name}')
    return p


def frame37(x):
    return x.reindex(columns=FEATURES)


def threshold_for_specificity(labels, views, floor=.9):
    y = np.asarray(labels,dtype=int)
    if not (y == 0).any() or not (y == 1).any():
        raise ValueError('Threshold selection needs known positives and negatives')
    bounds = []
    for score in views.values():
        score = np.asarray(score)
        if not np.isfinite(score).all():
            raise ValueError('Invalid development score')
        negative = np.sort(score[y == 0])
        kth = int(np.ceil(floor*len(negative)))-1
        bounds.append(float(np.nextafter(negative[kth],np.inf)))
    return max(bounds)


def statistics(y, score, threshold):
    y = np.asarray(y,dtype=int)
    pred = score >= threshold
    tp,fp = int((pred & (y == 1)).sum()),int((pred & (y == 0)).sum())
    fn,tn = int((~pred & (y == 1)).sum()),int((~pred & (y == 0)).sum())
    return {'n':len(y),'positives':int(y.sum()),'AP':float(average_precision_score(y,score)),
            'AUROC':float(roc_auc_score(y,score)), 'log_loss':float(log_loss(y,np.clip(score,1e-7,1-1e-7))),
            'Brier':float(np.mean((score-y)**2)), 'sensitivity':tp/(tp+fn), 'specificity':tn/(tn+fp),
            'precision':tp/(tp+fp) if tp+fp else 0., 'F1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.,
            'tp':tp,'fp':fp,'fn':fn,'tn':tn,'threshold':threshold}


def endpoint_data(features,meta,endpoint,route):
    rule = ENDPOINTS[endpoint]
    x = predictor_frame(encode(features),endpoint,route)
    keep = features[rule['marker']].notna() & features.age_years.ge(rule['minimum_age'])
    if endpoint == 'low_ferritin':
        keep &= features.sex.eq('F') & features.age_years.le(49)
    keep &= x[[name for name in x if name in LABS]].notna().sum(axis=1).ge(5)
    if route == 'extended':
        keep &= x[[name for name in x if name not in CBC]].notna().any(axis=1)
    f,m = features.loc[keep].reset_index(drop=True),meta.loc[keep].reset_index(drop=True)
    return f,m,(f[rule['marker']] < rule['cutoff']).astype(int).to_numpy()


def train(output):
    p = locked(output)
    if (output/'selection.json').exists():
        raise ValueError('Development selection already exists')
    baseline = load_model(p['baseline'])
    cycles = sorted({c for rule in ENDPOINTS.values() for c in rule['train_cycles']})
    cohorts = {cycle:existing_cohort(cycle) for cycle in cycles} # No access to held-out test tables.
    bundle = {'model_family':'biochemical_v4','research_only':True,'clinicalValidated':False,
              'features':FEATURES,'dictionary_file_sha256':p['source_sha256']['data/feature_dictionary.json'],
              'heads':{},'thresholds':{},'legacy_thresholds':{},'training_patients':{},'protocol_sha256':sha(output/'protocol.json')}
    selection,rows,fold_rows,split_rows,audits,cache = {},[],[],[],[],{}
    for endpoint,rule in ENDPOINTS.items():
        full = pd.concat([cohorts[c][0] for c in rule['train_cycles']],ignore_index=True)
        meta = pd.concat([cohorts[c][1] for c in rule['train_cycles']],ignore_index=True)
        if not full.patient_id.is_unique:
            raise ValueError('Duplicate patient IDs in development')
        # Drop complete duplicate vectors before folds; record patient membership.
        dedupe = ~full.duplicated(FEATURES)
        full,meta = full.loc[dedupe].reset_index(drop=True),meta.loc[dedupe].reset_index(drop=True)
        for route in p['routes']:
            f,m,y = endpoint_data(full,meta,endpoint,route)
            x = predictor_frame(encode(f),endpoint,route)
            views = {'available':x,'drop30':drop_observed(x,p['seed']+101)}
            splitter = StratifiedGroupKFold(n_splits=p['cv_folds'],shuffle=True,random_state=p['seed'])
            splits = list(splitter.split(x,y,m.group))
            for fold,(learn,valid) in enumerate(splits):
                if set(y[learn]) != {0,1} or set(y[valid]) != {0,1}:
                    raise ValueError('A fold has no known positive/negative examples')
                if set(m.group.iloc[learn]) & set(m.group.iloc[valid]):
                    raise ValueError('PSU leakage')
                audits.append({'endpoint':endpoint,'route':route,'fold':fold,
                    'learn_patients':f.patient_id.iloc[learn].tolist(),'valid_patients':f.patient_id.iloc[valid].tolist(),
                    'learn_groups':sorted(set(m.group.iloc[learn])),'valid_groups':sorted(set(m.group.iloc[valid]))})
                split_rows.extend({'endpoint':endpoint,'route':route,'patient_id':f.patient_id.iloc[i],
                    'cycle':f.cycle.iloc[i],'group':m.group.iloc[i],'fold':fold,'reference':int(y[i])} for i in valid)
            candidates = {}
            for config in p['configs']:
                oof = {view:np.empty(len(y)) for view in views}
                for fold,(learn,valid) in enumerate(splits):
                    head = BiochemicalClassifier(endpoint,route,config,p['seed']+fold).fit(x.iloc[learn],y[learn])
                    for view,frame in views.items():
                        oof[view][valid] = head.predict_score(frame.iloc[valid])
                threshold = threshold_for_specificity(y,oof)
                metrics = []
                for view,score in oof.items():
                    stats = statistics(y,score,threshold)
                    rows.append({'endpoint':endpoint,'route':route,'model':config['name'],'view':view,**stats})
                    for fold,(_,valid) in enumerate(splits):
                        fold_rows.append({'endpoint':endpoint,'route':route,'model':config['name'],'view':view,'fold':fold,
                            **statistics(y[valid],score[valid],threshold)})
                    cache[f'{endpoint}__{route}__{config["name"]}__{view}'] = score
                    metrics.append(stats)
                rank = (np.mean([s['AP'] for s in metrics]),np.mean([s['AUROC'] for s in metrics]),-np.mean([s['log_loss'] for s in metrics]))
                candidates[config['name']] = {'rank':tuple(float(v) for v in rank),'threshold':threshold,'config':config}
                print(endpoint,route,config['name'],'AP',round(rank[0],4),'threshold',round(threshold,5),flush=True)
            best = max(candidates,key=lambda name:candidates[name]['rank'])
            chosen = candidates[best]
            legacy = {view:legacy_scores(baseline,frame37(frame),rule['legacy_target']) for view,frame in views.items()}
            legacy_threshold = threshold_for_specificity(y,legacy)
            for view,score in legacy.items():
                cache[f'{endpoint}__{route}__legacy__{view}'] = score
                for name,threshold in [('v1_default',.5),('v1_tuned',legacy_threshold)]:
                    rows.append({'endpoint':endpoint,'route':route,'model':name,'view':view,**statistics(y,score,threshold)})
            key = f'{endpoint}/{route}'
            selection[key] = {'selected':best,'threshold':chosen['threshold'],'legacy_threshold':legacy_threshold,
                               'candidates':candidates,'n':len(y),'positives':int(y.sum()),
                               'training_cycles':rule['train_cycles']}
            bundle['heads'][(endpoint,route)] = BiochemicalClassifier(endpoint,route,chosen['config'],p['seed']).fit(x,y)
            bundle['thresholds'][(endpoint,route)] = chosen['threshold']
            bundle['legacy_thresholds'][(endpoint,route)] = legacy_threshold
            bundle['training_patients'][(endpoint,route)] = f.patient_id.tolist()
            cache[f'{endpoint}__{route}__y'] = y
    write_json(output/'selection.json',selection)
    write_json(output/'split_audit.json',audits)
    pd.DataFrame(rows).to_csv(output/'development_metrics.csv',index=False)
    pd.DataFrame(fold_rows).to_csv(output/'fold_metrics.csv',index=False)
    pd.DataFrame(split_rows).to_csv(output/'splits.csv',index=False)
    np.savez_compressed(output/'development_scores.npz',**cache)
    (output/'models').mkdir()
    joblib.dump(bundle,output/'models/selected.joblib',compress=3)
    write_json(output/'selection_lock.json',{'locked_at_utc':datetime.now(timezone.utc).isoformat(),
        'selection_sha256':sha(output/'selection.json'),'model_sha256':sha(output/'models/selected.joblib'),
        'protocol_sha256':sha(output/'protocol.json'),'test_results_seen':False})
    print('Development selection and weights locked before external evaluation',flush=True)


def external_scores(bundle,frame,endpoint):
    extras = [name for name in feature_names(endpoint,'extended') if name not in CBC]
    routes = np.where(frame[extras].notna().any(axis=1),'extended','cbc')
    scores,thresholds,legacy_thresholds = (np.empty(len(frame)) for _ in range(3))
    for route in ['extended','cbc']:
        mask = routes == route
        if mask.any():
            scores[mask] = bundle['heads'][(endpoint,route)].predict_score(frame.loc[mask])
            thresholds[mask] = bundle['thresholds'][(endpoint,route)]
            legacy_thresholds[mask] = bundle['legacy_thresholds'][(endpoint,route)]
    return scores,thresholds,legacy_thresholds,routes


def paired_interval(y,a,b,cluster,weights,ta,tb):
    arrays = []
    for score,threshold in [(a,ta),(b,tb)]:
        matrix = np.zeros((weights.shape[1],2))
        pred = score >= threshold
        np.add.at(matrix,(cluster[y == 1],pred[y == 1].astype(int)),1)
        counts = weights @ matrix
        arrays.append(np.divide(counts[:,1],counts.sum(axis=1),out=np.full(len(weights),np.nan),where=counts.sum(axis=1)>0))
    delta = arrays[0]-arrays[1]
    delta = delta[np.isfinite(delta)]
    return [float(v) for v in np.quantile(delta,[.025,.975])]


def evaluate(output):
    p = locked(output)
    if (output/'external_metrics.csv').exists():
        raise ValueError('External results already seen; no overwrite')
    for name,digest in p['test_source_sha256'].items():
        if sha(ROOT/name) != digest:
            raise ValueError('Frozen test source changed')
    lock = json.loads((output/'selection_lock.json').read_text())
    if sha(output/'selection.json') != lock['selection_sha256'] or sha(output/'models/selected.joblib') != lock['model_sha256']:
        raise ValueError('Development selection/weights changed')
    bundle = load_model({'path':str(output/'models/selected.joblib'),'sha256':lock['model_sha256']})
    baseline = load_model(p['baseline'])
    cohorts = {cycle:existing_cohort(cycle) for cycle in sorted({c for rule in ENDPOINTS.values() for c in rule['train_cycles']})}
    development = pd.concat([f for f,_ in cohorts.values()],ignore_index=True)
    case = pd.read_csv(ROOT/'data/case/deficiency_anemia.csv')
    tests,audit = {},{}
    for cycle,mapping in p['test_mapping'].items():
        f,meta,info = test_cohort(cycle,mapping)
        f,meta,extra = audit_test(f,meta,development,case,output,cycle)
        tests[cycle] = (f,meta)
        audit[cycle] = {**info,**extra,'rows_after_audit':len(f)}
        f.to_csv(output/f'{cycle}_test_features.csv',index=False)
        meta.to_csv(output/f'{cycle}_test_metadata.csv',index=False)
    rows,subgroups,predictions,gates = [],[],[],{}
    for endpoint,rule in ENDPOINTS.items():
        f,meta,y = endpoint_data(*tests[rule['test_cycle']],endpoint,'cbc')
        x = predictor_frame(encode(f),endpoint,'extended')
        views = {'available':x,'drop30':drop_observed(x,p['seed']+303),
                 'cbc_only':predictor_frame(encode(f),endpoint,'cbc')}
        details = {}
        for view,inputs in views.items():
            keep = inputs[[name for name in inputs if name in LABS]].notna().sum(axis=1).ge(5)
            if view != 'cbc_only':
                keep &= x[[name for name in x if name not in CBC]].notna().any(axis=1)
            X,Y,M,F = frame37(inputs.loc[keep]),y[keep],meta.loc[keep],f.loc[keep]
            if not len(Y):
                raise ValueError('No eligible test patients')
            weights,cluster = bootstrap_weights(M,p['bootstrap_replicates'],p['seed'])
            score,threshold,legacy_threshold,route = external_scores(bundle,X,endpoint)
            old = legacy_scores(baseline,X,rule['legacy_target'])
            alternatives = {'v4':(score,threshold),'v1_default':(old,np.full(len(Y),.5)),
                            'v1_tuned':(old,legacy_threshold)}
            info = {}
            for name,(values,cutoffs) in alternatives.items():
                # measure supports per-patient thresholds for per-route policy replay.
                stats = measure(Y,values,cluster,weights,cutoffs)
                fields = {'endpoint':endpoint,'view':view,'model':name,'cycle':rule['test_cycle'],
                          'excluded_sparse':int((~keep).sum()),'cbc_routed_count':int((route == 'cbc').sum())}
                rows.append({**fields,**stats})
                info[name] = stats
                predictions.append(pd.DataFrame({'patient_id':M.patient_id.to_numpy(),'endpoint':endpoint,'view':view,
                    'model':name,'reference':Y,'score':values,'threshold':cutoffs,'route':route}))
                masks = {'female':F.sex.eq('F'),'male':F.sex.eq('M'),'age18_49':F.age_years.between(18,49),
                    'age50_79':F.age_years.between(50,79),'age80plus':F.age_years.ge(80),
                    'Hb_low':F.hemoglobin.lt(np.where(F.sex.eq('F'),120,130)),
                    'Hb_not_low':F.hemoglobin.ge(np.where(F.sex.eq('F'),120,130))}
                for group,mask in masks.items():
                    mask = mask.to_numpy()
                    if mask.any():
                        subgroups.append({**fields,'subgroup':group,**measure(Y[mask],values[mask],cluster[mask],weights,cutoffs[mask])})
            gain = paired_interval(Y,score,old,cluster,weights,threshold,legacy_threshold)
            details[view] = {'v4':{k:float(info['v4'][k]) for k in ['F1','sensitivity','specificity','AP','AUROC']},
                             'v1_tuned':{k:float(info['v1_tuned'][k]) for k in ['F1','sensitivity','specificity','AP','AUROC']},
                             'positives':int(Y.sum()),'paired_sensitivity_gain_95ci':gain}
            print('TEST',endpoint,view,'n',len(Y),'positive',int(Y.sum()),'v4F1',round(info['v4']['F1'],4),flush=True)
        for route,view in [('extended','available'),('cbc','cbc_only')]:
            d = details[view]
            policy = p['external_gate']
            checks = {'positive_count':d['positives'] >= policy['minimum_positives'],
                      'specificity':d['v4']['specificity'] >= policy['minimum_specificity'],
                      'F1_gain_vs_tuned':d['v4']['F1']-d['v1_tuned']['F1'] >= policy['minimum_F1_gain_vs_v1_tuned'],
                      'paired_sensitivity_gain':d['paired_sensitivity_gain_95ci'][0] > policy['paired_sensitivity_gain_ci_low_above']}
            if route == 'extended':
                checks['stress_non_regression'] = details['drop30']['v4']['F1']-details['drop30']['v1_tuned']['F1'] >= -policy['stress_F1_drop_vs_v1_tuned_at_most']
            gates[f'{endpoint}/{route}'] = {'passed':all(checks.values()),'checks':checks,'test':d}
    pd.DataFrame(rows).to_csv(output/'external_metrics.csv',index=False)
    pd.DataFrame(subgroups).to_csv(output/'external_subgroups.csv',index=False)
    pd.concat(predictions,ignore_index=True).to_csv(output/'external_predictions.csv',index=False)
    write_json(output/'test_cohort_manifest.json',audit)
    write_json(output/'external_gates.json',gates)
    # Weights remain the exact pre-test artifact; evidence is a separate JSON sidecar.
    decision = {'default_clinical_model':'baseline_v1','clinical_model_replaced':False,'clinicalValidated':False,
        'accepted_biochemical_heads':[k for k,v in gates.items() if v['passed']],
        'rejected_biochemical_heads':[k for k,v in gates.items() if not v['passed']],
        'reason':p['release_policy'],'weights_sha256':lock['model_sha256'],
        'selection_sha256':lock['selection_sha256'],'test_opened_at_utc':datetime.now(timezone.utc).isoformat()}
    write_json(output/'release_decision.json',decision)
    write_json(output/'artifact_hashes.json',{str(path.relative_to(output)):sha(path) for path in output.rglob('*') if path.is_file() and path.name != 'artifact_hashes.json'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['freeze','train','evaluate'])
    parser.add_argument('--output',type=Path,default=OUTPUT)
    args = parser.parse_args()
    {'freeze':freeze,'train':train,'evaluate':evaluate}[args.stage](args.output)


if __name__ == '__main__':
    main()
