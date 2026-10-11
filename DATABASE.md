# Структура БД FSP Career

SQLite 3, foreign_keys=ON, WAL. Данные приложения находятся в data/fsp.sqlite3. SQL-источник: backend/schema.sql. Все JSON-колонки имеют тип TEXT с сериализованным JSON; даты — TEXT, ISO UTC для служебных отметок, исходный формат для дат опыта. Отсутствующие числовые значения — NULL.

Дополнение для четырёхсигнального подбора: `skill_test_results` хранит балл 0–100 и источник по каждой технологии кандидата; `candidate_skill_vectors` — массив `float32` из 202 значений и SHA-256 порядка каталога; `candidate_activity` — сводку за 30 дней, а `candidate_activity_days` и `candidate_daily_tests` — реальные события; `candidate_fsp_achievements` — уровень, этап, место и источник каждого достижения; `vacancy_query_vectors` — Jina-вектор обязанностей вакансии. Формулы и порядок запуска описаны в [RANKING.md](RANKING.md). Демо-значения помечены `synthetic_demo`.

`candidate_grade_estimates` хранит промежуточный эвристический результат: `Junior`, `Middle`, `Senior` или `Unknown`, уверенность и использованные признаки. `candidate_grades` хранит **итоговый грейд каждого кандидата**: только `Junior`, `Middle` или `Senior`, с источником `heuristic` либо `review_file`. `Intern` объединён с `Junior`, `Lead` — с `Senior`. Для пересчёта: `python3 -m backend.estimate_grades`, затем `python3 -m backend.apply_reviewed_grades`. Выгрузка и статистика находятся в `out/grade_estimation/candidate_grades.csv` и `final_grade_statistics.md`; промежуточный файл — `heuristic_candidate_grades.csv`. Грейд пока не участвует в ранжировании.

Вакансии из CSV не содержат ID работодателя/названия компании: employer_id остаётся NULL. У резюме нет почты и пароля: users.origin=dataset, email/password_hash=NULL. Регистрация создаёт новый UUID-аккаунт origin=registration и не присваивает ему случайную строку из датасета.

## schema_version

Версии SQL-схемы.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| version | INTEGER | нет | PK, позиция 1 |
| applied_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
```

## import_runs

История импорта CSV, контроль повторного импорта по SHA-256.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| id | TEXT | нет | PK, позиция 1 |
| filename | TEXT | нет | — |
| sha256 | TEXT | нет | — |
| kind | TEXT | нет | — |
| row_count | INTEGER | нет | default 0 |
| status | TEXT | нет | — |
| created_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE import_runs(
 id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('resumes','vacancies')), row_count INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL CHECK(status IN ('running','complete','failed')), created_at TEXT NOT NULL
);
```

## users

Аккаунты кандидатов/работодателей и внутренние ID датасета.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| id | TEXT | нет | PK, позиция 1 |
| email | TEXT | да | — |
| password_hash | TEXT | да | — |
| role | TEXT | нет | — |
| name | TEXT | нет | — |
| origin | TEXT | нет | — |
| created_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE users(
 id TEXT PRIMARY KEY, email TEXT UNIQUE COLLATE NOCASE, password_hash TEXT,
 role TEXT NOT NULL CHECK(role IN ('candidate','employer')), name TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('registration','dataset')), created_at TEXT NOT NULL,
 CHECK((origin='registration' AND email IS NOT NULL AND password_hash IS NOT NULL) OR origin='dataset')
);
```

## sessions

Сессии входа; хранится только хеш токена.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| token_hash | TEXT | нет | PK, позиция 1 |
| user_id | TEXT | нет | FK → users.id |
| expires_at | INTEGER | нет | — |
| created_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE sessions(
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires_at INTEGER NOT NULL, created_at TEXT NOT NULL
);
```

## candidate_profiles

Анкета кандидата и номер её редакции.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| user_id | TEXT | нет | PK, позиция 1; FK → users.id |
| fsp_id | TEXT | нет | default '' |
| profile_json | TEXT | нет | — |
| revision | INTEGER | нет | default 0 |
| updated_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE candidate_profiles(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 fsp_id TEXT NOT NULL DEFAULT '', profile_json TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
```

## employer_profiles

Профиль компании зарегистрированного работодателя.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| user_id | TEXT | нет | PK, позиция 1; FK → users.id |
| company_name | TEXT | нет | — |
| about | TEXT | нет | default '' |
| website | TEXT | нет | default '' |
| updated_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE employer_profiles(
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 company_name TEXT NOT NULL, about TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
```

## resumes

Поля резюме и исходная строка CSV без потерь.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| id | TEXT | нет | PK, позиция 1 |
| user_id | TEXT | нет | FK → users.id |
| import_id | TEXT | да | FK → import_runs.id |
| source_row | INTEGER | да | — |
| specialization | TEXT | да | — |
| specializations_json | TEXT | нет | default '[]' |
| position_name | TEXT | да | — |
| typical_position | TEXT | да | — |
| age | REAL | да | — |
| location | TEXT | да | — |
| experience_years | REAL | да | — |
| salary_min | REAL | да | — |
| salary_max | REAL | да | — |
| schedule_type | TEXT | да | — |
| business_trip | TEXT | да | — |
| relocation | TEXT | да | — |
| retraining_capability | TEXT | да | — |
| skills_text | TEXT | да | — |
| hard_skills | TEXT | да | — |
| soft_skills | TEXT | да | — |
| education_level | TEXT | да | — |
| education_institutions | TEXT | да | — |
| education_details | TEXT | да | — |
| additional_education | TEXT | да | — |
| languages | TEXT | да | — |
| certificates | TEXT | да | — |
| achievements | TEXT | да | — |
| additional_information | TEXT | да | — |
| ner_technologies_json | TEXT | нет | default '[]' |
| all_technologies_json | TEXT | нет | default '[]' |
| technology_count | INTEGER | да | — |
| raw_json | TEXT | нет | — |
| created_at | TEXT | нет | — |
| updated_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE resumes(
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
```

## technologies

Справочник технологий из CSV и дополнения из датасета.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| name | TEXT | нет | PK, позиция 1 |
| categories_json | TEXT | нет | default '[]' |
| test_required | INTEGER | нет | default 0 |

Ограничения и индексы:

```sql
CREATE TABLE technologies(
 name TEXT PRIMARY KEY COLLATE NOCASE, categories_json TEXT NOT NULL DEFAULT '[]', test_required INTEGER NOT NULL DEFAULT 0 CHECK(test_required IN (0,1))
);
```

## resume_technologies

Связь резюме и технологий многие-ко-многим.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| resume_id | TEXT | нет | PK, позиция 1; FK → resumes.id |
| technology | TEXT | нет | PK, позиция 2; FK → technologies.name |

Ограничения и индексы:

```sql
CREATE TABLE resume_technologies(
 resume_id TEXT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
 technology TEXT NOT NULL REFERENCES technologies(name), PRIMARY KEY(resume_id,technology)
);
```

## work_items

Отдельные работы, проекты и неклассифицированные фрагменты.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| id | TEXT | нет | PK, позиция 1 |
| resume_id | TEXT | нет | FK → resumes.id |
| user_id | TEXT | нет | FK → users.id |
| parent_item_id | TEXT | да | FK → work_items.id |
| kind | TEXT | нет | — |
| ordinal | INTEGER | нет | — |
| specialization | TEXT | да | — |
| title | TEXT | да | — |
| company | TEXT | да | — |
| start_date | TEXT | да | — |
| end_date | TEXT | да | — |
| description | TEXT | нет | — |
| source_field | TEXT | нет | — |
| source_text | TEXT | нет | — |
| split_method | TEXT | нет | — |
| needs_review | INTEGER | нет | — |
| created_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE work_items(
 id TEXT PRIMARY KEY, resume_id TEXT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 parent_item_id TEXT REFERENCES work_items(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('work','project','unclassified')),
 ordinal INTEGER NOT NULL, specialization TEXT, title TEXT, company TEXT,
 start_date TEXT, end_date TEXT, description TEXT NOT NULL, source_field TEXT NOT NULL, source_text TEXT NOT NULL,
 split_method TEXT NOT NULL, needs_review INTEGER NOT NULL CHECK(needs_review IN (0,1)), created_at TEXT NOT NULL
);
```

```sql
CREATE INDEX ix_work_user ON work_items(user_id);
```

```sql
CREATE INDEX ix_work_resume ON work_items(resume_id);
```

## embedding_chunks

Текстовые части работ/проектов, очередь векторизации.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| id | TEXT | нет | PK, позиция 1 |
| item_id | TEXT | нет | FK → work_items.id |
| ordinal | INTEGER | нет | — |
| content | TEXT | нет | — |
| content_sha256 | TEXT | нет | — |
| status | TEXT | нет | default 'pending' |
| error | TEXT | да | — |
| updated_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE embedding_chunks(
 id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, content TEXT NOT NULL, content_sha256 TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready','failed')),
 error TEXT, updated_at TEXT NOT NULL, UNIQUE(item_id,ordinal)
);
```

```sql
CREATE INDEX ix_chunks_status ON embedding_chunks(status);
```

```sql
CREATE INDEX ix_chunks_hash ON embedding_chunks(content_sha256);
```

## embeddings

Кэш настоящих Jina-векторов отдельных частей.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| content_sha256 | TEXT | нет | PK, позиция 1 |
| model | TEXT | нет | PK, позиция 2 |
| revision | TEXT | нет | PK, позиция 3 |
| task | TEXT | нет | PK, позиция 4 |
| dimensions | INTEGER | нет | PK, позиция 5 |
| normalized | INTEGER | нет | — |
| vector | BLOB | нет | — |
| created_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE embeddings(
 content_sha256 TEXT NOT NULL, model TEXT NOT NULL, revision TEXT NOT NULL, task TEXT NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions>0), normalized INTEGER NOT NULL CHECK(normalized=1),
 vector BLOB NOT NULL, created_at TEXT NOT NULL,
 CHECK(length(vector)=dimensions*4), PRIMARY KEY(content_sha256,model,revision,task,dimensions)
);
```

## item_vectors

Итоговый вектор каждой работы/проекта с user_id.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| item_id | TEXT | нет | PK, позиция 1; FK → work_items.id |
| user_id | TEXT | нет | FK → users.id |
| model | TEXT | нет | PK, позиция 2 |
| revision | TEXT | нет | PK, позиция 3 |
| task | TEXT | нет | PK, позиция 4 |
| dimensions | INTEGER | нет | PK, позиция 5 |
| vector | BLOB | нет | — |
| aggregation | TEXT | нет | default 'normalized_mean' |
| chunk_count | INTEGER | нет | — |
| created_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE item_vectors(
 item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 model TEXT NOT NULL, revision TEXT NOT NULL, task TEXT NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions>0), vector BLOB NOT NULL,
 aggregation TEXT NOT NULL DEFAULT 'normalized_mean', chunk_count INTEGER NOT NULL, created_at TEXT NOT NULL,
 CHECK(length(vector)=dimensions*4), PRIMARY KEY(item_id,model,revision,task,dimensions)
);
```

```sql
CREATE INDEX ix_item_vectors_user ON item_vectors(user_id);
```

## vacancies

Поля вакансии и исходная CSV-строка; employer_id пуст у датасета.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| id | TEXT | нет | PK, позиция 1 |
| employer_id | TEXT | да | FK → users.id |
| import_id | TEXT | да | FK → import_runs.id |
| source_row | INTEGER | да | — |
| specialization | TEXT | да | — |
| specializations_json | TEXT | нет | default '[]' |
| vacancy_name | TEXT | нет | — |
| typical_position | TEXT | да | — |
| professional_sphere | TEXT | да | — |
| region | TEXT | да | — |
| experience_requirements | REAL | да | — |
| schedule_type | TEXT | да | — |
| salary_min | REAL | да | — |
| salary_max | REAL | да | — |
| skills_text | TEXT | да | — |
| hard_skills | TEXT | да | — |
| soft_skills | TEXT | да | — |
| position_requirements | TEXT | да | — |
| responsibilities | TEXT | да | — |
| additional_requirements | TEXT | да | — |
| description | TEXT | да | — |
| ner_technologies_json | TEXT | нет | default '[]' |
| all_technologies_json | TEXT | нет | default '[]' |
| technology_count | INTEGER | да | — |
| raw_json | TEXT | нет | — |
| created_at | TEXT | нет | — |

Ограничения и индексы:

```sql
CREATE TABLE vacancies(
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
```

## vacancy_technologies

Связь вакансий и технологий многие-ко-многим.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| vacancy_id | TEXT | нет | PK, позиция 1; FK → vacancies.id |
| technology | TEXT | нет | PK, позиция 2; FK → technologies.name |

Ограничения и индексы:

```sql
CREATE TABLE vacancy_technologies(
 vacancy_id TEXT NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
 technology TEXT NOT NULL REFERENCES technologies(name), PRIMARY KEY(vacancy_id,technology)
);
```

## embedding_runs

Журнал прогонов модели и ошибки запуска.

| Поле | Тип | NULL допустим | Ключ/значение по умолчанию |
|---|---|---|---|
| id | TEXT | нет | PK, позиция 1 |
| model | TEXT | нет | — |
| revision | TEXT | нет | — |
| task | TEXT | нет | — |
| dimensions | INTEGER | нет | — |
| status | TEXT | нет | — |
| processed | INTEGER | нет | default 0 |
| error | TEXT | да | — |
| started_at | TEXT | нет | — |
| finished_at | TEXT | да | — |

Ограничения и индексы:

```sql
CREATE TABLE embedding_runs(
 id TEXT PRIMARY KEY, model TEXT NOT NULL, revision TEXT NOT NULL, task TEXT NOT NULL, dimensions INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','complete','failed')), processed INTEGER NOT NULL DEFAULT 0,
 error TEXT, started_at TEXT NOT NULL, finished_at TEXT
);
```

## Вектор пользователя

Связи: users.id → resumes.user_id → work_items.user_id; work_items.id → embedding_chunks.item_id и item_vectors.item_id. Для одной работы/проекта возможны несколько текстовых частей. embeddings кэшируется по SHA-256 текста и конфигурации модели. Итог item_vectors создаётся только когда обработаны все части с одинаковой моделью/ревизией/task/dimensions. Вектор хранится как 1024 float32 little-endian (4096 байт), нормализован по L2.

```sql
SELECT item_id, user_id, model, revision, task, dimensions, vector
FROM item_vectors
WHERE user_id = ?;
```

Нормализованное среднее частей является представлением всей работы/проекта. Исходные векторы частей сохраняются отдельно. Из-за общей очереди обработка не блокирует регистрацию. Векторизация всего датасета не завершена: точное состояние экспортированного снимка — в data/checkpoint.json.

parent_item_id предусмотрен как nullable FK. Импорт не угадывает родительскую связь проекта с компанией; он сохраняет источник в source_field/source_text.

## Серверные результаты тестирования

`test_questions` содержит 1 730 технологических заданий и ключи ответов. `daily_questions` содержит 106 общих заданий и ключи; эти таблицы доступны только backend. `assessment_attempts` фиксирует технологию, уровень, список вопросов, время и итог каждой попытки. `assessment_answers` хранит ответы и оценки отдельных вопросов с версией оценщика. `technology_level_results` хранит лучший результат по паре технология/уровень; `skill_test_results` и `candidate_skill_vectors` получают результат последней завершённой попытки. Редактируемый `candidate_profiles.profile_json` не является источником этих результатов.

`candidate_verified_grades` хранит подтверждённый грейд отдельно по специализации, вместе с версией матрицы и банка вопросов. Эвристические `candidate_grade_estimates` и `candidate_grades` не считаются подтверждением. `daily_sessions` фиксирует набор из трёх вопросов, ответы и оценку на московскую дату. `candidate_activity_days_msk` хранит даты посещений, а `candidate_activity` — 30-дневный итог посещений и ежедневных тестов. Подробная схема и ограничения находятся в `backend/schema.sql`.

## Отклики, приглашения и статусы вакансий

`vacancies.visibility` (`public`/`private`) и `vacancies.status` (`open`/`closed`) управляют публикацией и архивом. Миграция присваивает всем существующим вакансиям `public` и `open`; `closed_at` сохраняет дату закрытия. `candidate_initial_grades` хранит грейд, указанный при регистрации, а `candidate_test_grades` — текущий уровень доступных тестов. `hiring_interactions` хранит отклики и приглашения со статусами `pending`, `approved`, `rejected`, а `hiring_messages` — сообщения участников. Сообщения доступны только после одобрения. Резервная копия БД перед миграцией создаётся вне Git.

`candidate_specialization_grades` хранит заявленный и текущий грейд отдельно для каждой специализации кандидата. Версия миграции 3 переносит значения существующих аккаунтов из общих таблиц `candidate_initial_grades` и `candidate_test_grades`; они остаются только для совместимости со старыми записями. Заявленный грейд можно уточнить до подтверждения или повышения, а повышение текущего уровня происходит по каждой специализации отдельно.
