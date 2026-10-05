"""Replay the frozen ferritin external test through the backend adapter; no fitting."""
import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import numpy as np
    import pandas as pd
    from backend.ferritin_service import FerritinService
    from ml_baselines.biochemical import predictor_frame, drop_observed
    from ml_baselines.core import LABS, CBC, encode
    from ml_baselines.train_biochemical import endpoint_data, statistics
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'experiments/backend_hybrid_v1_v4')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    experiment = ROOT/'experiments/biochemical_v4'
    lock = json.loads((experiment/'selection_lock.json').read_text())
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    paths = [ROOT/f'experiments/{family}/models/selected.joblib' for family in
             ('baseline_v1','robust_v2','calibrated_v3','biochemical_v4')]
    before = {str(p.relative_to(ROOT)): digest(p) for p in paths}
    assert before['experiments/biochemical_v4/models/selected.joblib'] == lock['model_sha256']
    assert digest(experiment/'selection.json') == lock['selection_sha256']
    service = FerritinService()
    assert service.status()['state'] == 'ready', service.status()
    f = pd.read_csv(experiment/'nhanes_2007_2008_test_features.csv')
    meta = pd.read_csv(experiment/'nhanes_2007_2008_test_metadata.csv')
    f, meta, y = endpoint_data(f, meta, 'low_ferritin', 'cbc')
    x = predictor_frame(encode(f), 'low_ferritin', 'extended')
    views = {'available': x, 'drop30': drop_observed(x, 20261005+303),
             'cbc_only': predictor_frame(encode(f), 'low_ferritin', 'cbc')}
    saved = pd.read_csv(experiment/'external_predictions.csv')
    metrics = pd.read_csv(experiment/'external_metrics.csv')
    checks = []
    for view, frame in views.items():
        keep = frame[[name for name in frame if name in LABS]].notna().sum(axis=1).ge(5)
        if view != 'cbc_only':
            keep &= x[[name for name in x if name not in CBC]].notna().any(axis=1)
        scores, thresholds, routes = [], [], []
        for idx, row in frame.loc[keep].iterrows():
            values = {key: None if pd.isna(value) else 'F' if key == 'sex' else float(value) for key, value in row.items()}
            pregnancy = 'not_pregnant' if meta.at[idx, 'pregnancy_code'] == 2 else 'unknown'
            result = service.evaluate(values, pregnancy)
            assert result['status'] == 'research_prediction', result
            scores.append(result['score']); thresholds.append(result['decisionThreshold']); routes.append(result['route'])
        reference = saved.loc[saved.endpoint.eq('low_ferritin') & saved.view.eq(view) & saved.model.eq('v4')].set_index('patient_id').loc[f.loc[keep,'patient_id']]
        error = float(np.max(np.abs(np.array(scores)-reference.score.to_numpy())))
        assert error < 1e-12, (view, error)
        assert list(routes) == reference.route.tolist()
        np.testing.assert_allclose(thresholds, reference.threshold, atol=1e-15, rtol=0)
        actual = statistics(y[keep], np.array(scores), np.array(thresholds))
        expected = metrics.loc[metrics.endpoint.eq('low_ferritin') & metrics.view.eq(view) & metrics.model.eq('v4')].iloc[0]
        for name in ('n','positives','tp','fp','tn','fn','F1','sensitivity','specificity','precision','AUROC','AP'):
            assert abs(actual[name]-expected[name]) < 1e-12, (view, name)
        checks.append({'view':view,'n':len(scores),'max_abs_score_difference':error,
            'thresholds_and_routes_match':True, **{key:actual[key] for key in ('F1','sensitivity','specificity','precision','tp','fp','tn','fn')}})
        print(f'{view}: {len(scores)} backend predictions match frozen test; F1={actual["F1"]:.6f}', flush=True)
    from backend.app import create_app
    from fastapi.testclient import TestClient
    from backend.privacy import POLICY_VERSION
    synthetic = json.loads((ROOT/'examples/biochemical_v4_input.json').read_text())
    reports = {}
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(data_dir=Path(directory), ocr_mode='off')
        with TestClient(app, base_url='http://localhost', headers={'X-Hema-Client':'1'}) as client:
            app.state.auth.create_user('hybrid.qa', 'synthetic-hybrid-qa-password-2026', 'Вымышленный врач')
            assert client.post('/api/auth/login', json={'username':'hybrid.qa','password':'synthetic-hybrid-qa-password-2026'}).status_code == 200
            assert client.post('/api/privacy/acknowledge', json={'policyVersion':POLICY_VERSION,'dataKind':'synthetic','researchOnly':True}).status_code == 200
            for name, audience, inputs, pregnancy in [('predicted-patient','patient',synthetic,'not_pregnant'),
                ('predicted-doctor','doctor',synthetic,'not_pregnant'), ('observed-doctor','doctor',{**synthetic,'ferritin':9.},'unknown'),
                ('unknown-patient','patient',synthetic,'unknown')]:
                response = client.post(f'/api/{audience}/predict', json={'inputs':inputs,'pregnancyStatus':pregnancy})
                assert response.status_code == 200, response.text
                report = response.json()
                phrases = report['recommendationSnapshot']
                report.update(recommendations=phrases['items'],recommendationVersion=phrases['phraseFileVersion'],
                    recommendationConclusions=phrases.get('conclusions', []), insufficientData=phrases.get('insufficientData'))
                reports[name] = report
    qa = ROOT/'tmp/pdfs/hybrid'
    qa.mkdir(parents=True, exist_ok=True)
    (qa/'synthetic_reports.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    assert before == {str(p.relative_to(ROOT)):digest(p) for p in paths}
    evidence = {'verified_at_utc':datetime.now(timezone.utc).isoformat(),'success':True,'weights_unchanged':before,
        'external_replay':checks,'total_replayed_predictions':sum(c['n'] for c in checks),'no_training_or_threshold_selection':True,
        'synthetic_api_states':{name:r['ferritinScreening']['status'] for name,r in reports.items()},
        'clinicalValidated':False,'limitations':['replay of the already frozen test, not a new independent cohort',
            'quality describes low ferritin in the evaluated female age group, not the 12 clinical classes']}
    (args.output/'verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
