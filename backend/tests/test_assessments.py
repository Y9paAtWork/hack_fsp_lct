import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import numpy as np

from backend.app import app
from backend.assessments import daily_public, grade_progress, grade_question, moscow_day, objective_score, retry_allowed, text_score, update_grades
from backend.db import connect
from backend.ranking import catalog
from backend.sandbox import grade_code
from backend.sandbox_worker import docker_call, grade as worker_grade
from backend.tests.registration import candidate_payload


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'test.sqlite3'
        self.previous=os.environ.get('FSP_DB')
        os.environ['FSP_DB']=str(self.path)
        self.client=TestClient(app)
        self.client.__enter__()
        r=self.client.post('/api/auth/register',json=candidate_payload())
        self.assertEqual(r.status_code,200,r.text)
        self.user_id=r.json()['user']['id']

    def tearDown(self):
        self.client.__exit__(None,None,None)
        if self.previous is None:os.environ.pop('FSP_DB',None)
        else:os.environ['FSP_DB']=self.previous
        self.tmp.cleanup()

    def set_stack(self,technology='Git'):
        data=self.client.get('/api/profile').json()
        data['profile'].update({'selected':['Backend'],'active':'Backend','stacks':{'Backend':[technology]}})
        r=self.client.put('/api/profile',json=data)
        self.assertEqual(r.status_code,200,r.text)

    def test_profile_cannot_supply_test_score_or_grade(self):
        data=self.client.get('/api/profile').json()
        data['profile']['quiz']={'total':1,'correct':1,'score':100}
        data['profile']['grade']='Senior'
        data['profile']['selected']=['Backend']
        data['profile']['stacks']={'Backend':['Git','NotInFinalCatalog']}
        result=self.client.put('/api/profile',json=data)
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(result.json()['profile']['quiz'])
        self.assertNotIn('grade',result.json()['profile'])
        self.assertEqual(result.json()['profile']['stacks']['Backend'],['Git'])
        with connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM candidate_verified_grades WHERE user_id=?',(self.user_id,)).fetchone()[0],0)

    def test_technology_catalog_only_shows_own_grade_until_promotion(self):
        self.set_stack('Git')
        with connect() as c:
            c.execute("UPDATE candidate_specialization_grades SET current_grade='Middle' WHERE user_id=? AND specialization='Backend'",(self.user_id,))
        items=[item for item in self.client.get('/api/tests/technologies').json()['items'] if item['technology']=='Git']
        self.assertEqual([item['grade'] for item in items],['Middle'])
        self.assertEqual(self.client.post('/api/tests/technologies/start',json={'technology':'Git','grade':'Junior'}).status_code,409)
        self.assertEqual(self.client.post('/api/tests/technologies/start',json={'technology':'Git','grade':'Middle'}).status_code,200)

    def test_independent_specialization_grades_control_tests_and_promotion(self):
        data=self.client.get('/api/profile').json()
        data['profile'].update({'selected':['Backend','Frontend'],'active':'Backend',
                                'stacks':{'Backend':['Git'],'Frontend':['Git']}})
        self.assertEqual(self.client.put('/api/profile',json=data).status_code,200)
        declared=self.client.put('/api/grades/declarations',json={'grades':{'Backend':'Middle','Frontend':'Junior'}})
        self.assertEqual(declared.status_code,200,declared.text)
        self.assertEqual(declared.json()['grades']['Backend']['current_grade'],'Middle')
        self.assertEqual(self.client.get('/api/tests/technologies?specialization=Backend').json()['grade'],'Middle')
        self.assertEqual(self.client.get('/api/tests/technologies?specialization=Frontend').json()['grade'],'Junior')
        self.assertEqual(self.client.post('/api/tests/technologies/start',json={
            'technology':'Git','grade':'Middle','specialization':'Frontend'}).status_code,409)
        with connect() as c:
            c.execute('INSERT INTO candidate_verified_grades VALUES(?,?,?,?,?,?)',
                      (self.user_id,'Backend','Middle','test-rule','test-bank',datetime.now(timezone.utc).isoformat()))
        advanced=self.client.post('/api/grades/advance?specialization=Backend')
        self.assertEqual(advanced.status_code,200,advanced.text)
        self.assertEqual(advanced.json()['grade'],'Senior')
        self.assertEqual(self.client.get('/api/grades/current?specialization=Frontend').json()['grade'],'Junior')
        progress=self.client.get('/api/grades/progress').json()['specializations']
        self.assertEqual(progress['Backend']['current_grade'],'Senior')
        self.assertEqual(progress['Frontend']['current_grade'],'Junior')
        self.assertEqual(self.client.put('/api/grades/declarations',json={'grades':{
            'Backend':'Junior','Frontend':'Junior'}}).status_code,409)

    def test_grade_advance_requires_confirmation_and_preserves_initial_grade(self):
        self.set_stack('Git')
        self.assertEqual(self.client.post('/api/grades/advance').status_code,409)
        with connect() as c:
            c.execute('INSERT INTO candidate_verified_grades VALUES(?,?,?,?,?,?)',
                      (self.user_id,'Backend','Junior','test-rule','test-bank',datetime.now(timezone.utc).isoformat()))
        result=self.client.post('/api/grades/advance')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['grade'],'Middle')
        self.assertEqual(self.client.get('/api/tests/technologies').json()['grade'],'Middle')
        with connect() as c:
            self.assertEqual(c.execute('SELECT grade FROM candidate_initial_grades WHERE user_id=?',(self.user_id,)).fetchone()[0],'Junior')

    def test_technology_answers_hidden_score_vector_retake_and_restart(self):
        self.set_stack()
        catalog_response=self.client.get('/api/tests/technologies')
        self.assertEqual(catalog_response.status_code,200)
        item=next(x for x in catalog_response.json()['items'] if x['technology']=='Git' and x['grade']=='Junior')
        self.assertTrue(item['available'])
        start=self.client.post('/api/tests/technologies/start',json={'technology':'Git','grade':'Junior'})
        self.assertEqual(start.status_code,200,start.text)
        payload=start.json()
        self.assertNotIn('correct_answers',start.text)
        self.assertNotIn('explanation',start.text)
        with TestClient(app) as other:
            other.post('/api/auth/register',json=candidate_payload(name='Другой',email='other@example.org'))
            self.assertEqual(other.post(f"/api/tests/technologies/{payload['attempt_id']}/submit",
                                   json={'answers':{}}).status_code,404)
        with connect() as c:
            answers={q['id']:json.loads(c.execute('SELECT correct_answers_json FROM test_questions WHERE id=?',(q['id'],)).fetchone()[0])[0] for q in payload['questions']}
        result=self.client.post(f"/api/tests/technologies/{payload['attempt_id']}/submit",json={'answers':answers})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['score'],100)
        self.assertEqual(self.client.post('/api/tests/technologies/start',json={'technology':'Git','grade':'Junior'}).status_code,409)
        with connect() as c:
            names,_=catalog()
            vector=np.frombuffer(c.execute('SELECT vector FROM candidate_skill_vectors WHERE user_id=?',(self.user_id,)).fetchone()[0],dtype='<f4')
            self.assertEqual(vector[names.index('Git')],1.0)
            self.assertEqual(c.execute('SELECT source FROM skill_test_results WHERE user_id=? AND technology=?',(self.user_id,'Git')).fetchone()[0],'verified')
            earlier=(datetime.now(timezone.utc)-timedelta(days=30,seconds=1)).isoformat()
            c.execute('UPDATE assessment_attempts SET completed_at=? WHERE id=?',(earlier,payload['attempt_id']))
        self.assertEqual(self.client.post('/api/tests/technologies/start',json={'technology':'Git','grade':'Junior'}).status_code,200)
        self.client.post('/api/auth/logout')
        self.client.__exit__(None,None,None)
        self.client=TestClient(app)
        self.client.__enter__()
        login=self.client.post('/api/auth/login',json={'email':'nika@example.org','password':'test-password-123'})
        self.assertEqual(login.status_code,200)
        self.assertEqual(self.client.get('/api/tests/technologies').status_code,200)
        self.assertEqual(self.client.get('/api/profile').json()['profile']['stacks']['Backend'],['Git'])

    def test_objective_and_text_calibration(self):
        self.assertEqual(objective_score('single','A',['A']),1)
        self.assertEqual(objective_score('multi',['B','A'],['A','B']),1)
        self.assertEqual(objective_score('multi',['A','A'],['A']),0)
        class Encoder:
            def encode(self,texts):return [[1.0,0.0],[0.9,0.43589]]
        with patch('backend.assessments.text_encoder',return_value=Encoder()):
            self.assertGreater(text_score('first','second'),0.9)
        class Identical:
            def encode(self,texts):return [[1.0,0.0],[1.0,0.0]]
        with patch('backend.assessments.text_encoder',return_value=Identical()):
            self.assertEqual(text_score('the same','the same'),1)

    def test_retake_opens_at_exact_30_day_boundary(self):
        completed=datetime(2026,1,1,12,tzinfo=timezone.utc)
        boundary=completed+timedelta(days=30)
        self.assertFalse(retry_allowed(completed.isoformat(),boundary-timedelta(microseconds=1)))
        self.assertTrue(retry_allowed(completed.isoformat(),boundary))

    def test_daily_moscow_boundary_one_completion_and_activity(self):
        before=datetime(2026,10,11,20,59,tzinfo=timezone.utc)
        after=datetime(2026,10,11,21,0,tzinfo=timezone.utc)
        self.assertNotEqual(moscow_day(before),moscow_day(after))
        with connect() as c:
            first=daily_public(c,self.user_id,before)
            self.assertEqual(first['grade'],'Junior')
            self.assertEqual(first['date_msk'],'2026-10-11')
            next_day=daily_public(c,self.user_id,after)
            self.assertEqual(next_day['date_msk'],'2026-10-12')
            self.assertEqual(daily_public(c,self.user_id,before)['questions'],first['questions'])
        current=self.client.get('/api/tests/daily')
        self.assertEqual(current.status_code,200)
        self.assertNotIn('correct_answer',current.text)
        ids=[q['id'] for q in current.json()['questions']]
        with connect() as c:
            answers={str(qid):json.loads(c.execute('SELECT correct_answer_json FROM daily_questions WHERE id=?',(qid,)).fetchone()[0]) for qid in ids}
        result=self.client.post('/api/tests/daily/submit',json={'answers':answers})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['score'],100)
        self.assertEqual(self.client.post('/api/tests/daily/submit',json={'answers':answers}).status_code,409)
        with connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM candidate_daily_tests WHERE user_id=?',(self.user_id,)).fetchone()[0],1)
            self.assertGreater(c.execute('SELECT activity_score FROM candidate_activity WHERE user_id=?',(self.user_id,)).fetchone()[0],0)

    def test_daily_uses_active_specialization_confirmed_grade(self):
        self.set_stack('Git')
        with connect() as c:
            c.execute('INSERT INTO candidate_verified_grades VALUES(?,?,?,?,?,?)',
                      (self.user_id,'Backend','Middle','test-rule','test-bank','2026-01-01'))
            tomorrow=datetime.now(timezone.utc)+timedelta(days=2)
            result=daily_public(c,self.user_id,tomorrow)
            self.assertEqual(result['specialization'],'Backend')
            self.assertEqual(result['grade'],'Middle')
            self.assertEqual(len(result['questions']),3)

    def test_grade_matrix_requires_groups_and_is_separate_from_heuristic(self):
        self.set_stack('Python')
        with connect() as c:
            for technology in ['Python','FastAPI','PostgreSQL']:
                aid='test-'+technology
                c.execute('INSERT INTO assessment_attempts VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (aid,self.user_id,technology,'Junior','[]','completed',100,'2026-01-01','2026-01-01','test'))
                c.execute('INSERT INTO technology_level_results VALUES(?,?,?,?,?,?,?,?)',
                          (self.user_id,technology,'Junior',100,1,aid,'2026-01-01','test'))
            progress=update_grades(c,self.user_id)
            self.assertEqual(progress['Backend']['confirmed_grade'],'Junior')
            self.assertEqual(progress['Backend']['next_grade'],'Middle')
            self.assertEqual(c.execute('SELECT count(*) FROM candidate_verified_grades WHERE user_id=?',(self.user_id,)).fetchone()[0],1)
            self.assertIn('SQLAlchemy',str(progress['Backend']['levels']['Middle']['groups']))

    def test_code_fails_closed_without_dedicated_worker(self):
        old=os.environ.pop('FSP_SANDBOX_SECRET',None)
        try:
            with self.assertRaises(RuntimeError):grade_code('Python','def most_common(nums): return None',[{'input':[[]],'expected':None}])
        finally:
            if old is not None:os.environ['FSP_SANDBOX_SECRET']=old

    def test_worker_limits_and_timeout_fail_closed(self):
        sample={'technology':'Python','code':'def most_common(nums): return 1',
                'tests':[{'input':[[1]],'expected':1}]}
        completed=b'{"results": [{"ok": true, "value": 1}]}'
        with patch('backend.sandbox_worker.docker_call',return_value=completed) as run:
            self.assertEqual(worker_grade(sample)['score'],1.0)
            command=run.call_args.args[0]
            for flag in ['--network=none','--read-only','--cap-drop=ALL','--pids-limit=32','--memory=128m']:
                self.assertIn(flag,command)
            self.assertNotIn(b'"expected"',run.call_args.args[1])
        forged=b'{"score": 1.0}'
        with patch('backend.sandbox_worker.docker_call',return_value=forged):
            with self.assertRaises(RuntimeError):worker_grade(sample)
        with patch('backend.sandbox_worker.docker_call',side_effect=RuntimeError('timeout')):
            with self.assertRaises(RuntimeError):worker_grade(sample)
        with self.assertRaises(RuntimeError):
            docker_call(['python3','-c','print("x"*5000)'],b'')

    def test_supported_question_types_use_their_graders(self):
        with connect() as c:
            for kind in ['single','multi']:
                row=c.execute('SELECT * FROM test_questions WHERE question_type=? LIMIT 1',(kind,)).fetchone()
                answer=json.loads(row['correct_answers_json'])
                score,_=grade_question(row,answer[0] if kind=='single' else answer)
                self.assertEqual(score,1)
            for kind in ['short_answer','code_debug']:
                row=c.execute('SELECT * FROM test_questions WHERE question_type=? LIMIT 1',(kind,)).fetchone()
                with patch('backend.assessments.text_score',return_value=.8):
                    score,version=grade_question(row,'Объяснение')
                self.assertEqual(score,.8)
                self.assertIn('text-matching',version)
            for technology in ['Python','JavaScript','SQL']:
                row=c.execute("SELECT * FROM test_questions WHERE technology=? AND question_type='write_code' LIMIT 1",(technology,)).fetchone()
                with patch('backend.sandbox.grade_code',return_value=.75) as run:
                    score,version=grade_question(row,'submitted code')
                self.assertEqual(score,.75)
                self.assertEqual(run.call_args.args[0],technology)
                self.assertIn('docker',version)

if __name__=='__main__':unittest.main()
