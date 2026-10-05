"""Offline smoke verification of an extracted research handoff.

Run inside the extracted project with installed server/model/test dependencies:
    python scripts/verify_handoff.py --output /path/to/archive_verification.json
Uses generated test accounts and approved synthetic fixtures in temporary storage.
"""
import argparse
import csv
import hashlib
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    import sys
    sys.path.insert(0,str(ROOT))
    import backend.app as implementation
    from backend.model_service import ModelService
    from backend.privacy import POLICY_VERSION
    from fastapi.testclient import TestClient
    assert Path(implementation.__file__).resolve().is_relative_to(ROOT)
    manifest=json.loads((ROOT/'FILES_MANIFEST.json').read_text('utf-8'))
    for item in manifest['files']:
        assert hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()==item['sha256'],item['path']
    assert not (ROOT/'data/case/deficiency_anemia.csv').exists()
    assert not any(ROOT.rglob('auth.sqlite3'))
    families={}
    for family in ('baseline_v1','robust_v2','calibrated_v3'):
        state=ModelService(ROOT/f'experiments/{family}/models/selected.joblib').status()
        assert state['state']=='ready',state
        families[family]=state['modelVersion']
    from ml_baselines.biochemical_predict import load_candidate
    from ml_baselines.biochemical import predict_biochemical
    candidate=load_candidate(ROOT/'experiments/biochemical_v4/models/selected.joblib')
    candidate_inputs=json.loads((ROOT/'examples/biochemical_v4_input.json').read_text('utf-8'))
    candidate_result=predict_biochemical(candidate,candidate_inputs,'not_pregnant')
    assert candidate_result['clinicalValidated'] is False
    assert candidate_result['model_family']=='biochemical_v4'
    with (ROOT/'examples/synthetic_panel.csv').open() as stream:
        row=next(csv.DictReader(stream))
    inputs={k:(v if k=='sex' else int(v) if k=='age_years' else float(v)) for k,v in row.items() if v and k not in {'patient_id'}}
    with tempfile.TemporaryDirectory(prefix='hema-handoff-check-') as directory:
        app=implementation.create_app(data_dir=Path(directory),ocr_mode='off')
        with TestClient(app,base_url='http://localhost',headers={'X-Hema-Client':'1'}) as client:
            assert client.get('/api/health').json()['status']=='ok'
            assert client.get('/api/models/status').json()['modelConfiguration']=='baseline_v1_plus_ferritin_v4'
            assert client.post('/api/patient/predict',json={'inputs':inputs}).status_code==428
            app.state.auth.create_user('synthetic.doctor','handoff-only-test-password-2026','Вымышленный врач')
            assert client.post('/api/auth/login',json={'username':'synthetic.doctor','password':'handoff-only-test-password-2026'}).status_code==200
            acknowledgement={'policyVersion':POLICY_VERSION,'dataKind':'synthetic','researchOnly':True}
            assert client.post('/api/privacy/acknowledge',json=acknowledgement).status_code==200
            for context, state in [('unknown','unknown_pregnancy'),('not_pregnant','research_prediction')]:
                response=client.post('/api/patient/predict',json={'inputs':candidate_inputs,'pregnancyStatus':context})
                assert response.status_code==200,response.text
                assert response.json()['ferritinScreening']['status']==state
            reports=[]
            for role in ('patient','doctor'):
                response=client.post(f'/api/{role}/predict',json={'inputs':inputs})
                assert response.status_code==200,response.text
                result=response.json();reports.append(result['reportId'])
                assert result['modelConnected'] and not result['clinicalUseEnabled']
                assert client.get('/api/reports/'+result['reportId']).status_code==200
                assert client.post('/api/recommendations',json={'reportId':result['reportId'],'audience':role}).status_code==200
            other=TestClient(app,base_url='http://localhost',headers={'X-Hema-Client':'1'})
            assert other.post('/api/privacy/acknowledge',json=acknowledgement).status_code==200
            assert other.get('/api/reports/'+reports[0]).status_code==404
            original=(ROOT/'output/pdf/test_uploads/01_cbc.pdf').read_bytes()
            response=client.post('/api/observations/pdf',files={'files':('synthetic.pdf',original,'application/pdf')})
            assert response.status_code==200,response.text
            observation=response.json();oid=observation['observationId']
            assert observation['observation']['privacy']['rawTextRetained'] is False
            document=observation['observation']['documents'][0]['id']
            assert client.get(f'/api/observations/{oid}/documents/{document}').content==original
            assert other.get(f'/api/observations/{oid}/documents/{document}').status_code==404
            assert client.delete('/api/observations/'+oid).status_code==200
            assert client.get('/api/observations/'+oid).status_code==404
            app.state.auth.create_organization('synthetic-lab','Вымышленная лаборатория','test@example.invalid')
            kid,token=app.state.auth.create_api_key('synthetic-lab','Handoff test')
            before=app.state.store.db.execute('SELECT count(*) FROM objects').fetchone()[0]
            response=client.post('/api/integration/predict',json={**acknowledgement,'inputs':inputs},headers={'Authorization':'Bearer '+token})
            assert response.status_code==200,response.text
            assert response.json()['stored'] is False and response.json()['organizationId']=='synthetic-lab'
            assert before==app.state.store.db.execute('SELECT count(*) FROM objects').fetchone()[0]
            app.state.auth.revoke_api_key(kid)
            assert client.post('/api/integration/predict',json={**acknowledgement,'inputs':inputs},headers={'Authorization':'Bearer '+token}).status_code==401
            assert client.delete('/api/privacy/session').status_code==200
            assert all(client.get('/api/reports/'+rid).status_code==404 for rid in reports)
            assert not (Path(directory)/'hema.sqlite3').exists()
            other.close()
    evidence={'success':True,'sourceRoot':str(ROOT),'manifestFilesVerified':len(manifest['files']),
              'familiesReady':families,'biochemicalV4SyntheticInference':True,'hybridBackendFerritinInference':True,
              'checks':['research gate','real inference for both audiences','separate trusted biochemical v4 inference','hybrid backend ferritin and explicit pregnancy scope',
                'foreign session denied','synthetic PDF through actual worker','original PDF volatile/private',
                'delete observation','stateless organization API','API key revocation','clear session',
                'no persistent medical database','no runtime/clinical source CSV in handoff'],
              'limitations':['uses existing dependencies','macOS execution only','not clinical/legal certification']}
    args.output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'success':True,'familiesReady':list(families),'manifestFilesVerified':len(manifest['files'])}))


if __name__=='__main__':main()
