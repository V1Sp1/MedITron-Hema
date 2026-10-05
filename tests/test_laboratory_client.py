"""End-to-end reference client and reports from the actual trusted model."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.privacy import POLICY_VERSION
from scripts.laboratory_client import LaboratoryClient, LaboratoryError, private_output
from scripts.build_demo_scenarios import SCENARIOS
from test_backend import pdf

ACK={'policyVersion':POLICY_VERSION,'dataKind':'synthetic','researchOnly':True}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.app=create_app(data_dir=self.root/'data',model_bundle=None,ocr_mode='off')
        self.server=TestClient(self.app,base_url='http://localhost',client=('127.0.0.1',50000))
        self.server.__enter__()
        self.app.state.auth.create_organization('lab-demo','Synthetic test','research@example.invalid')
        _,token=self.app.state.auth.create_api_key('lab-demo','test',scopes=['pdf','reports'])
        self.key=self.root/'token.key';private_output(self.key,token.encode())
        self.client=LaboratoryClient('http://localhost',self.key,transport=self.server._transport)

    def tearDown(self):
        self.client.close();self.server.__exit__(None,None,None);self.temp.cleanup()

    def test_client_json_pdf_review_download_and_deletion(self):
        self.assertEqual(self.client.research_metadata(),ACK)
        inputs={'age_years':42,'sex':'F','hemoglobin':108}
        result=self.client.reports({**ACK,'inputs':inputs,'format':'json'}).json()
        self.assertEqual(len(result['reports']),2)
        source=self.root/'source.pdf';source.write_bytes(pdf(['Hb 108 g/L','MCV 78 fL','Ferritin 9 ng/mL']))
        draft=self.client.upload_pdfs([source],{**ACK,'age_years':42,'sex':'F'})
        self.assertEqual(draft['inputs']['ferritin'],9)
        with self.assertRaises(ValueError):self.client.reviewed_reports(draft,ACK,reviewed=False)
        response=self.client.reviewed_reports(draft,{**ACK,'format':'zip'},reviewed=True)
        output=self.root/'report.zip';private_output(output,response.content)
        self.assertTrue(output.read_bytes().startswith(b'PK'))
        self.assertEqual(self.client.draft(draft['observationId'])['revision'],draft['revision'])
        self.client.delete_draft(draft['observationId'])
        with self.assertRaisesRegex(LaboratoryError,'404'):self.client.draft(draft['observationId'])

    def test_client_does_not_send_key_to_redirect_or_allow_remote_http(self):
        calls=[]
        def redirect(request):
            calls.append(request.url)
            return httpx.Response(302,headers={'Location':'https://attacker.example.invalid'},text='private medical response')
        with LaboratoryClient('https://laboratory.example.invalid',self.key,transport=httpx.MockTransport(redirect)) as client:
            with self.assertRaises(LaboratoryError) as error:client.capabilities()
        self.assertEqual(len(calls),1)
        self.assertNotIn('private medical response',str(error.exception))
        for url in ['http://external.example.invalid','http://127.0.0.1.attacker.invalid',
                    'https://user:secret@example.invalid','https://example.invalid?token=bad',
                    'https://example.invalid/api/','https://example.invalid#token']:
            with self.subTest(url=url),self.assertRaises(ValueError):LaboratoryClient(url,self.key)

    def test_client_private_files_and_identifier_validation(self):
        output=self.root/'new.json';private_output(output,b'first')
        with self.assertRaises(FileExistsError):private_output(output,b'second')
        self.assertEqual(output.read_bytes(),b'first')
        if os.name=='posix':
            self.assertEqual(output.stat().st_mode&0o777,0o600)
            self.key.chmod(0o644)
            with self.assertRaises(ValueError):LaboratoryClient('http://localhost',self.key)
        with self.assertRaises(ValueError):self.client.delete_draft('../reports')


class ActualModelReportsTests(unittest.TestCase):
    def test_current_model_measured_ferritin_and_separate_prediction_in_report(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(data_dir=Path(directory),ocr_mode='off')
            with TestClient(app,base_url='http://localhost',client=('127.0.0.1',50000)) as client:
                app.state.auth.create_organization('lab-demo','Synthetic','research@example.invalid')
                _,token=app.state.auth.create_api_key('lab-demo','test',scopes=['reports'])
                headers={'Authorization':'Bearer '+token}
                body={**ACK,'inputs':SCENARIOS[0][3],'format':'json'}
                response=client.post('/api/integration/v1/reports',json=body,headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                patient,doctor=response.json()['reports']
                self.assertTrue(patient['modelConnected'])
                self.assertEqual(patient['prediction'],doctor['prediction'])
                self.assertEqual(patient['ferritinScreening']['observedValue'],6)
                self.assertEqual(patient['ferritinScreening']['status'],'observed')
                self.assertEqual(patient['inputs']['hemoglobin'],105)
                response=client.post('/api/integration/v1/reports',json={**body,'format':'pdf','audience':'doctor'},headers=headers)
                import pypdfium2
                document=pypdfium2.PdfDocument(response.content)
                text='\n'.join(page.get_textpage().get_text_range() for page in document)
                self.assertIn('Ферритин измерен: 6 µg/L',text)
                document.close()
                self.assertNotIn('Ферритин измерен: None',text)
                panel={**SCENARIOS[0][3],'ferritin':None}
                response=client.post('/api/integration/v1/reports',json={**body,'inputs':panel},headers=headers)
                self.assertEqual(response.json()['reports'][0]['ferritinScreening']['status'],'unknown_pregnancy')
                response=client.post('/api/integration/v1/reports',json={**body,'inputs':panel,'pregnancyStatus':'not_pregnant'},headers=headers)
                ferritin=response.json()['reports'][0]['ferritinScreening']
                self.assertEqual(ferritin['status'],'research_prediction')
                self.assertTrue(0<=ferritin['score']<=1)
                self.assertFalse(ferritin['clinicalValidated'])
                self.assertEqual(response.json()['reports'][0]['prediction'],response.json()['reports'][1]['prediction'])


if __name__=='__main__':unittest.main()
