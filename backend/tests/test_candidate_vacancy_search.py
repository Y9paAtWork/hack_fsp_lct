import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from backend.app import app
from backend.candidate_vacancy_search import ensure_experience_vectors
from backend.db import connect
from backend.embedding_worker import DIMENSIONS, MODEL, REVISION
from backend.ranking import store_vacancy_query_vector
from backend.tests.registration import candidate_payload


class FakeEncoder:
    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        vectors = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for i, text in enumerate(texts):
            vectors[i, 0 if 'Python' in text else 1] = 1
        return vectors


class CandidateVacancySearchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = Path(self.folder.name) / 'site.sqlite3'
        self.old_db = os.environ.get('FSP_DB')
        os.environ['FSP_DB'] = str(self.db)
        self.client = TestClient(app)
        self.client.__enter__()
        self.other = TestClient(app)
        self.other.__enter__()
        payload = candidate_payload()
        payload['profile']['stacks'] = {'Backend': ['Python']}
        payload['profile']['jobs'] = {'Backend': [
            {'company': 'Acme', 'role': 'Разработчик', 'start': '2022', 'end': '2025',
             'description': 'Создавал Python API'}]}
        payload['profile']['ownProjects'] = {'Backend': [
            {'name': 'База данных', 'description': 'Проектировал схемы данных', 'tasks': 'Оптимизировал запросы'}]}
        response = self.client.post('/api/auth/register', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.user_id = response.json()['user']['id']
        response = self.other.post('/api/auth/register',
                                   json=candidate_payload(email='other@example.org'))
        self.assertEqual(response.status_code, 200, response.text)
        with connect() as c:
            for vid, title, spec, duties, visibility, status, technology in [
                ('a', 'Python developer', 'Backend', 'Создавать Python API', 'public', 'open', 'Python'),
                ('b', 'SQL developer', 'Backend', 'Работать с базами данных', 'public', 'open', 'SQL'),
                ('c', 'Private Python', 'Backend', 'Создавать Python API', 'private', 'open', 'Python'),
                ('d', 'Closed Python', 'Backend', 'Создавать Python API', 'public', 'closed', 'Python'),
                ('e', 'Frontend Python', 'Frontend', 'Создавать Python API', 'public', 'open', 'Python'),
            ]:
                c.execute('''INSERT INTO vacancies
                    (id,vacancy_name,specialization,responsibilities,raw_json,created_at,visibility,status)
                    VALUES(?,?,?,?,?,?,?,?)''',
                    (vid, title, spec, duties, '{}', 'now', visibility, status))
                c.execute('INSERT INTO vacancy_technologies VALUES(?,?)', (vid, technology))
                vector = np.zeros(DIMENSIONS, dtype=np.float32)
                vector[0 if technology == 'Python' else 1] = 1
                store_vacancy_query_vector(c, vid, vector)
            c.execute('''INSERT INTO vacancies
                (id,vacancy_name,specialization,responsibilities,raw_json,created_at)
                VALUES('f','Без вектора','Backend','Создавать API','{}','now')''')

    def tearDown(self):
        self.other.__exit__(None, None, None)
        self.client.__exit__(None, None, None)
        if self.old_db is None:
            os.environ.pop('FSP_DB', None)
        else:
            os.environ['FSP_DB'] = self.old_db
        self.folder.cleanup()

    def test_indexes_own_experience_then_ranks_only_open_public_specialization(self):
        encoder = FakeEncoder()
        ensure_experience_vectors(self.db, self.user_id, encoder=encoder)
        self.assertEqual(encoder.calls, 1)
        ensure_experience_vectors(self.db, self.user_id, encoder=encoder)
        self.assertEqual(encoder.calls, 1)
        with connect() as c:
            row = c.execute('''SELECT iv.model,iv.revision,iv.task,iv.dimensions
                FROM item_vectors iv WHERE iv.user_id=?''', (self.user_id,)).fetchone()
            self.assertEqual(tuple(row), (MODEL, REVISION, 'retrieval.passage', DIMENSIONS))
        result = self.client.post('/api/candidate/vacancies/search',
                                  json={'specialization': 'Backend', 'page': 1, 'limit': 1})
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['items'][0]['id'], 'a')
        self.assertEqual(data['items'][0]['components']['experience'], 1)
        self.assertEqual(data['items'][0]['components']['skills'], .5)
        self.assertEqual(data['experience_blocks'], 2)
        self.assertEqual(data['stale_vacancies'], 1)
        page2 = self.client.post('/api/candidate/vacancies/search',
                                 json={'specialization': 'Backend', 'page': 2, 'limit': 1}).json()
        self.assertEqual(page2['items'][0]['id'], 'b')
        self.assertEqual(page2['items'][0]['components']['experience'], 1)
        self.assertEqual(self.client.post('/api/candidate/vacancies/search',
                       json={'specialization': 'Frontend'}).status_code, 422)

    def test_missing_experience_stays_eligible_with_zero_experience_score(self):
        with connect() as c:
            c.execute('DELETE FROM work_items WHERE user_id=?', (self.user_id,))
        result = self.client.post('/api/candidate/vacancies/search',
                                  json={'specialization': 'Backend'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['total'], 2)
        self.assertTrue(all(item['components']['experience'] == 0 for item in result.json()['items']))

    def test_candidates_only_use_their_own_experience_and_stack(self):
        ensure_experience_vectors(self.db, self.user_id, encoder=FakeEncoder())
        result = self.other.post('/api/candidate/vacancies/search',
                                 json={'specialization': 'Backend'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(all(item['components']['experience'] == 0 and
                            item['components']['skills'] == 0 for item in result.json()['items']))


if __name__ == '__main__':
    unittest.main()
