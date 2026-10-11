import os
import json
import csv
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import app
from backend.db import connect, init_db
from backend.import_data import import_file
from backend.splitting import split_resume, chunks, digest, insert_item
from backend.embedding_worker import run_batch, MODEL, REVISION, TASK, DIMENSIONS
from backend.tests.registration import candidate_payload

class BackendTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite3';self.old=os.environ.get('FSP_DB');os.environ['FSP_DB']=str(self.path)
  self.client=TestClient(app);self.client.__enter__()
 def tearDown(self):
  self.client.__exit__(None,None,None)
  if self.old is None:os.environ.pop('FSP_DB',None)
  else:os.environ['FSP_DB']=self.old
  self.tmp.cleanup()
 def signup(self,email='nika@example.org',role='candidate'):
  payload=candidate_payload(email=email) if role=='candidate' else {'name':'Ника','email':email,'password':'test-password-123','role':role,'company_name':'Acme'}
  r=self.client.post('/api/auth/register',json=payload);self.assertEqual(r.status_code,200,r.text);return r.json()['user']
 def test_auth_password_hash_logout_login_and_duplicate(self):
  u=self.signup();self.assertEqual(self.client.get('/api/auth/me').json()['user']['id'],u['id'])
  with connect() as c:
   row=c.execute('SELECT password_hash FROM users WHERE id=?',(u['id'],)).fetchone();self.assertTrue(row[0].startswith('$argon2id$'));self.assertNotIn('test-password',row[0])
   token=c.execute('SELECT token_hash FROM sessions').fetchone()[0];self.assertNotEqual(token,self.client.cookies.get('fsp_session'))
  self.assertEqual(self.client.post('/api/auth/register',json=candidate_payload(name='N',email='NIKA@example.org')).status_code,409)
  self.client.post('/api/auth/logout');self.assertEqual(self.client.get('/api/profile').status_code,401)
  self.assertEqual(self.client.post('/api/auth/login',json={'email':'nika@example.org','password':'wrong'}).status_code,401)
  self.assertEqual(self.client.post('/api/auth/login',json={'email':'nika@example.org','password':'test-password-123'}).status_code,200)
 def test_roles_and_data_isolation(self):
  u=self.signup();self.assertEqual(self.client.get('/api/candidates').status_code,403)
  p=self.client.get('/api/profile').json();p['profile']['fsp']='own-id'
  self.assertEqual(self.client.put('/api/profile',json=p).status_code,200)
  self.client.post('/api/auth/logout');self.signup('other@example.org')
  self.assertEqual(self.client.get('/api/profile').json()['profile']['fsp'],'')
  self.client.post('/api/auth/logout');self.signup('employer@example.org','employer')
  self.assertEqual(self.client.get('/api/profile').status_code,403);self.assertEqual(self.client.get('/api/database/stats').status_code,200)
 def test_profile_items_queue_concurrency_and_reset(self):
  u=self.signup();p=self.client.get('/api/profile').json()
  p['profile'].update({'selected':['Backend'],'active':'Backend','jobs':{'Backend':[{'company':'Acme','role':'Developer','start':'2020','end':'Present','description':'Python API'}]},'ownProjects':{'Backend':[{'name':'Pet app','description':'Docker','tasks':'API'}]},'stacks':{'Backend':['Python']}})
  result=self.client.put('/api/profile',json=p);self.assertEqual(result.status_code,200,result.text)
  self.assertEqual(self.client.put('/api/profile',json=p).status_code,409)
  with connect() as c:
   self.assertEqual(c.execute('SELECT COUNT(*) FROM work_items WHERE user_id=?',(u['id'],)).fetchone()[0],2)
   self.assertEqual(c.execute('SELECT COUNT(*) FROM embedding_chunks').fetchone()[0],2)
  p=result.json();p['profile']['fsp']='edit';self.client.put('/api/profile',json=p)
  with connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM work_items').fetchone()[0],2)
  self.assertEqual(self.client.post('/api/profile/reset',json={'revision':2}).status_code,200)
  self.assertEqual(self.client.get('/api/profile/items').json()['items'],[])
 def test_csrf_origin_and_registration_validation(self):
  payload={'name':'Nika','email':'n@example.org','password':'short','role':'candidate'}
  self.assertEqual(self.client.post('/api/auth/register',json=payload).status_code,422)
  payload['password']='long-password';self.assertEqual(self.client.post('/api/auth/register',json=payload,headers={'Origin':'https://evil.example'}).status_code,403)
 def test_candidate_is_inserted_only_after_complete_registration(self):
  incomplete={'name':'Ника','email':'draft@example.org','password':'test-password-123','role':'candidate'}
  self.assertEqual(self.client.post('/api/auth/register',json=incomplete).status_code,422)
  with connect() as c:self.assertEqual(c.execute("SELECT count(*) FROM users WHERE email='draft@example.org'").fetchone()[0],0)
  complete=candidate_payload(email='draft@example.org',grade='Middle')
  missing_salary=candidate_payload(email='salary-missing@example.org')
  del missing_salary['profile']['salaryMin']
  self.assertEqual(self.client.post('/api/auth/register',json=missing_salary).status_code,422)
  with connect() as c:self.assertEqual(c.execute("SELECT count(*) FROM users WHERE email='salary-missing@example.org'").fetchone()[0],0)
  complete['profile']['fsp']='fsp-123'
  complete['profile']['stacks']={'Backend':['Python']}
  complete['profile']['jobs']={'Backend':[{'company':'Acme','role':'Backend Developer','start':'2022','end':'2025','description':'Python API'}]}
  complete['profile']['ownProjects']={'Backend':[{'name':'Pet app','description':'Сервис на Python','tasks':'Разработка API'}]}
  self.assertEqual(self.client.post('/api/auth/register',json=complete).status_code,200)
  with connect() as c:
   self.assertEqual(c.execute("SELECT count(*) FROM users WHERE email='draft@example.org'").fetchone()[0],1)
   self.assertEqual(c.execute("SELECT declared_grade FROM candidate_specialization_grades WHERE specialization='Backend'").fetchone()[0],'Middle')
   self.assertEqual(c.execute("SELECT expected_salary_min FROM resumes WHERE user_id=(SELECT id FROM users WHERE email='draft@example.org')").fetchone()[0],70000)
  def assert_saved_profile():
   saved=self.client.get('/api/profile')
   self.assertEqual(saved.status_code,200,saved.text)
   p=saved.json()['profile']
   self.assertEqual(p['step'],4)
   self.assertEqual(p['fsp'],'fsp-123')
   self.assertEqual(p['selected'],['Backend'])
   self.assertEqual(p['stacks']['Backend'],['Python'])
   self.assertEqual(p['jobs']['Backend'][0]['company'],'Acme')
   self.assertEqual(p['ownProjects']['Backend'][0]['name'],'Pet app')
  assert_saved_profile()
  self.assertEqual(self.client.post('/api/auth/logout').status_code,200)
  self.assertEqual(self.client.post('/api/auth/login',json={'email':'draft@example.org','password':'test-password-123'}).status_code,200)
  assert_saved_profile()
 def test_employer_can_save_and_index_own_vacancy(self):
  owner=self.signup('owner@example.org','employer')
  payload={'vacancy_name':'Backend Engineer','specialization':'Backend','grade':'Junior','salary_min':80000,'salary_max':150000,
           'description':'Разрабатываем сервисы для управления заказами и платежами.',
           'responsibilities':'Разрабатывать API, проектировать сервисы и улучшать производительность.',
           'position_requirements':'Опыт с Python и PostgreSQL'}
  result=self.client.post('/api/vacancies',json=payload)
  self.assertEqual(result.status_code,201,result.text)
  vid=result.json()['id']
  mine=self.client.get('/api/vacancies?mine=true').json()['items']
  self.assertEqual(len(mine),1)
  self.assertEqual(mine[0]['employer_id'],owner['id'])
  self.assertEqual(mine[0]['grade'],'Junior')
  self.assertEqual(json.loads(mine[0]['required_technologies_json']),[])
  bad={**payload,'salary_max':70000}
  self.assertEqual(self.client.post('/api/vacancies',json=bad).status_code,422)
  bad={**payload,'required_technologies':['Imaginary Framework']}
  self.assertEqual(self.client.post('/api/vacancies',json=bad).status_code,422)
  required={**payload,'required_technologies':['Python']}
  created=self.client.post('/api/vacancies',json=required)
  self.assertEqual(created.status_code,201,created.text)
  with connect() as c:
   self.assertEqual(json.loads(c.execute('SELECT required_technologies_json FROM vacancies WHERE id=?',(created.json()['id'],)).fetchone()[0]),['Python'])
  self.assertIn('Python',json.loads(mine[0]['all_technologies_json']))
  with patch('backend.ranking_cli.index_vacancies') as indexer:
   self.assertEqual(self.client.post(f'/api/vacancies/{vid}/index').status_code,200)
   indexer.assert_called_once_with(None,[vid])
  self.client.post('/api/auth/logout')
  self.signup('another@example.org','employer')
  self.assertEqual(self.client.post(f'/api/vacancies/{vid}/index').status_code,403)
  self.assertEqual(self.client.get(f'/api/matches/{vid}').status_code,403)
 def test_multiple_specializations_share_stack_but_keep_experience_separate(self):
  payload=candidate_payload(email='multi@example.org')
  payload['profile']['selected']=['Backend','Frontend']
  payload['profile']['stacks']={'Backend':['Python'],'Frontend':[]}
  payload['profile']['jobs']={'Frontend':[{'company':'Web Studio','role':'Developer','start':'2023','end':'2025','description':'Interfaces'}]}
  payload['profile']['ownProjects']={'Backend':[{'name':'API','description':'Backend API','tasks':'Python'}]}
  payload['specialization_grades']={'Backend':'Middle','Frontend':'Junior'}
  result=self.client.post('/api/auth/register',json=payload)
  self.assertEqual(result.status_code,200,result.text)
  saved=self.client.get('/api/profile').json()['profile']
  self.assertEqual(saved['stacks'],{'Backend':['Python'],'Frontend':['Python']})
  with connect() as c:
   blocks=c.execute('SELECT kind,specialization FROM work_items WHERE user_id=? ORDER BY kind',(result.json()['user']['id'],)).fetchall()
   self.assertEqual({(row['kind'],row['specialization']) for row in blocks},{('work','Frontend'),('project','Backend')})
  current=self.client.get('/api/profile').json()
  current['profile']['stacks']={'Backend':[],'Frontend':[]}
  self.assertEqual(self.client.put('/api/profile',json=current).status_code,200)
  self.assertEqual(self.client.get('/api/profile').json()['profile']['stacks'],{'Backend':[],'Frontend':[]})
 def test_split_composite_deduplicated_metadata_and_project(self):
  row={'work_experience_text':'Developer @ Acme [2020-01-01 — present] Python API || Analyst @ Beta [2018 — 2020] SQL', 'work_experience_titles':'Developer; Analyst','work_experience_companies':'Acme; Beta','additional_information':'Проект: Resume app\nDocker API'}
  items=split_resume(row);self.assertEqual([i['kind'] for i in items],['work','work','project']);self.assertEqual(items[0]['company'],'Acme')
  fallback=split_resume({'work_experience_descriptions':'First || Second','work_experience_titles':'only one'})
  self.assertEqual(len(fallback),2);self.assertEqual(fallback[1]['title'],'');self.assertTrue(fallback[0]['needs_review'])
  self.assertEqual(split_resume({}),[])
 def test_chunks_preserve_all_non_whitespace_text(self):
  text='a'*2400+' '+'b'*2400
  result=list(chunks(text));self.assertEqual(''.join(s.replace(' ','') for s in result),text.replace(' ',''));self.assertTrue(all(len(s)<=2200 for s in result))
 def test_full_csv_import_is_idempotent_and_preserves_raw(self):
  file=Path(self.tmp.name)/'cv.csv';row={'position_name':'Dev','work_experience_text':'Developer @ Acme [2020 — present] Python','all_technologies':'["Python"]','age':'unknown'}
  with file.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=row);w.writeheader();w.writerow(row)
  self.assertEqual(import_file(file,'resumes')['rows'],1);self.assertTrue(import_file(file,'resumes')['already_imported'])
  with connect() as c:
   self.assertEqual(c.execute('SELECT COUNT(*) FROM resumes').fetchone()[0],1);r=c.execute('SELECT raw_json,age FROM resumes').fetchone();self.assertEqual(json.loads(r[0]),row);self.assertIsNone(r[1])
   u=c.execute('SELECT email,password_hash,origin FROM users').fetchone();self.assertEqual(tuple(u),(None,None,'dataset'))
 def test_vector_worker_transaction_dedup_and_real_metadata(self):
  self.signup();p=self.client.get('/api/profile').json();p['profile'].update({'selected':['Backend'],'jobs':{'Backend':[{'description':'Python API'},{'description':'Python API'}]}});self.client.put('/api/profile',json=p)
  class FakeForUnitTest:
   calls=0
   def encode(self,texts):
    self.calls+=1;v=np.ones((len(texts),DIMENSIONS),dtype=np.float32);return v
  fake=FakeForUnitTest();run_batch(fake);self.assertEqual(fake.calls,1)
  with connect() as c:
   self.assertEqual(c.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0],1);self.assertEqual(c.execute('SELECT COUNT(*) FROM item_vectors').fetchone()[0],2)
   row=c.execute('SELECT vector FROM embeddings').fetchone();self.assertAlmostEqual(float(np.linalg.norm(np.frombuffer(row[0],dtype='<f4'))),1,places=5)
  self.assertEqual(run_batch(fake),0)
 def test_embedding_failure_is_visible(self):
  self.signup();p=self.client.get('/api/profile').json();p['profile'].update({'selected':['Backend'],'jobs':{'Backend':[{'description':'Python API'}]}});self.client.put('/api/profile',json=p)
  class Fails:
   def encode(self,texts):raise RuntimeError('Test failure')
  with self.assertRaises(RuntimeError):run_batch(Fails())
  with connect() as c:self.assertEqual(c.execute('SELECT status FROM embedding_chunks').fetchone()[0],'failed');self.assertEqual(c.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0],0)

if __name__=='__main__':unittest.main()
