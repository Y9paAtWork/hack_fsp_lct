import os
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import app
from backend.db import connect
from backend.tests.registration import candidate_payload

class MarketplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=os.environ.get('FSP_DB')
        os.environ['FSP_DB']=str(Path(self.tmp.name)/'marketplace.sqlite3')
        self.employer=TestClient(app);self.employer.__enter__()
        self.candidate=TestClient(app);self.candidate.__enter__()
        self.other=TestClient(app);self.other.__enter__()
        r=self.employer.post('/api/auth/register',json={'name':'Компания','email':'company@example.org','password':'password123','role':'employer','company_name':'Компания'})
        self.assertEqual(r.status_code,200,r.text)
        self.company_id=r.json()['user']['id']
        r=self.candidate.post('/api/auth/register',json=candidate_payload(name='Анна',email='anna@example.org',password='password123',grade='Middle'))
        self.assertEqual(r.status_code,200,r.text)
        self.candidate_id=r.json()['user']['id']
        self.other.post('/api/auth/register',json=candidate_payload(name='Другая',email='other@example.org',password='password123'))
        r=self.employer.post('/api/vacancies',json={'vacancy_name':'Backend Developer','specialization':'Backend','grade':'Middle','salary_min':100000,'salary_max':180000,'description':'Работа над платформой заказов и внутренними сервисами.','responsibilities':'Разработка API и оптимизация запросов для платформы заказов.','visibility':'public'})
        self.assertEqual(r.status_code,201,r.text)
        self.vacancy_id=r.json()['id']
    def tearDown(self):
        for client in (self.other,self.candidate,self.employer):client.__exit__(None,None,None)
        if self.old is None:os.environ.pop('FSP_DB',None)
        else:os.environ['FSP_DB']=self.old
        self.tmp.cleanup()
    def test_application_approval_chat_isolation_and_archive(self):
        listing=self.candidate.get('/api/public/vacancies?specialization=Backend').json()
        self.assertEqual(listing['ranking_status'],'placeholder')
        self.assertEqual(listing['items'][0]['id'],self.vacancy_id)
        self.assertEqual(self.candidate.get('/api/tests/technologies').json()['grade'],'Middle')
        r=self.candidate.post(f'/api/vacancies/{self.vacancy_id}/apply')
        self.assertEqual(r.status_code,201,r.text)
        interaction_id=r.json()['id']
        self.assertEqual(self.candidate.post(f'/api/vacancies/{self.vacancy_id}/apply').status_code,409)
        self.assertEqual(self.candidate.post(f'/api/interactions/{interaction_id}/messages',json={'body':'Здравствуйте'}).status_code,409)
        self.assertEqual(self.other.get(f'/api/interactions/{interaction_id}').status_code,403)
        employer_items=self.employer.get(f'/api/interactions?vacancy_id={self.vacancy_id}').json()['items']
        self.assertEqual(employer_items[0]['id'],interaction_id)
        self.assertEqual(self.candidate.post(f'/api/interactions/{interaction_id}/decision',json={'status':'approved'}).status_code,403)
        self.assertEqual(self.employer.post(f'/api/interactions/{interaction_id}/decision',json={'status':'approved'}).status_code,200)
        self.assertEqual(self.candidate.post(f'/api/interactions/{interaction_id}/messages',json={'body':'Здравствуйте'}).status_code,201)
        self.assertEqual(self.employer.post(f'/api/interactions/{interaction_id}/messages',json={'body':'Давайте обсудим опыт'}).status_code,201)
        detail=self.candidate.get(f'/api/interactions/{interaction_id}').json()
        self.assertEqual([m['body'] for m in detail['messages']],['Здравствуйте','Давайте обсудим опыт'])
        self.assertEqual(self.employer.get('/api/employer/overview').json()['applications'],1)
        self.assertEqual(self.employer.patch(f'/api/employer/vacancies/{self.vacancy_id}',json={'visibility':'public','status':'closed'}).status_code,200)
        self.assertEqual(self.candidate.get('/api/public/vacancies?specialization=Backend').json()['total'],0)
        self.assertEqual(self.other.post(f'/api/vacancies/{self.vacancy_id}/apply').status_code,409)
        self.assertEqual(self.employer.get(f'/api/matches/{self.vacancy_id}').status_code,409)
        self.assertEqual(self.employer.get('/api/employer/overview').json()['archived_vacancies'],1)
    def test_private_vacancy_and_invitation_flow(self):
        self.assertEqual(self.employer.patch(f'/api/employer/vacancies/{self.vacancy_id}',json={'visibility':'private','status':'open'}).status_code,200)
        self.assertEqual(self.candidate.get('/api/public/vacancies').json()['total'],0)
        self.assertEqual(self.candidate.post(f'/api/vacancies/{self.vacancy_id}/apply').status_code,403)
        r=self.employer.post(f'/api/vacancies/{self.vacancy_id}/invite',json={'candidate_id':self.candidate_id})
        self.assertEqual(r.status_code,201,r.text)
        interaction_id=r.json()['id']
        self.assertEqual(self.employer.post(f'/api/interactions/{interaction_id}/decision',json={'status':'approved'}).status_code,403)
        self.assertEqual(self.candidate.post(f'/api/interactions/{interaction_id}/decision',json={'status':'approved'}).status_code,200)
        self.assertEqual(self.employer.post(f'/api/interactions/{interaction_id}/messages',json={'body':'Приглашаем вас'}).status_code,201)
        self.assertEqual(self.other.get('/api/interactions').json()['items'],[])
    def test_existing_vacancies_default_to_public_open(self):
        with connect() as c:
            row=c.execute('SELECT visibility,status FROM vacancies WHERE id=?',(self.vacancy_id,)).fetchone()
            self.assertEqual(tuple(row),('public','open'))
            c.execute("INSERT INTO vacancies(id,vacancy_name,raw_json,created_at) VALUES('000-import','Импорт','{}','2026-01-01')")
        first=self.employer.get('/api/vacancies?limit=1').json()['items'][0]
        self.assertEqual(first['id'],self.vacancy_id)

if __name__=='__main__':unittest.main()
