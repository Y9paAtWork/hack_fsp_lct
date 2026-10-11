import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import app
from backend.tests.registration import candidate_payload


class ResumePdfTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.previous=os.environ.get('FSP_DB')
        os.environ['FSP_DB']=str(Path(self.temp.name)/'resume.sqlite3')
        self.client=TestClient(app)
        self.client.__enter__()
        payload=candidate_payload(name='Анна Иванова')
        payload['profile']['selected']=['Backend','DevOps / SRE']
        payload['profile']['stacks']={'Backend':['Docker'],'DevOps / SRE':['Docker']}
        payload['profile']['jobs']={
            'Backend':[{'company':'Альфа','role':'Backend инженер','description':'Разработка API и сервисов.'}],
            'DevOps / SRE':[{'company':'Бета','role':'DevOps инженер','description':'Автоматизация релизов.'}],
        }
        payload['specialization_grades']={'Backend':'Junior','DevOps / SRE':'Middle'}
        result=self.client.post('/api/auth/register',json=payload)
        self.assertEqual(result.status_code,200,result.text)

    def tearDown(self):
        self.client.__exit__(None,None,None)
        if self.previous is None:os.environ.pop('FSP_DB',None)
        else:os.environ['FSP_DB']=self.previous
        self.temp.cleanup()

    def test_pdf_requires_own_specialization_and_generates_download(self):
        self.assertEqual(self.client.get('/api/profile/resume.pdf').status_code,422)
        self.assertEqual(self.client.get('/api/profile/resume.pdf?specialization=QA').status_code,404)
        for specialization in ('Backend','DevOps / SRE'):
            result=self.client.get('/api/profile/resume.pdf',params={'specialization':specialization})
            self.assertEqual(result.status_code,200,result.text[:200] if result.status_code!=200 else '')
            self.assertEqual(result.headers['content-type'],'application/pdf')
            self.assertIn('attachment',result.headers['content-disposition'])
            self.assertTrue(result.content.startswith(b'%PDF-'))
            self.assertGreater(len(result.content),10000)


if __name__=='__main__':unittest.main()
