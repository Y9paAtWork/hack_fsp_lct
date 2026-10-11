import tempfile
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidTag

from backend.candidate_access import anonymize_matches, resolve_match
from backend.db import connect, init_db
from backend.encrypted_database import decrypt, encrypt, generate_key


class CandidateSecurityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = Path(self.folder.name) / 'security.sqlite3'
        init_db(self.db)
        with connect(self.db) as c:
            c.execute("INSERT INTO users VALUES('e','e@example.org','hash','employer','Company','registration','now')")
            c.execute("INSERT INTO employer_profiles(user_id,company_name,updated_at,access_status) VALUES('e','Company','now','approved')")
            c.execute("INSERT INTO users(id,role,name,origin,created_at) VALUES('c','candidate','Secret Name','dataset','now')")
            c.execute("INSERT INTO resumes(id,user_id,specialization,position_name,raw_json,created_at,updated_at) VALUES('r','c','Backend','Developer','{}','now','now')")
            c.execute("INSERT INTO vacancies(id,employer_id,vacancy_name,raw_json,created_at) VALUES('v','e','Backend','{}','now')")

    def tearDown(self):
        self.folder.cleanup()

    def test_ranked_candidate_is_pseudonymous_and_token_is_scoped(self):
        private = {'resume_id':'r','user_id':'c','candidate_name':'Secret Name',
                   'position_name':'Developer','specialization':'Backend','candidate_stack':['Python'],
                   'candidate_grade':'Junior','verified_grade':None,'contactable':False,
                   'expected_salary_min':200000,'salary_mismatch':True,'score':.8,
                   'components':{'experience':.9,'skills':.7,'activity':0,'fsp':0},
                   'experience_blocks_found':2,'requested_technologies':1,
                   'vacancy_technologies':['Python']}
        with connect(self.db) as c:
            public = anonymize_matches(c,'e','v',[private])[0]
            self.assertNotIn('resume_id',public)
            self.assertNotIn('user_id',public)
            self.assertNotIn('candidate_name',public)
            self.assertNotIn('expected_salary_min',public)
            self.assertEqual(resolve_match(c,'e','v',public['match_id'])['candidate_id'],'c')
            with self.assertRaises(Exception):
                resolve_match(c,'other','v',public['match_id'])

    def test_streaming_encryption_round_trip_and_wrong_key(self):
        source = Path(self.folder.name) / 'source.sqlite3'
        encrypted = Path(self.folder.name) / 'source.fspenc'
        restored = Path(self.folder.name) / 'restored.sqlite3'
        source.write_bytes(self.db.read_bytes())
        key = generate_key()
        encrypt(source, encrypted, key)
        self.assertNotIn(b'Secret Name', encrypted.read_bytes())
        decrypt(encrypted, restored, key, check_sqlite=True)
        self.assertEqual(source.read_bytes(), restored.read_bytes())
        with self.assertRaises(InvalidTag):
            decrypt(encrypted, Path(self.folder.name) / 'wrong.sqlite3', generate_key())


if __name__ == '__main__':
    unittest.main()


