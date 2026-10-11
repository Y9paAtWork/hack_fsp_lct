import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.db import connect, init_db
from backend.embedding_worker import DIMENSIONS, MODEL, REVISION
from backend.index_all_vacancies import index_all
from backend.ranking import vacancy_experience_text


class FakeEncoder:
    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        vectors = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        vectors[:, 0] = 1
        return vectors


class IndexAllVacanciesTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = Path(self.folder.name) / 'test.sqlite3'
        init_db(self.db)
        with connect(self.db) as c:
            c.executemany('''INSERT INTO vacancies
                (id,vacancy_name,responsibilities,description,raw_json,created_at)
                VALUES(?,?,?,?,?,?)''', [
                ('a', 'Backend developer', 'Разрабатывать API и сервисы.', '', '{}', 'now'),
                ('b', 'Аналитик', '', 'Обязанности: Проводить интервью и описывать процессы. Требования: SQL и Python.', '{}', 'now'),
                ('c', 'Инженер', '', 'Компания ищет инженера. Требования: SQL и Python.', '{}', 'now'),
            ])

    def tearDown(self):
        self.folder.cleanup()

    def test_resume_skip_and_reindex_changed_text(self):
        encoder = FakeEncoder()
        first = index_all(self.db, batch_size=2, limit=1, encoder=encoder, progress=lambda _: None)
        self.assertEqual(first['written'], 1)
        second = index_all(self.db, batch_size=2, encoder=encoder, progress=lambda _: None)
        self.assertEqual((second['current'], second['written']), (1, 2))
        self.assertEqual(second['title_only'], 1)
        self.assertEqual(len(encoder.calls), 2)
        dry = index_all(self.db, dry_run=True, progress=lambda _: None)
        self.assertEqual((dry['current'], dry['pending'], dry['written']), (3, 0, 0))
        with connect(self.db) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM vacancy_query_vectors').fetchone()[0], 3)
            metadata = c.execute("SELECT model,revision,task,dimensions,length(vector) FROM vacancy_query_vectors WHERE vacancy_id='b'").fetchone()
            self.assertEqual(tuple(metadata), (MODEL, REVISION, 'retrieval.query', DIMENSIONS, 4 * DIMENSIONS))
            c.execute("UPDATE vacancies SET responsibilities='Новые задачи' WHERE id='a'")
        changed = index_all(self.db, encoder=encoder, progress=lambda _: None)
        self.assertEqual((changed['current'], changed['written']), (2, 1))
        self.assertEqual(len(encoder.calls), 3)

    def test_description_duties_exclude_requirements_and_title_fallback(self):
        with connect(self.db) as c:
            rows = {row['id']: row for row in c.execute('SELECT * FROM vacancies')}
        self.assertIn('Проводить интервью', vacancy_experience_text(rows['b']))
        self.assertNotIn('SQL и Python', vacancy_experience_text(rows['b']))
        self.assertEqual(vacancy_experience_text(rows['c']), 'Инженер')


if __name__ == '__main__':
    unittest.main()
