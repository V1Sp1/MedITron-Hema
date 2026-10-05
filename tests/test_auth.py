import io
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.auth import AuthStore, COOKIE, SESSION_SECONDS, verify_password
from auth_helpers import PASSWORD, authenticate, acknowledge
from test_backend import VALUES, pdf


class DoctorAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(data_dir=self.root, model_bundle=None, ocr_mode='off')
        self.client = TestClient(self.app, base_url='http://localhost')
        self.client.__enter__()
        self.client.headers['X-Hema-Client'] = '1'

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def login(self):
        return authenticate(self.app, self.client)

    def test_patient_open_doctor_endpoints_require_server_authentication(self):
        self.assertEqual(self.client.post('/api/patient/predict', json={'inputs': VALUES}).status_code, 428)
        acknowledge(self.client)
        self.assertEqual(self.client.post('/api/patient/predict', json={'inputs': VALUES}).status_code, 200)
        self.assertEqual(self.client.get('/api/auth/session').json(), {'authenticated': False, 'user': None})
        self.assertEqual(self.client.post('/api/doctor/predict', json={'inputs': VALUES}).status_code, 401)
        self.assertEqual(self.client.get('/api/recommendations/catalog?audience=doctor').status_code, 401)
        for endpoint in ['/api/data-sufficiency', '/api/recommendations/preview']:
            self.assertEqual(self.client.post(endpoint, json={'audience':'doctor','inputs':VALUES}).status_code, 401)

    def test_passwords_and_session_tokens_are_not_stored_in_plaintext(self):
        user = self.login()
        token = self.client.cookies.get(COOKIE)
        row = self.app.state.auth.db.execute('SELECT * FROM users').fetchone()
        self.assertNotIn(PASSWORD, row['password_hash'])
        self.assertTrue(verify_password(PASSWORD, row['password_hash']))
        self.assertEqual(row['id'], user['id'])
        stored = self.app.state.auth.db.execute('SELECT token_hash FROM sessions').fetchone()[0]
        self.assertNotEqual(token, stored)
        self.assertEqual(self.client.get('/api/auth/session').json()['user']['id'], user['id'])
        self.assertNotIn('password', self.client.get('/api/auth/session').text)
        if __import__('os').name != 'nt':
            self.assertEqual((self.root/'auth.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_wrong_unknown_and_disabled_accounts_return_same_message(self):
        self.login()
        bodies = [{'username':'test.doctor','password':'wrong'}, {'username':'unknown','password':'wrong'}]
        self.app.state.auth.change_user('test.doctor', active=False)
        bodies.append({'username':'test.doctor','password':PASSWORD})
        results = [self.client.post('/api/auth/login', json=body) for body in bodies]
        self.assertEqual([r.status_code for r in results], [401]*3)
        self.assertEqual(len({r.text for r in results}), 1)

    def test_failed_attempts_persist_and_trigger_rate_limit(self):
        body = {'username':'missing.doctor','password':'wrong'}
        for _ in range(5):
            self.assertEqual(self.client.post('/api/auth/login', json=body).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json=body).status_code, 429)
        with TestClient(create_app(data_dir=self.root, model_bundle=None), base_url='http://localhost',
                        headers={'X-Hema-Client':'1'}) as restarted:
            self.assertEqual(restarted.post('/api/auth/login', json=body).status_code, 429)

    def test_login_rotation_logout_and_cookie_properties(self):
        self.login(); first = self.client.cookies.get(COOKIE)
        response = self.client.post('/api/auth/login', json={'username':'test.doctor','password':PASSWORD})
        cookie = response.headers['set-cookie'].lower()
        self.assertIn('httponly', cookie); self.assertIn('samesite=strict', cookie)
        self.assertIn(f'max-age={SESSION_SECONDS}', cookie)
        self.assertNotEqual(first, self.client.cookies.get(COOKIE))
        self.assertIsNone(self.app.state.auth.current(first))
        token = self.client.cookies.get(COOKIE)
        self.assertEqual(self.client.post('/api/auth/logout', json={}).status_code, 200)
        self.assertIsNone(self.app.state.auth.current(token))
        self.assertEqual(self.client.post('/api/doctor/predict', json={'inputs':VALUES}).status_code, 401)

    def test_expiry_reset_and_disable_revoke_sessions(self):
        self.login()
        with patch('backend.auth.time.time', return_value=time.time()+SESSION_SECONDS+1):
            self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.app.state.auth.change_user('test.doctor', password=PASSWORD+'new')
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        response = self.client.post('/api/auth/login', json={'username':'test.doctor','password':PASSWORD+'new'})
        self.assertEqual(response.status_code, 200)
        self.app.state.auth.change_user('test.doctor', active=False)
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_report_and_recommendations_cannot_be_read_after_logout_or_by_another_doctor(self):
        owner = self.login()
        report = self.client.post('/api/doctor/predict', json={'inputs':VALUES}).json()
        self.assertEqual(report['doctorId'], owner['id'])
        url = '/api/reports/'+report['reportId']
        self.client.post('/api/auth/logout',json={})
        self.assertEqual(self.client.get(url).status_code,404)
        for audience in ['doctor','patient']:
            self.assertEqual(self.client.post('/api/recommendations',json={'reportId':report['reportId'],'audience':audience}).status_code,401 if audience=='doctor' else 404)
        authenticate(self.app,self.client,'another.doctor')
        self.assertEqual(self.client.get(url).status_code,404)

    def test_private_pdf_observation_review_and_original_are_protected(self):
        content = pdf(['Hemoglobin 108 g/L 120-150'])
        acknowledge(self.client)
        self.assertEqual(self.client.post('/api/observations/pdf',data={'audience':'doctor'},files={'files':('lab.pdf',content,'application/pdf')}).status_code,401)
        self.login()
        response=self.client.post('/api/observations/pdf',data={'audience':'doctor'},files={'files':('lab.pdf',content,'application/pdf')})
        self.assertEqual(response.status_code,200,response.text)
        observation=response.json();url='/api/observations/'+observation['observationId']
        stored=self.app.state.store.get('observation',observation['observationId'])
        document=stored['documents'][0]['id']
        self.client.post('/api/auth/logout',json={})
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.get(url+'/documents/'+document).status_code,404)
        self.assertEqual(self.client.put(url+'/review',json={'revision':observation['revision'],'inputs':VALUES}).status_code,404)
        self.assertEqual(self.client.post('/api/patient/predict',json={'inputs':VALUES,'observationId':observation['observationId'],'observationRevision':observation['revision'],'reviewedObservation':True}).status_code,428)

    def test_csrf_and_remote_origins_are_rejected(self):
        self.login()
        response=self.client.post('/api/auth/logout',json={},headers={'X-Hema-Client':'','Origin':'http://localhost'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.client.post('/api/auth/logout',json={},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertTrue(self.client.get('/api/auth/session').json()['authenticated'])
        preflight=self.client.options('/api/auth/login',headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'X-Hema-Client,Content-Type'})
        self.assertEqual(preflight.status_code,200)
        self.assertEqual(preflight.headers['access-control-allow-credentials'],'true')

    def test_accounts_and_sessions_survive_restart_and_database_is_not_public(self):
        self.login()
        with TestClient(create_app(data_dir=self.root,model_bundle=None),base_url='http://localhost') as restarted:
            restarted.cookies.update(self.client.cookies)
            self.assertTrue(restarted.get('/api/auth/session').json()['authenticated'])
        self.assertEqual(self.client.get('/data/local/auth.sqlite3').status_code,404)
        self.assertEqual(self.client.get('/api/auth/users').status_code,404)
        with self.assertRaises(ValueError): self.app.state.auth.create_user('test.doctor',PASSWORD)
        with self.assertRaises(ValueError): self.app.state.auth.create_user('valid.user','short')

    def test_invalid_login_does_not_echo_submitted_secret(self):
        secret = 'secret-' * 25
        response = self.client.post('/api/auth/login', json={'username':'test.doctor','password':secret})
        self.assertEqual(response.status_code,422)
        self.assertNotIn(secret,response.text)
