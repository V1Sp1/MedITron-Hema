"""B2B integration, files, ownership and abuse checks on synthetic data only."""
import io
import json
import sqlite3
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from lab_parser import parse_batch
from backend.app import create_app
from backend.auth import AuthStore
from backend.privacy import POLICY_VERSION
from test_backend import pdf, VALUES

BASE = {'policyVersion':POLICY_VERSION,'dataKind':'synthetic','researchOnly':True}


class LaboratoryAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(data_dir=self.root, model_bundle=None, ocr_mode='off')
        self.client = TestClient(self.app, base_url='http://localhost', client=('127.0.0.1',50000))
        self.client.__enter__()
        for org in ('lab-one','lab-two'):
            self.app.state.auth.create_organization(org,org,'research@example.invalid')
        self.kid,self.token = self.app.state.auth.create_api_key('lab-one','Full synthetic test',scopes=['predict','reports','pdf'])
        self.headers = {'Authorization':'Bearer '+self.token}

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()

    def report(self, **options):
        return self.client.post('/api/integration/v1/reports',json={**BASE,'inputs':VALUES,**options},headers=self.headers)

    def upload(self, *, metadata=None, content=None, filename='synthetic.pdf', headers=None):
        return self.client.post('/api/integration/v1/observations/pdf',
            data={'metadata':json.dumps(metadata or {**BASE,'age_years':42,'sex':'F'})},
            files=[('files',(filename,content or pdf(['Hb 108 g/L','MCV 78 fL','MCH 25 pg','Ferritin 9 ng/mL']),'application/pdf'))],
            headers=headers or self.headers)

    def reviewed(self, draft, **options):
        return self.client.post('/api/integration/v1/observations/'+draft['observationId']+'/reports',
            json={**BASE,'inputs':draft['inputs'],'revision':draft['revision'],'reviewedObservation':True,**options},
            headers=self.headers)

    def test_json_report_is_attachment_stateless_and_contains_both_roles(self):
        response = self.report(format='json')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('attachment;',response.headers['Content-Disposition'])
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(response.headers['X-Hema-Clinical-Use-Enabled'],'false')
        result = response.json()
        self.assertEqual(response.headers['X-Hema-Request-Id'],result['requestId'])
        self.assertEqual([r['audience'] for r in result['reports']],['patient','doctor'])
        for report in result['reports']:
            self.assertFalse(report['stored']);self.assertFalse(report['clinicalUseEnabled'])
            self.assertEqual(report['organizationId'],'lab-one')
            self.assertIsNone(report['reportId'])
            self.assertTrue(report['insufficientData']['active'])
            self.assertTrue(report['recommendations'])
            self.assertNotIn(self.token,json.dumps(report))
            self.assertNotIn('_access',report)
        self.assertEqual(self.app.state.store.db.execute('SELECT count(*) FROM objects').fetchone()[0],0)

    def test_real_pdf_and_zip_have_fixed_safe_names_and_embedded_cyrillic(self):
        response = self.report(format='pdf',audience='doctor')
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertEqual(response.headers['Content-Type'],'application/pdf')
        import pypdfium2
        doc=pypdfium2.PdfDocument(response.content)
        text='\n'.join(page.get_textpage().get_text_range() for page in doc)
        self.assertIn('Гемоглобин',text);self.assertIn('Полнота данных',text)
        self.assertIn('Следующие шаги',text);self.assertIn('Исследовательский',text)
        response=self.report()
        self.assertEqual(response.status_code,200,response.text)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(set(archive.namelist()),{'reports.json','report-patient.pdf','report-doctor.pdf'})
            self.assertIsNone(archive.testzip())
            for role in ('patient','doctor'):self.assertTrue(archive.read('report-'+role+'.pdf').startswith(b'%PDF-'))

    def test_pdf_requires_review_then_generates_files_and_original_is_not_retained(self):
        response=self.upload(filename='PrivateOriginalName.pdf')
        self.assertEqual(response.status_code,200,response.text)
        draft=response.json()
        self.assertTrue(draft['requiresReview']);self.assertFalse(draft['originalFilesRetained'])
        self.assertEqual(draft['inputs']['ferritin'],9)
        self.assertEqual(draft['inputs']['age_years'],42)
        self.assertNotIn('PrivateOriginalName',response.text)
        self.assertNotIn('_integration',response.text)
        oid=draft['observationId'];path='/api/integration/v1/observations/'+oid
        self.assertEqual(self.app.state.store.db.execute('SELECT count(*) FROM documents').fetchone()[0],0)
        self.assertEqual(list(Path(self.app.state.staging.name).glob('*/*')),[])
        bad=self.reviewed(draft,reviewedObservation=False,format='json')
        self.assertEqual(bad.status_code,422)
        self.assertEqual(self.reviewed(draft,revision=2).status_code,409)
        self.assertEqual(self.reviewed(draft,dataKind='anonymized').status_code,409)
        reviewed=self.reviewed(draft,format='json')
        self.assertEqual(reviewed.status_code,200,reviewed.text)
        self.assertEqual(reviewed.json()['reports'][0]['inputSource'],'reviewed_pdf')
        self.assertEqual(reviewed.json()['reports'][0]['observationId'],oid)
        self.assertEqual(self.client.get(path+'/documents/doc-1',headers=self.headers).status_code,404)
        self.assertEqual(self.client.delete(path,headers=self.headers).status_code,200)
        self.assertEqual(self.client.get(path,headers=self.headers).status_code,404)
        self.assertEqual(self.reviewed(draft).status_code,404)

    def test_same_org_another_key_and_foreign_org_cannot_access_known_draft(self):
        draft=self.upload().json();path='/api/integration/v1/observations/'+draft['observationId']
        for org in ('lab-one','lab-two'):
            _,token=self.app.state.auth.create_api_key(org,'Other key',scopes=['pdf','reports'])
            headers={'Authorization':'Bearer '+token}
            self.assertEqual(self.client.get(path,headers=headers).status_code,404)
            self.assertEqual(self.client.delete(path,headers=headers).status_code,404)
            response=self.client.post(path+'/reports',json={**BASE,'inputs':VALUES,'revision':1,'reviewedObservation':True},headers=headers)
            self.assertEqual(response.status_code,404)
        self.assertEqual(self.client.get('/api/observations/'+draft['observationId']).status_code,404)

    def test_legacy_keys_keep_predict_only_and_cannot_gain_new_permissions(self):
        _,token=self.app.state.auth.create_api_key('lab-one','Legacy default')
        headers={'Authorization':'Bearer '+token}
        self.assertEqual(self.client.post('/api/integration/predict',json={**BASE,'inputs':VALUES},headers=headers).status_code,200)
        self.assertEqual(self.client.post('/api/integration/v1/reports',json={**BASE,'inputs':VALUES},headers=headers).status_code,403)
        self.assertEqual(self.upload(headers=headers).status_code,403)
        _,token=self.app.state.auth.create_api_key('lab-one','Reports only',scopes=['reports'])
        self.assertEqual(self.client.post('/api/integration/predict',json={**BASE,'inputs':VALUES},headers={'Authorization':'Bearer '+token}).status_code,403)
        for scopes in ([],['admin'],['reports','unknown']):
            with self.assertRaises(ValueError):self.app.state.auth.create_api_key('lab-one','bad',scopes=scopes)

    def test_revoke_or_expire_blocks_all_operations_and_draft_ttl_is_fixed(self):
        draft=self.upload().json();path='/api/integration/v1/observations/'+draft['observationId']
        initial=draft['expiresAt']
        self.assertEqual(self.client.get(path,headers=self.headers).json()['expiresAt'],initial)
        self.app.state.store.clock=lambda:initial+1
        self.assertEqual(self.client.get(path,headers=self.headers).status_code,404)
        self.app.state.auth.revoke_api_key(self.kid)
        self.assertEqual(self.report(format='json').status_code,401)
        self.assertEqual(self.client.get(path,headers=self.headers).status_code,401)
        _,token=self.app.state.auth.create_api_key('lab-one','Expired',scopes=['reports'],lifetime_days=1)
        with patch('backend.auth.time.time',return_value=time.time()+86401):
            self.assertEqual(self.client.post('/api/integration/v1/reports',json={**BASE,'inputs':VALUES},headers={'Authorization':'Bearer '+token}).status_code,401)

    def test_draft_is_lost_on_restart_and_is_not_written_to_disk(self):
        draft=self.upload().json();oid=draft['observationId']
        disk=list(self.root.glob('**/*'))
        self.assertFalse(any(p.suffix.lower() in {'.pdf','.json'} and p.is_file() and p.name!='.hema-ephemeral-owner.json' for p in disk))
        with TestClient(create_app(data_dir=self.root,model_bundle=None),base_url='http://localhost',client=('127.0.0.1',50001)) as client:
            self.assertEqual(client.get('/api/integration/v1/observations/'+oid,headers=self.headers).status_code,404)

    def test_data_rules_size_scope_and_no_reflection(self):
        for change in ({'dataKind':'real_patient'}, {'researchOnly':False}, {'policyVersion':'old'},
                       {'patientName':'Synthetic private person'}, {'callbackUrl':'http://127.0.0.1/admin'},
                       {'inputs':{**VALUES,'patient_id':'private-id'}}, {'format':'pdf','audience':'both'}):
            response=self.report(**change)
            self.assertEqual(response.status_code,422,response.text)
            self.assertNotIn('Synthetic private',response.text)
            self.assertNotIn('private-id',response.text)
        self.assertEqual(self.client.post('/api/integration/v1/reports',content=b'x'*(128*1024+1),headers=self.headers).status_code,413)
        self.assertEqual(self.upload(metadata={**BASE,'patient_id':'private-id'}).status_code,422)
        self.assertEqual(self.upload(content=b'not a pdf').status_code,422)
        self.assertEqual(self.upload(filename='file.pdf:stream').status_code,422)
        response=self.upload(metadata={**BASE,'dataKind':'anonymized'},content=pdf(['Patient: Synthetic Person','Hb 108 g/L']))
        self.assertEqual(response.status_code,422);self.assertNotIn('Synthetic Person',response.text)

    def test_unauthorized_never_calls_parser_and_remote_http_is_rejected(self):
        with patch('backend.laboratory_api.run_in_threadpool') as called:
            self.assertEqual(self.upload(headers={'Authorization':'Bearer bad'}).status_code,401)
            called.assert_not_called()
        # Outer client already owns lifespan; do not restart/close the same app.
        remote=TestClient(self.app,base_url='http://localhost',client=('192.0.2.1',50001))
        try:
            self.assertEqual(remote.post('/api/integration/v1/reports',json={**BASE,'inputs':VALUES},headers=self.headers).status_code,426)
        finally:
            remote.close()
        response=self.client.post('/api/integration/v1/reports',json={**BASE,'inputs':VALUES},
                                  headers=[('Authorization','Bearer '+self.token),('Authorization','Bearer '+self.token)])
        self.assertEqual(response.status_code,401)

    def test_rate_limit_and_capabilities_describe_contract(self):
        capabilities=self.client.get('/api/integration/v1/capabilities',headers=self.headers)
        self.assertEqual(capabilities.status_code,200)
        self.assertFalse(capabilities.json()['realPatientProcessingEnabled'])
        self.assertEqual(capabilities.json()['limits']['draftRetentionSeconds'],900)
        for _ in range(59):self.app.state.integration_limiter.allow(self.kid)
        response=self.report(format='json')
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.headers['Retry-After'],'60')
        schema=self.client.get('/openapi.json').json()
        self.assertIn('LaboratoryKey',schema['components']['securitySchemes'])
        self.assertIn('multipart/form-data',schema['paths']['/api/integration/v1/observations/pdf']['post']['requestBody']['content'])

    def test_revocation_during_inference_or_parsing_cannot_return_or_save_results(self):
        def revoke(report):
            self.app.state.auth.revoke_api_key(self.kid)
            return report
        with patch.object(self.app.state,'model') as model:
            model.apply.side_effect=revoke
            self.assertEqual(self.report(format='json').status_code,401)
        _,self.token=self.app.state.auth.create_api_key('lab-one','race',scopes=['pdf','reports'])
        self.headers={'Authorization':'Bearer '+self.token}
        self.kid=self.app.state.auth.api_key(self.token)['id']
        def parser(paths,**kwargs):
            result=parse_batch(paths,**kwargs).to_dict()
            self.app.state.auth.revoke_api_key(self.kid)
            return result
        app=create_app(data_dir=self.root/'race',model_bundle=None,parser=parser,ocr_mode='off')
        with TestClient(app,base_url='http://localhost',client=('127.0.0.1',50002)) as client:
            app.state.auth.create_organization('lab-one','test','research@example.invalid')
            self.kid,token=app.state.auth.create_api_key('lab-one','race',scopes=['pdf','reports'])
            previous=self.app;self.app=app
            try:
                response=client.post('/api/integration/v1/observations/pdf',data={'metadata':json.dumps(BASE)},
                    files={'files':('synthetic.pdf',pdf(['Hb 108 g/L']),'application/pdf')},headers={'Authorization':'Bearer '+token})
                self.assertEqual(response.status_code,401)
                self.assertEqual(app.state.store.db.execute('SELECT count(*) FROM objects').fetchone()[0],0)
            finally:self.app=previous

    def test_audit_does_not_contain_tokens_inputs_or_filenames(self):
        self.report(format='json');self.upload(filename='DoNotLogThis.pdf')
        events=list(self.app.state.privacy.events)
        self.assertTrue(any(e['action']=='integration_report' for e in events))
        serialized=json.dumps(events)
        for forbidden in (self.token,'hemoglobin','DoNotLogThis.pdf'):
            self.assertNotIn(forbidden,serialized)


class ScopeMigrationTests(unittest.TestCase):
    def test_old_db_keys_do_not_automatically_gain_new_scopes(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'auth.sqlite3'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE api_keys (id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL, organization_id TEXT NOT NULL,label TEXT NOT NULL, active INTEGER NOT NULL,expires_at REAL NOT NULL)')
            token='old-key-for-synthetic-test'
            db.execute('INSERT INTO api_keys VALUES (?,?,?,?,?,?)',('old',AuthStore.token_hash(token),'local-research','old',1,time.time()+86400))
            db.commit();db.close()
            store=AuthStore(Path(root))
            self.assertEqual(store.api_key(token)['scopes'],['predict'])
            store.close()
