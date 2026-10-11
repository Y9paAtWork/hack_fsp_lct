"""Build a small public demo database from the local full database.

Only catalog skills, grades and demo scores are copied from resumes. Published
experience text is newly written from topic categories and re-embedded locally;
names, contacts, source resumes and employer accounts never enter the seed.
"""

import argparse
import html
import json
import re
import sqlite3
from pathlib import Path

from .assessments import matrix, seed_daily, seed_technology_questions
from .db import ROOT, connect, init_db
from .embedding_worker import DIMENSIONS, MODEL, REVISION, LocalJina, normalize_vectors
from .import_data import seed_catalog
from .matching_requirements import demo_salary_min, usable_salary_range
from .ranking import catalog, store_vacancy_query_vector, vacancy_experience_text

STAMP = '2026-10-11T00:00:00+00:00'
GRADES = ('Junior', 'Middle', 'Senior')
FOCUS_VACANCIES = {
    ('ML / AI', 'Middle'): 'd55efdb0256855959a98fca2e02573a8',
    ('ML / AI', 'Junior'): '1c43c20f6b965c9cb608f3ffb1e284c6',
}
TOPICS = {
    'ML / AI': ('разработка NLP-моделей для обработки документов', 'построение моделей компьютерного зрения',
                'разработка речевых ML-сервисов', 'создание рекомендательных моделей', 'подготовка ML-моделей к внедрению'),
    'Data Science': ('проверка гипотез и построение моделей прогнозирования', 'анализ данных и обучение ML-моделей'),
    'Data Engineering': ('разработка ETL-пайплайнов и обработка потоков данных', 'проектирование хранилищ данных'),
    'Data Analytics': ('исследование продуктовых метрик и подготовка аналитических отчётов',),
    'Backend': ('разработка API и серверной бизнес-логики', 'проектирование сервисов и оптимизация запросов'),
    'Frontend': ('разработка интерфейсов и клиентской логики',),
    'Fullstack': ('разработка пользовательского интерфейса и серверного API',),
    'System Analysis': ('описание требований и проектирование интеграций',),
    'DevOps / SRE': ('автоматизация развёртывания и мониторинг сервисов',),
    'QA': ('проектирование проверок и автоматизация тестирования',),
    'Mobile': ('разработка мобильного приложения и интеграция с API',),
    'Cybersecurity': ('анализ угроз и защита инфраструктуры',),
    'System / Network': ('администрирование сетей и диагностика инфраструктуры',),
    'Database': ('проектирование схем данных и оптимизация запросов',),
    'Software Engineering': ('разработка программных компонентов и их тестирование',),
    'Other IT': ('поддержка и развитие внутренних ИТ-сервисов',),
}
TITLE_HINTS = {
    'ML / AI': ('ml', 'machine learning', 'data scientist', 'nlp', 'computer vision', 'машинному обучению'),
    'Data Analytics': ('аналитик', 'analyst', 'analytics'),
}


def clean_public(text, limit=2600):
    text = html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))
    text = re.sub(r'https?://\S+|www\.\S+', ' ', text, flags=re.I)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', ' ', text)
    text = re.sub(r'(?<!\w)(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\w)', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def candidate_target(spec, grade):
    if (spec, grade) == ('ML / AI', 'Middle'):
        return 46
    if (spec, grade) == ('ML / AI', 'Junior'):
        return 12
    if grade == 'Middle' and spec in ('Backend', 'Frontend', 'System Analysis', 'QA'):
        return 8
    return 2


def candidate_rows(source, spec, grade, limit):
    return source.execute('''SELECT r.*,g.grade,g.grade_source,g.confidence,g.source_version
      FROM resumes r JOIN users u ON u.id=r.user_id
      JOIN candidate_grades g ON g.user_id=r.user_id
      WHERE u.origin='dataset' AND r.specialization=? AND g.grade=?
      AND EXISTS (SELECT 1 FROM work_items w JOIN item_vectors iv ON iv.item_id=w.id
        WHERE w.user_id=r.user_id AND w.specialization=?)
      ORDER BY coalesce(r.technology_count,0) DESC,coalesce(r.experience_years,0) DESC,r.id
      LIMIT ?''', (spec, grade, spec, limit)).fetchall()


def topic_for(spec, source_text, ordinal):
    options = TOPICS[spec]
    if spec == 'ML / AI':
        lower = source_text.lower()
        for terms, index in [(('nlp', 'текст', 'документ', 'bert', 'rag'), 0),
                             (('computer vision', 'изображен', 'детекц', 'opencv'), 1),
                             (('speech', 'реч', 'аудио', 'asr'), 2),
                             (('рекомендац', 'recommend'), 3)]:
            if any(term in lower for term in terms):
                return options[index]
    return options[ordinal % len(options)]


def experience_text(spec, grade, technologies, source_text, ordinal):
    topic = topic_for(spec, source_text, ordinal)
    stack = ', '.join(technologies[:8]) or 'профильные инструменты'
    detail = ('собирал требования, проводил эксперименты, оценивал качество решений и готовил результат к внедрению'
              if grade != 'Junior' else
              'готовил данные, реализовывал отдельные компоненты и проверял результат вместе с командой')
    return (f'Проект в направлении {spec}: {topic}. На позиции уровня {grade} специалист {detail}. '
            f'В работе применялись {stack}. Описание обезличено для открытой демонстрационной базы.')


def insert_row(c, table, values):
    columns = list(values)
    c.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
              list(values.values()))


def build(source_path, output_path, model_path):
    source_path = Path(source_path).resolve()
    output_path = Path(output_path).resolve()
    if source_path == output_path or output_path.exists():
        raise FileExistsError('Output must be a new SQLite file distinct from the source')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f'{source_path.as_uri()}?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    init_db(output_path)
    names, catalog_hash = catalog()
    allowed = set(names)
    counts = {'candidates': 0, 'experience_blocks': 0, 'vacancies': 0}
    passages = []
    queries = []
    with connect(output_path) as c:
        seed_catalog(c)
        seed_technology_questions(c)
        seed_daily(c)
        for spec in matrix():
            for grade in GRADES:
                rows = candidate_rows(source, spec, grade, candidate_target(spec, grade))
                if not rows:
                    raise ValueError(f'No source resumes for {spec} / {grade}')
                for row in rows:
                    counts['candidates'] += 1
                    number = counts['candidates']
                    uid, rid = f'demo-candidate-{number:03}', f'demo-resume-{number:03}'
                    technologies = [t for t in json.loads(row['all_technologies_json'] or '[]') if t in allowed]
                    technologies = list(dict.fromkeys(technologies))
                    position = clean_public(row['position_name'], 100) or f'{spec} специалист'
                    insert_row(c, 'users', {'id': uid, 'role': 'candidate', 'name': f'Кандидат {number:03}',
                                            'origin': 'dataset', 'created_at': STAMP})
                    insert_row(c, 'resumes', {'id': rid, 'user_id': uid, 'specialization': spec,
                        'specializations_json': json.dumps([spec], ensure_ascii=False),
                        'position_name': position, 'experience_years': row['experience_years'],
                        'all_technologies_json': json.dumps(technologies, ensure_ascii=False),
                        'technology_count': len(technologies), 'raw_json': '{"demo_anonymized":true}',
                        'created_at': STAMP, 'updated_at': STAMP,
                        'expected_salary_min': row['expected_salary_min'] or demo_salary_min(spec, grade)})
                    insert_row(c, 'candidate_grades', {'user_id': uid, 'resume_id': rid,
                        'grade': grade, 'grade_source': row['grade_source'],
                        'confidence': row['confidence'], 'reviewer_notes': '',
                        'source_version': row['source_version'], 'updated_at': STAMP})
                    c.executemany('INSERT INTO resume_technologies VALUES(?,?)',
                                  [(rid, tech) for tech in technologies])
                    for table in ('candidate_skill_grade_vectors', 'candidate_skill_vectors', 'candidate_activity'):
                        for value in source.execute(f'SELECT * FROM {table} WHERE user_id=?', (row['user_id'],)):
                            item = dict(value)
                            item['user_id'] = uid
                            if table == 'candidate_skill_grade_vectors' and item['catalog_sha256'] != catalog_hash:
                                continue
                            insert_row(c, table, item)
                    achievement = source.execute('''SELECT level,stage,place FROM candidate_fsp_achievements
                      WHERE user_id=? ORDER BY id LIMIT 1''', (row['user_id'],)).fetchone()
                    if achievement:
                        insert_row(c, 'candidate_fsp_achievements', {'id': f'demo-achievement-{number:03}',
                            'user_id': uid, 'level': achievement['level'], 'stage': achievement['stage'],
                            'place': achievement['place'], 'source': 'synthetic_demo',
                            'event_name': 'Демонстрационное достижение', 'achieved_at': STAMP})
                    blocks = source.execute('''SELECT w.* FROM work_items w JOIN item_vectors iv ON iv.item_id=w.id
                      WHERE w.user_id=? AND w.specialization=? AND iv.model=? AND iv.revision=?
                      AND iv.task='retrieval.passage' AND iv.dimensions=?
                      ORDER BY length(w.description) DESC,w.id LIMIT 2''',
                      (row['user_id'], spec, MODEL, REVISION, DIMENSIONS)).fetchall()
                    for ordinal, block in enumerate(blocks):
                        item_id = f'demo-item-{number:03}-{ordinal}'
                        text = experience_text(spec, grade, technologies,
                                               block['description'], number + ordinal)
                        insert_row(c, 'work_items', {'id': item_id, 'resume_id': rid, 'user_id': uid,
                            'kind': block['kind'], 'ordinal': ordinal, 'specialization': spec,
                            'title': position, 'company': 'Демонстрационный проект',
                            'description': text, 'source_field': 'demo_seed', 'source_text': text,
                            'split_method': 'anonymized_rewrite', 'needs_review': 0, 'created_at': STAMP})
                        passages.append((item_id, uid, text))
                        counts['experience_blocks'] += 1

        for spec in matrix():
            for grade in GRADES:
                chosen = FOCUS_VACANCIES.get((spec, grade))
                if chosen:
                    original = source.execute('SELECT * FROM vacancies WHERE id=?', (chosen,)).fetchone()
                else:
                    options = source.execute('''SELECT v.* FROM vacancies v JOIN vacancy_query_vectors q
                      ON q.vacancy_id=v.id WHERE v.specialization=? AND v.grade=?
                      AND v.status='open' AND v.visibility='public'
                      AND length(coalesce(v.description,'')) BETWEEN 500 AND 4000
                      ORDER BY coalesce(v.technology_count,0) DESC,length(v.description) DESC,v.id LIMIT 60''',
                      (spec, grade)).fetchall()
                    hints = TITLE_HINTS.get(spec, ())
                    original = next((row for row in options if
                        (grade == 'Senior' or not any(word in row['vacancy_name'].lower()
                            for word in ('senior', 'lead', 'ведущий', 'главный')))
                        and not (spec == 'Data Analytics' and 'devops' in row['vacancy_name'].lower())
                        and (not hints or any(word in row['vacancy_name'].lower() for word in hints))),
                        options[0] if options else None)
                if not original:
                    raise ValueError(f'No source vacancy for {spec} / {grade}')
                counts['vacancies'] += 1
                vid = f'demo-vacancy-{counts["vacancies"]:03}'
                technologies = [t for t in json.loads(original['all_technologies_json'] or '[]') if t in allowed]
                technologies = list(dict.fromkeys(technologies))
                minimum = original['salary_min']
                maximum = original['salary_max']
                if not usable_salary_range(minimum, maximum):
                    minimum = demo_salary_min(spec, grade)
                    maximum = round(minimum * 1.5, -3)
                description = clean_public(original['description'])
                responsibilities = clean_public(original['responsibilities'])
                requirements = clean_public(original['position_requirements'])
                if not responsibilities:
                    responsibilities = description[:1400]
                name = clean_public(original['vacancy_name'], 180)
                vacancy = {'id': vid, 'specialization': spec,
                    'specializations_json': json.dumps([spec], ensure_ascii=False),
                    'vacancy_name': name, 'region': clean_public(original['region'], 100),
                    'experience_requirements': original['experience_requirements'],
                    'salary_min': minimum, 'salary_max': maximum,
                    'skills_text': ', '.join(technologies),
                    'position_requirements': requirements, 'responsibilities': responsibilities,
                    'description': description,
                    'all_technologies_json': json.dumps(technologies, ensure_ascii=False),
                    'technology_count': len(technologies), 'raw_json': '{"demo_anonymized":true}',
                    'created_at': STAMP, 'visibility': 'public', 'status': 'open',
                    'grade': grade, 'grade_source': 'demo_curated_v1',
                    'required_technologies_json': '[]'}
                insert_row(c, 'vacancies', vacancy)
                c.executemany('INSERT INTO vacancy_technologies VALUES(?,?)',
                              [(vid, tech) for tech in technologies])
                text = vacancy_experience_text(vacancy)
                if not text:
                    raise ValueError(f'Empty vacancy query for {vid}')
                queries.append((vid, text))

    print(f'Selected {counts}; embedding {len(passages)} experience blocks and {len(queries)} vacancies', flush=True)
    encoder = LocalJina(model_path=str(model_path), task='retrieval.passage')
    for offset in range(0, len(passages), 8):
        batch = passages[offset:offset + 8]
        vectors = normalize_vectors(encoder.encode([text for _, _, text in batch]), len(batch))
        with connect(output_path) as c:
            for (item_id, uid, _), vector in zip(batch, vectors):
                insert_row(c, 'item_vectors', {'item_id': item_id, 'user_id': uid,
                    'model': MODEL, 'revision': REVISION, 'task': 'retrieval.passage',
                    'dimensions': DIMENSIONS, 'vector': vector.astype('<f4').tobytes(),
                    'chunk_count': 1, 'created_at': STAMP})
        if offset % 80 == 0:
            print(f'Experience vectors: {min(offset + 8, len(passages))}/{len(passages)}', flush=True)
    encoder.task = 'retrieval.query'
    for offset in range(0, len(queries), 8):
        batch = queries[offset:offset + 8]
        vectors = normalize_vectors(encoder.encode([text for _, text in batch]), len(batch))
        with connect(output_path) as c:
            for (vid, _), vector in zip(batch, vectors):
                store_vacancy_query_vector(c, vid, vector)
    source.close()
    with connect(output_path) as c:
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        c.execute('PRAGMA journal_mode=DELETE')
        c.execute('VACUUM')
        print('Integrity:', c.execute('PRAGMA integrity_check').fetchone()[0], flush=True)
    print(f'Demo database: {output_path} ({output_path.stat().st_size / 1024**2:.1f} MiB)', flush=True)
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=ROOT / 'data' / 'fsp.sqlite3', type=Path)
    parser.add_argument('--output', default=ROOT / 'data' / 'demo_seed.sqlite3', type=Path)
    parser.add_argument('--model-path', default=ROOT.parent / 'models' / 'jina-embeddings-v3', type=Path)
    args = parser.parse_args()
    build(args.source, args.output, args.model_path)
