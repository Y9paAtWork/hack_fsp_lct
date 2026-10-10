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
