import os
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import app
from backend.db import connect
from backend.tests.registration import candidate_payload

class MarketplaceTests(unittest.TestCase):
    def invitation(self,**changes):
        return {'match_id':'secure-match-token-123456','company_name':'Компания',
                'description':'Разработка и развитие внутренней платформы компании.',
                'salary_min':120000,'salary_max':180000,'contact_method':'Чат на платформе',
                **changes}

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
        with connect() as c:
            c.execute("UPDATE employer_profiles SET access_status='approved' WHERE user_id=?",(self.company_id,))
            resume_id=c.execute('SELECT id FROM resumes WHERE user_id=?',(self.candidate_id,)).fetchone()[0]
            c.execute('''INSERT INTO candidate_match_access
              (match_id,employer_id,vacancy_id,candidate_id,resume_id,created_at,last_seen_at)
              VALUES(?,?,?,?,?,'now','now')''',('secure-match-token-123456',self.company_id,
                                               self.vacancy_id,self.candidate_id,resume_id))
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
        r=self.employer.post(f'/api/vacancies/{self.vacancy_id}/invite',json=self.invitation())
        self.assertEqual(r.status_code,201,r.text)
        interaction_id=r.json()['id']
        pending=self.candidate.get(f'/api/interactions/{interaction_id}').json()
        self.assertEqual((pending['salary_min'],pending['salary_max']),(120000,180000))
        self.assertEqual(pending['contact_method'],'Чат на платформе')
        self.assertEqual(pending['company_name'],'Компания')
        self.assertEqual(pending['vacancy_id'],self.vacancy_id)
        with connect() as c:
            c.execute('UPDATE vacancies SET salary_min=90000,salary_max=110000 WHERE id=?',(self.vacancy_id,))
        updated=self.candidate.get(f'/api/interactions/{interaction_id}').json()
        self.assertEqual((updated['salary_min'],updated['salary_max']),(120000,180000))
        self.assertEqual(self.employer.post(f'/api/interactions/{interaction_id}/decision',json={'status':'approved'}).status_code,403)
        self.assertEqual(self.candidate.post(f'/api/interactions/{interaction_id}/decision',json={'status':'approved'}).status_code,200)
        self.assertEqual(self.employer.post(f'/api/interactions/{interaction_id}/messages',json={'body':'Приглашаем вас'}).status_code,201)
        self.assertEqual(self.other.get('/api/interactions').json()['items'],[])

    def test_demo_resume_offer_is_saved_without_delivery_or_chat(self):
        with connect() as c:
            c.execute("INSERT INTO users(id,role,name,origin,created_at) VALUES('demo','candidate','Имя из импорта','dataset','now')")
            c.execute("INSERT INTO resumes(id,user_id,specialization,position_name,raw_json,created_at,updated_at) VALUES('demo-resume','demo','Backend','Разработчик','{}','now','now')")
            c.execute('''INSERT INTO candidate_match_access
              (match_id,employer_id,vacancy_id,candidate_id,resume_id,created_at,last_seen_at)
              VALUES(?,?,?,?,?,'now','now')''',('demo-match-token-123456',self.company_id,
                                             self.vacancy_id,'demo','demo-resume'))
        offer=self.employer.post(f'/api/vacancies/{self.vacancy_id}/invite',
            json=self.invitation(match_id='demo-match-token-123456'))
        self.assertEqual(offer.status_code,201,offer.text)
        self.assertEqual(offer.json()['delivery_status'],'demo_only')
        invitation_id=offer.json()['id']
        listing=self.employer.get('/api/interactions').json()['items']
        saved=next(item for item in listing if item['id']==invitation_id)
        self.assertEqual(saved['delivery_status'],'demo_only')
        self.assertEqual(saved['candidate_name'],'Кандидат')
        self.assertIsNone(saved['candidate_id'])
        detail=self.employer.get(f'/api/interactions/{invitation_id}').json()
        self.assertEqual(detail['delivery_status'],'demo_only')
        self.assertFalse(detail['chat_enabled'])
        self.assertIsNone(detail['resume_id'])
        self.assertEqual(self.employer.post(f'/api/interactions/{invitation_id}/messages',
            json={'body':'Здравствуйте'}).status_code,409)
        self.assertEqual(self.employer.post(f'/api/vacancies/{self.vacancy_id}/invite',
            json=self.invitation(match_id='demo-match-token-123456')).status_code,409)

    def test_direct_invitation_is_disabled(self):
        response=self.employer.post('/api/invitations',json=self.invitation(title='Работа над API'))
        self.assertEqual(response.status_code,403,response.text)

    def test_direct_candidate_search_is_disabled(self):
        found=self.employer.get('/api/employer/candidates/search?q=Анна')
        self.assertEqual(found.status_code,403,found.text)
        self.assertEqual(self.candidate.get('/api/employer/candidates/search?q=Анна').status_code,403)
    def test_existing_vacancies_default_to_public_open(self):
        with connect() as c:
            row=c.execute('SELECT visibility,status FROM vacancies WHERE id=?',(self.vacancy_id,)).fetchone()
            self.assertEqual(tuple(row),('public','open'))
            c.execute("INSERT INTO vacancies(id,vacancy_name,raw_json,created_at) VALUES('000-import','Импорт','{}','2026-01-01')")
        first=self.employer.get('/api/vacancies?limit=1').json()['items'][0]
        self.assertEqual(first['id'],self.vacancy_id)

if __name__=='__main__':unittest.main()
