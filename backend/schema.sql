PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
INSERT OR IGNORE INTO schema_version VALUES(1,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
CREATE TABLE IF NOT EXISTS import_runs(
 id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('resumes','vacancies')), row_count INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL CHECK(status IN ('running','complete','failed')), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users(
 id TEXT PRIMARY KEY, email TEXT UNIQUE COLLATE NOCASE, password_hash TEXT,
 role TEXT NOT NULL CHECK(role IN ('candidate','employer')), name TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('registration','dataset')), created_at TEXT NOT NULL,
 CHECK((origin='registration' AND email IS NOT NULL AND password_hash IS NOT NULL) OR origin='dataset')
);
CREATE TABLE IF NOT EXISTS sessions(
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires_at INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_profiles(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 fsp_id TEXT NOT NULL DEFAULT '', profile_json TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employer_profiles(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 company_name TEXT NOT NULL, about TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resumes(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
 import_id TEXT REFERENCES import_runs(id), source_row INTEGER,
 specialization TEXT, specializations_json TEXT NOT NULL DEFAULT '[]', position_name TEXT, typical_position TEXT,
 age REAL, location TEXT, experience_years REAL, salary_min REAL, salary_max REAL,
 schedule_type TEXT, business_trip TEXT, relocation TEXT, retraining_capability TEXT,
 skills_text TEXT, hard_skills TEXT, soft_skills TEXT, education_level TEXT,
 education_institutions TEXT, education_details TEXT, additional_education TEXT,
 languages TEXT, certificates TEXT, achievements TEXT, additional_information TEXT,
 ner_technologies_json TEXT NOT NULL DEFAULT '[]', all_technologies_json TEXT NOT NULL DEFAULT '[]',
 technology_count INTEGER, raw_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(import_id,source_row)
);
CREATE TABLE IF NOT EXISTS technologies(
 name TEXT PRIMARY KEY COLLATE NOCASE, categories_json TEXT NOT NULL DEFAULT '[]', test_required INTEGER NOT NULL DEFAULT 0 CHECK(test_required IN (0,1))
);
CREATE TABLE IF NOT EXISTS resume_technologies(
 resume_id TEXT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
 technology TEXT NOT NULL REFERENCES technologies(name), PRIMARY KEY(resume_id,technology)
);
CREATE TABLE IF NOT EXISTS work_items(
 id TEXT PRIMARY KEY, resume_id TEXT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 parent_item_id TEXT REFERENCES work_items(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('work','project','unclassified')),
 ordinal INTEGER NOT NULL, specialization TEXT, title TEXT, company TEXT,
 start_date TEXT, end_date TEXT, description TEXT NOT NULL, source_field TEXT NOT NULL, source_text TEXT NOT NULL,
 split_method TEXT NOT NULL, needs_review INTEGER NOT NULL CHECK(needs_review IN (0,1)), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_work_user ON work_items(user_id);
CREATE INDEX IF NOT EXISTS ix_work_resume ON work_items(resume_id);
CREATE TABLE IF NOT EXISTS candidate_grade_estimates(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 resume_id TEXT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
 grade TEXT NOT NULL CHECK(grade IN ('Junior','Middle','Senior','Unknown')),
 confidence TEXT NOT NULL CHECK(confidence IN ('high','medium','low')),
 declared_years REAL, dated_it_years REAL, effective_it_years REAL,
 it_work_blocks INTEGER NOT NULL, it_project_blocks INTEGER NOT NULL,
 evidence_json TEXT NOT NULL, heuristic_version TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_candidate_grade ON candidate_grade_estimates(grade,confidence);
CREATE TABLE IF NOT EXISTS candidate_grades(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 resume_id TEXT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
 grade TEXT NOT NULL CHECK(grade IN ('Junior','Middle','Senior')),
 grade_source TEXT NOT NULL CHECK(grade_source IN ('heuristic','review_file')),
 confidence TEXT CHECK(confidence IN ('high','medium','low')),
 reviewer_notes TEXT NOT NULL DEFAULT '',
 source_version TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_final_candidate_grade ON candidate_grades(grade,grade_source);
CREATE TABLE IF NOT EXISTS embedding_chunks(
 id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, content TEXT NOT NULL, content_sha256 TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready','failed')),
 error TEXT, updated_at TEXT NOT NULL, UNIQUE(item_id,ordinal)
);
CREATE INDEX IF NOT EXISTS ix_chunks_status ON embedding_chunks(status);
CREATE INDEX IF NOT EXISTS ix_chunks_hash ON embedding_chunks(content_sha256);
CREATE TABLE IF NOT EXISTS embeddings(
 content_sha256 TEXT NOT NULL, model TEXT NOT NULL, revision TEXT NOT NULL, task TEXT NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions>0), normalized INTEGER NOT NULL CHECK(normalized=1),
 vector BLOB NOT NULL, created_at TEXT NOT NULL,
 CHECK(length(vector)=dimensions*4), PRIMARY KEY(content_sha256,model,revision,task,dimensions)
);
CREATE TABLE IF NOT EXISTS item_vectors(
 item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 model TEXT NOT NULL, revision TEXT NOT NULL, task TEXT NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions>0), vector BLOB NOT NULL,
 aggregation TEXT NOT NULL DEFAULT 'normalized_mean', chunk_count INTEGER NOT NULL, created_at TEXT NOT NULL,
 CHECK(length(vector)=dimensions*4), PRIMARY KEY(item_id,model,revision,task,dimensions)
);
CREATE INDEX IF NOT EXISTS ix_item_vectors_user ON item_vectors(user_id);
CREATE TABLE IF NOT EXISTS vacancies(
 id TEXT PRIMARY KEY, employer_id TEXT REFERENCES users(id) ON DELETE CASCADE,
 import_id TEXT REFERENCES import_runs(id), source_row INTEGER,
 specialization TEXT, specializations_json TEXT NOT NULL DEFAULT '[]', vacancy_name TEXT NOT NULL,
 typical_position TEXT, professional_sphere TEXT, region TEXT, experience_requirements REAL,
 schedule_type TEXT, salary_min REAL, salary_max REAL, skills_text TEXT, hard_skills TEXT, soft_skills TEXT,
 position_requirements TEXT, responsibilities TEXT, additional_requirements TEXT, description TEXT,
 ner_technologies_json TEXT NOT NULL DEFAULT '[]', all_technologies_json TEXT NOT NULL DEFAULT '[]',
 technology_count INTEGER, raw_json TEXT NOT NULL, created_at TEXT NOT NULL,
 visibility TEXT NOT NULL DEFAULT 'public' CHECK(visibility IN ('public','private')),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
 closed_at TEXT,
 UNIQUE(import_id,source_row)
);
CREATE TABLE IF NOT EXISTS vacancy_technologies(
 vacancy_id TEXT NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
 technology TEXT NOT NULL REFERENCES technologies(name), PRIMARY KEY(vacancy_id,technology)
);
CREATE TABLE IF NOT EXISTS embedding_runs(
 id TEXT PRIMARY KEY, model TEXT NOT NULL, revision TEXT NOT NULL, task TEXT NOT NULL, dimensions INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','complete','failed')), processed INTEGER NOT NULL DEFAULT 0,
 error TEXT, started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS skill_test_results(
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 technology TEXT NOT NULL REFERENCES technologies(name),
 score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
 source TEXT NOT NULL CHECK(source IN ('synthetic_demo','verified')),
 tested_at TEXT,
 PRIMARY KEY(user_id,technology)
);
CREATE TABLE IF NOT EXISTS test_questions(
 id TEXT PRIMARY KEY, technology TEXT NOT NULL, specializations_json TEXT NOT NULL DEFAULT '[]',
 test_required INTEGER NOT NULL DEFAULT 0, grade TEXT NOT NULL, question_type TEXT NOT NULL,
 question_text TEXT NOT NULL, options_json TEXT NOT NULL DEFAULT '[]',
 correct_answers_json TEXT NOT NULL DEFAULT '[]', explanation TEXT NOT NULL DEFAULT '',
 points REAL NOT NULL DEFAULT 1, origin_type TEXT NOT NULL DEFAULT '',
 original_question_url TEXT NOT NULL DEFAULT '', reference_url TEXT NOT NULL DEFAULT '',
 review_status TEXT NOT NULL DEFAULT '', concept_id TEXT NOT NULL DEFAULT '',
 derivation TEXT NOT NULL DEFAULT '', code_snippet TEXT NOT NULL DEFAULT '',
 reference_code TEXT NOT NULL DEFAULT '', tests_json TEXT NOT NULL DEFAULT '[]',
 rubric_json TEXT NOT NULL DEFAULT '[]', source_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_test_questions_tech_grade ON test_questions(technology,grade);
CREATE TABLE IF NOT EXISTS candidate_skill_vectors(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 catalog_sha256 TEXT NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions>0),
 vector BLOB NOT NULL,
 source TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 CHECK(length(vector)=dimensions*4)
);
CREATE TABLE IF NOT EXISTS candidate_activity(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 active_days_30 INTEGER NOT NULL CHECK(active_days_30 BETWEEN 0 AND 30),
 daily_tests_30 INTEGER NOT NULL CHECK(daily_tests_30>=0),
 activity_score REAL NOT NULL CHECK(activity_score BETWEEN 0 AND 1),
 source TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_activity_days(
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 day_utc TEXT NOT NULL, PRIMARY KEY(user_id,day_utc)
);
CREATE TABLE IF NOT EXISTS candidate_activity_days_msk(
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 day_msk TEXT NOT NULL, PRIMARY KEY(user_id,day_msk)
);
CREATE TABLE IF NOT EXISTS candidate_daily_tests(
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
 completed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_daily_tests_user_time ON candidate_daily_tests(user_id,completed_at);
CREATE TABLE IF NOT EXISTS candidate_fsp_achievements(
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 level TEXT NOT NULL CHECK(level IN ('regional','national','international')),
 stage TEXT NOT NULL CHECK(stage IN ('qualifier','final')),
 place INTEGER CHECK(place BETWEEN 1 AND 3),
 source TEXT NOT NULL,
 event_name TEXT NOT NULL DEFAULT '',
 achieved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_fsp_achievements_user ON candidate_fsp_achievements(user_id);
CREATE TABLE IF NOT EXISTS vacancy_query_vectors(
 vacancy_id TEXT PRIMARY KEY REFERENCES vacancies(id) ON DELETE CASCADE,
 model TEXT NOT NULL, revision TEXT NOT NULL, task TEXT NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions=1024),
 vector BLOB NOT NULL CHECK(length(vector)=dimensions*4),
 text_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessment_attempts(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 technology TEXT NOT NULL REFERENCES technologies(name), grade TEXT NOT NULL CHECK(grade IN ('Junior','Middle','Senior')),
 question_ids_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('started','completed')),
 score INTEGER CHECK(score BETWEEN 0 AND 100), started_at TEXT NOT NULL, completed_at TEXT,
 source_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assessment_user_tech ON assessment_attempts(user_id,technology,grade,completed_at);
CREATE TABLE IF NOT EXISTS assessment_answers(
 attempt_id TEXT NOT NULL REFERENCES assessment_attempts(id) ON DELETE CASCADE,
 question_id TEXT NOT NULL, answer_json TEXT NOT NULL, score REAL NOT NULL CHECK(score BETWEEN 0 AND 1),
 grader_version TEXT NOT NULL, PRIMARY KEY(attempt_id,question_id)
);
CREATE TABLE IF NOT EXISTS technology_level_results(
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 technology TEXT NOT NULL REFERENCES technologies(name), grade TEXT NOT NULL CHECK(grade IN ('Junior','Middle','Senior')),
 best_score INTEGER NOT NULL CHECK(best_score BETWEEN 0 AND 100), passed INTEGER NOT NULL CHECK(passed IN (0,1)),
 attempt_id TEXT NOT NULL REFERENCES assessment_attempts(id), tested_at TEXT NOT NULL,
 source_version TEXT NOT NULL, PRIMARY KEY(user_id,technology,grade)
);
CREATE TABLE IF NOT EXISTS candidate_verified_grades(
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, specialization TEXT NOT NULL,
 grade TEXT NOT NULL CHECK(grade IN ('Junior','Middle','Senior')),
 rule_version TEXT NOT NULL, source_version TEXT NOT NULL, confirmed_at TEXT NOT NULL,
 PRIMARY KEY(user_id,specialization)
);
CREATE TABLE IF NOT EXISTS daily_questions(
 id INTEGER PRIMARY KEY, grade TEXT NOT NULL, topic TEXT NOT NULL, question_type TEXT NOT NULL,
 question_text TEXT NOT NULL, options_json TEXT NOT NULL, correct_answer_json TEXT NOT NULL,
 points REAL NOT NULL, explanation TEXT NOT NULL, source_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_sessions(
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, day_msk TEXT NOT NULL,
 specialization TEXT NOT NULL, grade TEXT NOT NULL, question_ids_json TEXT NOT NULL,
 score INTEGER CHECK(score BETWEEN 0 AND 100), answers_json TEXT, completed_at TEXT,
 PRIMARY KEY(user_id,day_msk)
);
CREATE TABLE IF NOT EXISTS candidate_initial_grades(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 grade TEXT NOT NULL CHECK(grade IN ('Junior','Middle','Senior')),
 declared_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_test_grades(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 current_grade TEXT NOT NULL CHECK(current_grade IN ('Junior','Middle','Senior')),
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_specialization_grades(
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 specialization TEXT NOT NULL,
 declared_grade TEXT NOT NULL CHECK(declared_grade IN ('Junior','Middle','Senior')),
 current_grade TEXT NOT NULL CHECK(current_grade IN ('Junior','Middle','Senior')),
 declared_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(user_id,specialization)
);
CREATE TABLE IF NOT EXISTS hiring_interactions(
 id TEXT PRIMARY KEY,
 vacancy_id TEXT NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
 candidate_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 employer_id TEXT REFERENCES users(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('application','invitation')),
 status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(vacancy_id,candidate_id,kind)
);
CREATE INDEX IF NOT EXISTS ix_hiring_candidate ON hiring_interactions(candidate_id,updated_at);
CREATE INDEX IF NOT EXISTS ix_hiring_employer ON hiring_interactions(employer_id,updated_at);
CREATE TABLE IF NOT EXISTS hiring_messages(
 id TEXT PRIMARY KEY,
 interaction_id TEXT NOT NULL REFERENCES hiring_interactions(id) ON DELETE CASCADE,
 sender_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 5000),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hiring_messages ON hiring_messages(interaction_id,created_at);
