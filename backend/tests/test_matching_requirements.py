import json
import tempfile
import unittest
from pathlib import Path

from backend.db import connect, init_db
from backend.import_data import import_file
from backend.matching_requirements import infer_vacancy_grade


class MatchingRequirementsTests(unittest.TestCase):
    def test_grade_uses_title_experience_requirements_and_stack(self):
        vacancy = dict(vacancy_name='Senior Backend Engineer', experience_requirements=1,
                       position_requirements='', additional_requirements='',
                       responsibilities='', description='', technology_count=2)
        self.assertEqual(infer_vacancy_grade(vacancy)[0], 'Senior')
        vacancy['vacancy_name'] = 'Backend Engineer'
        vacancy['experience_requirements'] = 3
        self.assertEqual(infer_vacancy_grade(vacancy)[0], 'Middle')
        vacancy['experience_requirements'] = None
        vacancy['position_requirements'] = 'Нужно наставничество и проектирование архитектуры'
        self.assertEqual(infer_vacancy_grade(vacancy)[0], 'Senior')
        vacancy['position_requirements'] = ''
        vacancy['technology_count'] = 9
        self.assertEqual(infer_vacancy_grade(vacancy)[0], 'Middle')

    def test_backfill_existing_rows_keeps_mandatory_stack_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'database.sqlite3'
            init_db(path)
            with connect(path) as c:
                c.execute("INSERT INTO users(id,role,name,origin,created_at) VALUES('u','candidate','A','dataset','now')")
                c.execute("INSERT INTO resumes(id,user_id,specialization,position_name,raw_json,created_at,updated_at) VALUES('r','u','Backend','Developer','{}','now','now')")
                c.execute("INSERT INTO candidate_grades(user_id,resume_id,grade,grade_source,source_version,updated_at) VALUES('u','r','Senior','heuristic','test','now')")
                c.execute("INSERT INTO vacancies(id,vacancy_name,specialization,experience_requirements,raw_json,created_at) VALUES('v','Backend Engineer','Backend',5,'{}','now')")
                c.execute('DELETE FROM schema_version WHERE version=4')
            init_db(path)
            with connect(path) as c:
                self.assertEqual(c.execute("SELECT expected_salary_min FROM resumes WHERE id='r'").fetchone()[0], 200000)
                row = c.execute("SELECT grade,grade_source,required_technologies_json FROM vacancies WHERE id='v'").fetchone()
                self.assertEqual((row['grade'],row['grade_source'],json.loads(row['required_technologies_json'])),
                                 ('Senior','experience_heuristic_v1',[]))
                self.assertEqual(c.execute('SELECT count(*) FROM schema_version WHERE version=4').fetchone()[0], 1)

    def test_new_imported_vacancy_is_graded_without_mandatory_skills(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'database.sqlite3'
            source=Path(folder)/'vacancies.csv'
            source.write_text('vacancy_name,specialization,experience_requirements,all_technologies\n'
                              'Backend Engineer,Backend,6,"[""Python""]"\n',encoding='utf-8')
            import_file(source,'vacancies',path)
            with connect(path) as c:
                row=c.execute('SELECT grade,required_technologies_json FROM vacancies').fetchone()
                self.assertEqual((row['grade'],json.loads(row['required_technologies_json'])),('Senior',[]))


if __name__ == '__main__':
    unittest.main()
