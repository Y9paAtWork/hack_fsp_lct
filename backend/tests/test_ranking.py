import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.db import connect, init_db
from backend.embedding_worker import DIMENSIONS, MODEL, REVISION
from backend.ranking import (activity_score, catalog, fsp_score,
                             populate_demo, rank_vacancy, record_verified_skill_test,
                             rebuild_skill_vector, record_visit,
                             store_vacancy_query_vector, vacancy_experience_text)


class RankingTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = Path(self.folder.name) / 'test.sqlite3'
        init_db(self.db)
        self.names, _ = catalog()
        with connect(self.db) as c:
            c.executemany('INSERT INTO technologies(name) VALUES(?)', [(x,) for x in self.names])
            for uid, spec in [('a', 'ML / AI'), ('b', 'ML / AI'), ('c', 'Frontend'), ('d', 'ML / AI')]:
                c.execute("INSERT INTO users(id,role,name,origin,created_at) VALUES(?,'candidate',?,'dataset','now')", (uid, uid))
                c.execute('''INSERT INTO resumes(id,user_id,specialization,position_name,raw_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?)''', ('r' + uid, uid, spec, spec, '{}', 'now', 'now'))
            c.execute('''INSERT INTO vacancies(id,vacancy_name,specialization,responsibilities,raw_json,created_at)
                VALUES('v','ML engineer','ML / AI','Build ML services','{}','now')''')
            c.execute("INSERT INTO vacancy_technologies VALUES('v','Python')")
            c.execute("INSERT INTO resume_technologies VALUES('ra','Python')")
            c.execute("INSERT INTO resume_technologies VALUES('rb','Python')")
            for uid, value in [('a', 1.0), ('b', 0.2), ('c', 1.0)]:
                item = 'item' + uid
                c.execute('''INSERT INTO work_items(id,resume_id,user_id,kind,ordinal,description,
                    source_field,source_text,split_method,needs_review,created_at)
                    VALUES(?,?,?,'work',0,'Built ML models','x','x','manual',0,'now')''',
                    (item, 'r' + uid, uid))
                vec = np.zeros(DIMENSIONS, dtype='<f4')
                vec[0] = value
                vec[1] = (1 - value**2)**0.5
                c.execute('''INSERT INTO item_vectors(item_id,user_id,model,revision,task,dimensions,
                    vector,chunk_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                    (item, uid, MODEL, REVISION, 'retrieval.passage', DIMENSIONS, vec.tobytes(), 1, 'now'))
            query = np.zeros(DIMENSIONS, dtype='<f4')
            query[0] = 1
            store_vacancy_query_vector(c, 'v', query)

    def tearDown(self):
        self.folder.cleanup()

    def test_vector_order_and_verified_result_preserved(self):
        first = populate_demo(self.db)
        self.assertEqual(first['dimensions'], 202)
        with connect(self.db) as c:
            record_verified_skill_test(c, 'a', 'Python', 100)
        populate_demo(self.db)
        with connect(self.db) as c:
            row = c.execute("SELECT vector FROM candidate_skill_vectors WHERE user_id='a'").fetchone()
            values = np.frombuffer(row[0], dtype='<f4')
            self.assertEqual(values.shape, (202,))
            self.assertEqual(values[self.names.index('Python')], 1.0)
            self.assertEqual(values[self.names.index('Scala')], 0.0)
            self.assertEqual(c.execute("SELECT source FROM skill_test_results WHERE user_id='a' AND technology='Python'").fetchone()[0], 'verified')

    def test_removed_technology_is_zero_and_visit_is_real(self):
        populate_demo(self.db)
        with connect(self.db) as c:
            c.execute("DELETE FROM resume_technologies WHERE resume_id='ra' AND technology='Python'")
            names, catalog_hash = catalog()
            rebuild_skill_vector(c, 'a', names, catalog_hash)
            record_visit(c, 'a')
            row = c.execute("SELECT vector FROM candidate_skill_vectors WHERE user_id='a'").fetchone()
            self.assertEqual(np.frombuffer(row[0], dtype='<f4')[names.index('Python')], 0.0)
            activity = c.execute("SELECT active_days_30,daily_tests_30,source FROM candidate_activity WHERE user_id='a'").fetchone()
            self.assertEqual(tuple(activity), (1, 0, 'server_events'))

    def test_four_signals_and_specialization_filter(self):
        populate_demo(self.db)
        with connect(self.db) as c:
            record_verified_skill_test(c, 'a', 'Python', 100)
            record_verified_skill_test(c, 'b', 'Python', 0)
            c.execute("UPDATE candidate_activity SET activity_score=1 WHERE user_id='a'")
            c.execute("UPDATE candidate_activity SET activity_score=0 WHERE user_id='b'")
            c.execute('''INSERT INTO candidate_fsp_achievements
              VALUES('ach-a','a','international','final',1,'verified','','now')''')
            c.execute('''INSERT INTO work_items(id,resume_id,user_id,kind,ordinal,description,
                source_field,source_text,split_method,needs_review,created_at)
                VALUES('weaker','ra','a','project',1,'Less related','x','x','manual',0,'now')''')
            weaker = np.zeros(DIMENSIONS, dtype='<f4')
            weaker[0] = 0.1
            weaker[1] = (1 - 0.1**2)**0.5
            c.execute('''INSERT INTO item_vectors(item_id,user_id,model,revision,task,dimensions,
                vector,chunk_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                ('weaker', 'a', MODEL, REVISION, 'retrieval.passage', DIMENSIONS, weaker.tobytes(), 1, 'now'))
            ranked = rank_vacancy(c, 'v')
        self.assertEqual(ranked[0]['resume_id'], 'ra')
        self.assertEqual({r['resume_id'] for r in ranked}, {'ra', 'rb', 'rd'})
        self.assertAlmostEqual(ranked[0]['components']['experience'], 1.0)
        self.assertAlmostEqual(ranked[0]['components']['skills'], 1.0)
        self.assertAlmostEqual(ranked[0]['score'], 1.0, places=4)
        self.assertEqual(ranked[0]['experience_blocks_found'], 2)
        self.assertEqual(ranked[0]['best_experience_item_id'], 'itema')
        no_experience = next(r for r in ranked if r['resume_id'] == 'rd')
        self.assertEqual(no_experience['components']['experience'], 0)
        self.assertIsNone(no_experience['best_experience_item_id'])

    def test_verified_filter_applies_before_top_limit(self):
        with connect(self.db) as c:
            c.execute('''INSERT INTO candidate_verified_grades VALUES(?,?,?,?,?,?)''',
                      ('b','ML / AI','Junior','test-v1','test-bank','now'))
            all_candidates = rank_vacancy(c,'v',limit=1)
            confirmed = rank_vacancy(c,'v',limit=1,verified_only=True)
        self.assertEqual(all_candidates[0]['resume_id'],'ra')
        self.assertEqual(confirmed[0]['resume_id'],'rb')
        self.assertEqual(confirmed[0]['verified_grade'],'Junior')

    def test_synthetic_values_have_narrow_range(self):
        populate_demo(self.db)
        with connect(self.db) as c:
            scores = [r[0] for r in c.execute("SELECT score FROM skill_test_results WHERE source='synthetic_demo'")]
            activities = [r[0] for r in c.execute("SELECT activity_score FROM candidate_activity WHERE source='synthetic_demo'")]
            ranked = rank_vacancy(c, 'v')
        self.assertTrue(all(48 <= x <= 52 for x in scores))
        self.assertTrue(all(0.48 <= x <= 0.52 for x in activities))
        self.assertTrue(all(0.48 <= r['components']['fsp'] <= 0.52 for r in ranked))

    def test_ranking_uses_technologies_extracted_from_description(self):
        populate_demo(self.db)
        with connect(self.db) as c:
            c.execute("UPDATE vacancies SET description='Нужен опыт с k8s и postgres.' WHERE id='v'")
            record_verified_skill_test(c, 'a', 'Python', 100)
            ranked = rank_vacancy(c, 'v')
        self.assertEqual(ranked[0]['technology_source'], 'description_heuristics')
        self.assertEqual(ranked[0]['vacancy_technologies'], ['Kubernetes', 'PostgreSQL'])
        self.assertEqual(ranked[0]['requested_technologies'], 2)
        self.assertEqual(ranked[0]['components']['skills'], 0.0)

    def test_component_bounds(self):
        self.assertEqual(activity_score(0, 0), 0)
        self.assertEqual(activity_score(30, 30), 1)
        self.assertEqual(fsp_score([]), 0)
        self.assertEqual(fsp_score([{'level': 'international', 'stage': 'final', 'place': 1}]), 1)

    def test_changed_vacancy_needs_new_query_vector(self):
        populate_demo(self.db)
        with connect(self.db) as c:
            c.execute("UPDATE vacancies SET responsibilities='Different duties' WHERE id='v'")
            with self.assertRaisesRegex(ValueError, 'missing or stale'):
                rank_vacancy(c, 'v')

    def test_vacancy_query_uses_duties_before_requirements(self):
        row = {'vacancy_name': 'Инженер ИБ', 'responsibilities':
               'О компании. Обязанности: Внедрять средства защиты, отслеживать инциденты и писать документацию. '
               'Требования: Знание Python, SQL и других технологий.'}
        text = vacancy_experience_text(row)
        self.assertIn('Внедрять средства защиты', text)
        self.assertNotIn('О компании', text)
        self.assertNotIn('Знание Python', text)


if __name__ == '__main__':
    unittest.main()
