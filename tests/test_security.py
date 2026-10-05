"""Adversarial regression checks using only generated synthetic records."""
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.model_service import ModelService
from backend.privacy import COOKIE, POLICY_VERSION, ResearchSessions, IntegrationLimiter, cleanup_abandoned
from backend.store import Store, StorageFull
from scripts import bundle_project
from auth_helpers import PASSWORD, acknowledge
from test_backend import VALUES, pdf


class SecurityAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(data_dir=self.root, model_bundle=None, ocr_mode='off')
        self.client = TestClient(self.app, base_url='http://localhost', headers={'X-Hema-Client':'1'})
        self.client.__enter__()
        self.other = TestClient(self.app, base_url='http://localhost', headers={'X-Hema-Client':'1'})

    def tearDown(self):
        self.other.close()
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def upload(self, content=None):
        return self.client.post('/api/observations/pdf', files={'files':('private-name.pdf', content or pdf(['Hb 108 g/L']), 'application/pdf')})

    def report(self, **extra):
        return self.client.post('/api/patient/predict', json={'inputs':VALUES, **extra})

    def test_acknowledgement_is_explicit_and_real_data_not_a_valid_mode(self):
        with patch('backend.app.run_parser') as parser:
            self.assertEqual(self.upload().status_code, 428)
            parser.assert_not_called()
        self.assertEqual(self.report().status_code, 428)
        for change in [{'dataKind':'real_patient'}, {'researchOnly':False}, {'policyVersion':'old'}, {'consent':True}]:
            response=self.client.post('/api/privacy/acknowledge',json={
                'policyVersion':POLICY_VERSION,'dataKind':'synthetic','researchOnly':True,**change})
            self.assertEqual(response.status_code,422)
        status=self.client.get('/api/privacy/status').json()
        self.assertFalse(status['realPatientProcessingEnabled'])
        response=acknowledge(self.client)
        self.assertEqual(response['dataKind'],'synthetic')
        stored=list(self.app.state.privacy.sessions)[0]
        self.assertNotEqual(stored,self.client.cookies.get(COOKIE))

    def test_foreign_session_cannot_read_change_delete_or_derive_known_ids(self):
        acknowledge(self.client);acknowledge(self.other)
        obs=self.upload().json();oid=obs['observationId'];base='/api/observations/'+oid
        rid=self.report(observationId=oid,observationRevision=1,reviewedObservation=True).json()['reportId']
        attempts=[self.other.get(base),self.other.get(base+'/documents/doc-1'),
                  self.other.put(base+'/review',json={'revision':2,'inputs':VALUES}),
                  self.other.delete(base),self.other.get('/api/reports/'+rid),self.other.delete('/api/reports/'+rid),
                  self.other.post('/api/recommendations',json={'reportId':rid,'audience':'patient'}),
                  self.other.post('/api/patient/predict',json={'inputs':VALUES,'observationId':oid,
                    'observationRevision':2,'reviewedObservation':True})]
        self.assertEqual([r.status_code for r in attempts],[404]*len(attempts))
        self.assertEqual(self.client.get('/api/reports/'+rid).status_code,200)

    def test_delete_observation_cascades_pdf_and_linked_report(self):
        acknowledge(self.client);obs=self.upload().json();oid=obs['observationId']
        rid=self.report(observationId=oid,observationRevision=1,reviewedObservation=True).json()['reportId']
        self.assertEqual(self.client.delete('/api/observations/'+oid).status_code,200)
        self.assertIsNone(self.app.state.store.get_document(oid,'doc-1'))
        self.assertEqual(self.client.get('/api/reports/'+rid).status_code,404)

    def test_clear_session_revokes_even_a_copied_old_cookie(self):
        acknowledge(self.client);rid=self.report().json()['reportId']
        old=self.client.cookies.get(COOKIE)
        self.assertEqual(self.client.delete('/api/privacy/session').status_code,200)
        self.other.cookies.set(COOKIE,old,domain='localhost.local',path='/')
        self.assertEqual(self.other.get('/api/reports/'+rid).status_code,404)
        self.assertEqual(self.other.post('/api/patient/predict',json={'inputs':VALUES}).status_code,428)
        self.assertEqual(self.app.state.store.db.execute('SELECT count(*) FROM objects').fetchone()[0],0)

    def test_expiry_does_not_extend_on_reads_and_removes_original_pdf(self):
        acknowledge(self.client);obs=self.upload().json();oid=obs['observationId']
        expired=time.time()+3601
        self.app.state.store.clock=lambda:expired
        self.app.state.privacy.clock=lambda:expired
        self.assertEqual(self.client.get('/api/observations/'+oid).status_code,404)
        self.assertIsNone(self.app.state.store.get_document(oid,'doc-1'))
        self.assertEqual(self.report().status_code,428)

    def test_identity_text_minimized_and_anonymized_identity_rejected(self):
        content=pdf(['Patient: Synthetic Person','DOB: 01.01.1984','Hb 108 g/L'])
        acknowledge(self.client)
        response=self.upload(content);self.assertEqual(response.status_code,200,response.text)
        payload=response.json()['observation']
        encoded=json.dumps(payload)
        for sensitive in ['Synthetic Person','1984','private-name.pdf','patient_names']:
            self.assertNotIn(sensitive,encoded)
        self.assertEqual(payload['documents'][0]['pages'][0]['rows'],[])
        self.assertIsNone(payload['measurements'][0]['source']['text'])
        # The actual PDF remains only in the authorized volatile blob until deletion.
        self.assertEqual(self.app.state.store.get_document(response.json()['observationId'],'doc-1'),content)
        self.assertEqual(list(Path(self.app.state.staging.name).glob('*/*')),[])
        acknowledge(self.client,'anonymized')
        self.assertEqual(self.upload(content).status_code,422)
        self.assertEqual(self.app.state.store.db.execute('SELECT count(*) FROM objects').fetchone()[0],0)

    def test_legacy_records_without_owner_and_on_disk_db_are_never_served(self):
        identifier=str(uuid4());self.app.state.store.put('report',{'id':identifier,'inputs':VALUES})
        acknowledge(self.client)
        self.assertEqual(self.client.get('/api/reports/'+identifier).status_code,404)
        disk=self.root/'hema.sqlite3';disk.write_bytes(b'legacy fixture must remain sealed')
        with TestClient(create_app(data_dir=self.root,model_bundle=None),base_url='http://localhost') as restarted:
            self.assertEqual(restarted.get('/api/reports/'+identifier).status_code,404)
        self.assertEqual(disk.read_bytes(),b'legacy fixture must remain sealed')

    def test_revoke_while_parsing_cannot_resurrect_records(self):
        from lab_parser import parse_batch
        acknowledge(self.client)
        old=self.client.cookies.get(COOKIE)
        def clearing_parser(paths,**kwargs):
            self.app.state.privacy.revoke(old)
            return parse_batch(paths,**kwargs).to_dict()
        app=create_app(data_dir=self.root/'race',model_bundle=None,parser=clearing_parser)
        # Replace the active parser's captured privacy state with the tested app.
        with TestClient(app,base_url='http://localhost') as client:
            acknowledge(client);old=client.cookies.get(COOKIE)
            previous=self.app;self.app=app
            try:
                result=client.post('/api/observations/pdf',files={'files':('test.pdf',pdf(['Hb 108 g/L']))})
                self.assertEqual(result.status_code,428)
                self.assertEqual(app.state.store.db.execute('SELECT count(*) FROM objects').fetchone()[0],0)
            finally:self.app=previous

    def test_organizations_doctors_and_api_keys_are_isolated_revocable_and_stateless(self):
        auth=self.app.state.auth
        for org in ['clinic-a','clinic-b']:
            auth.create_organization(org,org,'research@example.invalid')
            auth.create_user(org+'.doctor',PASSWORD,organization_id=org)
        for client,org in [(self.client,'clinic-a'),(self.other,'clinic-b')]:
            self.assertEqual(client.post('/api/auth/login',json={'username':org+'.doctor','password':PASSWORD}).status_code,200)
            acknowledge(client)
        report=self.client.post('/api/doctor/predict',json={'inputs':VALUES}).json()
        self.assertEqual(self.other.get('/api/reports/'+report['reportId']).status_code,404)
        kid,token=auth.create_api_key('clinic-a','synthetic test',lifetime_days=1)
        stored=auth.db.execute('SELECT token_hash FROM api_keys WHERE id=?',(kid,)).fetchone()[0]
        self.assertNotEqual(stored,token)
        body={'policyVersion':POLICY_VERSION,'dataKind':'synthetic','researchOnly':True,'inputs':VALUES}
        before=self.app.state.store.db.execute('SELECT count(*) FROM objects').fetchone()[0]
        response=self.other.post('/api/integration/predict',json=body,headers={'Authorization':'Bearer '+token})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['organizationId'],'clinic-a')
        self.assertFalse(response.json()['stored']);self.assertIsNone(response.json()['reportId'])
        self.assertFalse(response.json()['clinicalUseEnabled'])
        self.assertEqual(before,self.app.state.store.db.execute('SELECT count(*) FROM objects').fetchone()[0])
        with patch('backend.auth.time.time',return_value=time.time()+86401):
            self.assertIsNone(auth.api_key(token))
        auth.revoke_api_key(kid)
        self.assertEqual(self.other.post('/api/integration/predict',json=body,headers={'Authorization':'Bearer '+token}).status_code,401)

    def test_small_body_limit_csrf_no_cache_and_validation_do_not_echo_data(self):
        self.assertEqual(self.client.post('/api/patient/predict',content=b'x'*(128*1024+1)).status_code,413)
        acknowledge(self.client)
        self.assertEqual(self.client.post('/api/patient/predict',json={'inputs':VALUES},headers={'X-Hema-Client':''}).status_code,403)
        response=self.client.post('/api/patient/predict',json={'inputs':{**VALUES,'SyntheticPrivateName':12}})
        self.assertEqual(response.status_code,422);self.assertNotIn('SyntheticPrivateName',response.text)
        response=self.report()
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        self.assertNotIn('_access',response.json())


class SecurityUnitTests(unittest.TestCase):
    def test_failed_key_cli_removes_only_its_new_file_and_never_issues_active_key(self):
        from backend.integrations import main
        from backend.auth import AuthStore
        import contextlib, io
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'new.key'
            arguments=['integrations','--data-dir',root,'key-create','missing-org','--label','test','--token-file',str(target)]
            with patch('sys.argv',arguments),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:main()
            self.assertEqual(failure.exception.code,2)
            self.assertFalse(target.exists())
            store=AuthStore(Path(root))
            self.assertEqual(store.list_api_keys(),[]);store.close()
            target.write_text('existing private file')
            with patch('sys.argv',arguments),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):main()
            self.assertEqual(target.read_text(),'existing private file')

    def test_storage_limits_expiry_and_sessions_hash_tokens(self):
        with tempfile.TemporaryDirectory() as root:
            now=[100];store=Store(Path(root),clock=lambda:now[0],max_bytes=400)
            value={'id':'a','revision':1,'_access':{'expiresAt':150,'grantId':'g'}}
            store.put('observation',value,{'doc':b'x'*100})
            with self.assertRaises(StorageFull):store.put('report',{'id':'b','data':'x'*1000})
            with self.assertRaises(StorageFull):store.update('observation',{**value,'revision':2,'data':'x'*1000},1)
            self.assertEqual(store.get('observation','a')['revision'],1)
            now[0]=150;self.assertIsNone(store.get_document('a','doc'));store.close()
        sessions=ResearchSessions(clock=lambda:100)
        token,session=sessions.create('synthetic')
        self.assertNotIn(token,sessions.sessions)
        self.assertEqual(sessions.current(token)['id'],session['id'])
        sessions.clock=lambda:3700;self.assertIsNone(sessions.current(token))

    def test_limits_expire_and_unknown_pickle_is_refused_before_deserialization(self):
        now=[0];limit=IntegrationLimiter(clock=lambda:now[0])
        self.assertTrue(all(limit.allow('a') for _ in range(60)))
        self.assertFalse(limit.allow('a'));self.assertTrue(limit.allow('b'))
        now[0]=60;self.assertTrue(limit.allow('a'))
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'untrusted.joblib';path.write_bytes(b'untrusted serialized code')
            with patch('joblib.load') as load:
                self.assertEqual(ModelService(path).error,'untrusted_weights');load.assert_not_called()

    def test_crashed_cleanup_keeps_live_or_unmarked_folders(self):
        with tempfile.TemporaryDirectory() as root:
            for name,pid in [('hema-live-dead',99999999),('hema-live-running',__import__('os').getpid())]:
                folder=Path(root)/name;folder.mkdir()
                (folder/'.hema-ephemeral-owner.json').write_text(json.dumps({'version':'hema-ephemeral-v1','pid':pid}))
            (Path(root)/'hema-live-unmarked').mkdir()
            cleanup_abandoned(root)
            self.assertFalse((Path(root)/'hema-live-dead').exists())
            self.assertTrue((Path(root)/'hema-live-running').exists())
            self.assertTrue((Path(root)/'hema-live-unmarked').exists())

    def test_archive_allowlist_excludes_arbitrary_runtime_sources_and_credentials(self):
        for path in ['data/local-demo/auth.sqlite3','data/other/patients.csv','data/case/deficiency_anemia.csv',
                     'experiments/baseline_v1/splits.csv','experiments/biochemical_v4/split_audit.json',
                     'experiments/biochemical_v4/external_predictions.csv',
                     'experiments/external_validation_v1/features.csv',
                     'data/external/raw/nhanes_2005_2006/DEMO_D.xpt',
                     'frontend/.env','backend/token.key','tmp/source.pdf']:
            self.assertFalse(bundle_project.permitted(Path(path)),path)
        self.assertTrue(bundle_project.permitted(Path('backend/privacy.py')))
        for path in ['data/external/unit_mapping.json','experiments/biochemical_v4/models/selected.joblib',
                     'experiments/biochemical_v4/external_gates.json','experiments/external_validation_v1/REPORT.md',
                     'experiments/baseline_v1/holdout_confusion.png']:
            self.assertTrue(bundle_project.permitted(Path(path)),path)
        with tempfile.TemporaryDirectory() as root:
            p=Path(root);(p/'docs').mkdir();(p/'docs'/'public.md').write_text('public')
            (p/'docs'/'runtime').mkdir();(p/'docs'/'runtime'/'.hema-runtime').touch()
            (p/'docs'/'runtime'/'patient.json').write_text('private')
            with patch.object(bundle_project,'ROOT',p):
                self.assertEqual([x.name for x in bundle_project.files()],['public.md'])
                (p/'docs'/'public.md').write_text('hema_lab_'+'A'*43)
                with self.assertRaises(ValueError):bundle_project.files()
