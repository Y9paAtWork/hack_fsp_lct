"""Four-signal vacancy matching. Demo signals are explicitly marked synthetic.

The catalog CSV defines the position of every skill in the float32 vector.
Experience uses only individual work/project Jina vectors and vacancy duties.
"""

import csv
import hashlib
import itertools
import json
import re
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import numpy as np

from .db import ROOT, connect, init_db, now
from .embedding_worker import DIMENSIONS, MODEL, REVISION
from .technology_heuristics import extract_technologies

CATALOG_PATH = Path(__file__).with_name('technology_stack_FINAL.csv')
WEIGHTS = {'experience': 0.70, 'skills': 0.25, 'activity': 0.025, 'fsp': 0.025}
SCORING_VERSION = 'demo-four-signal-v2'


def catalog():
    with CATALOG_PATH.open(encoding='utf-8-sig', newline='') as file:
        names = [row['technology'].strip() for row in csv.DictReader(file)]
    if len(names) != len(set(names)) or not names:
        raise ValueError('Technology catalog is empty or has duplicate names')
    return names, hashlib.sha256('\n'.join(names).encode()).hexdigest()


def unit(*parts):
    data = '\0'.join(map(str, parts)).encode()
    return int.from_bytes(hashlib.sha256(data).digest()[:8], 'big') / 2**64


def activity_score(days, tests):
    return round(0.55 * min(days / 20, 1) + 0.45 * min(tests / 12, 1), 6)


def demo_neutral(value):
    """Keep invented 0..1 signals in 0.48..0.52 until measured data exists."""
    return round(0.48 + 0.04 * min(1.0, max(0.0, value)), 6)


def refresh_real_activity(c, user_id):
    """Roll up Moscow visit dates and completed daily scores for 30 days."""
    since = (datetime.now(timezone.utc).astimezone(ZoneInfo('Europe/Moscow')).date()-timedelta(days=29)).isoformat()
    days = c.execute('SELECT count(*) FROM candidate_activity_days_msk WHERE user_id=? AND day_msk>=?', (user_id,since)).fetchone()[0]
    tests, mean = c.execute('SELECT count(*),coalesce(avg(score),0) FROM daily_sessions WHERE user_id=? AND day_msk>=? AND completed_at IS NOT NULL', (user_id,since)).fetchone()
    score = round(0.55*min(days/20,1)+0.45*min(tests/12,1)*mean/100,6)
    c.execute('''INSERT INTO candidate_activity VALUES(?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET active_days_30=excluded.active_days_30,
        daily_tests_30=excluded.daily_tests_30,activity_score=excluded.activity_score,
        source=excluded.source,updated_at=excluded.updated_at''',
        (user_id, days, tests, score, 'server_events', now()))


def record_visit(c, user_id):
    day=datetime.now(timezone.utc).astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    inserted = c.execute('INSERT OR IGNORE INTO candidate_activity_days_msk VALUES(?,?)', (user_id,day)).rowcount
    if inserted or c.execute('SELECT 1 FROM candidate_activity WHERE user_id=?', (user_id,)).fetchone() is None:
        refresh_real_activity(c, user_id)


def record_completed_daily_test(c, user_id, score):
    """Call only after a server-graded daily test, never from an untrusted score."""
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        raise ValueError('score must be an integer from 0 to 100')
    c.execute('INSERT INTO candidate_daily_tests VALUES(?,?,?,?)',
              (uuid.uuid4().hex, user_id, score, now()))
    refresh_real_activity(c, user_id)


def achievement_score(level, stage, place):
    level_factor = {'regional': 0.6, 'national': 0.8, 'international': 1.0}[level]
    stage_factor = {'qualifier': 0.4, 'final': 1.0}[stage]
    place_factor = {1: 1.0, 2: 0.85, 3: 0.7, None: 0.35}[place]
    return level_factor * stage_factor * place_factor


def fsp_score(rows):
    scores = sorted((achievement_score(r['level'], r['stage'], r['place']) for r in rows), reverse=True)
    return min(1.0, scores[0] + 0.1 * sum(scores[1:])) if scores else 0.0


def ensure_catalog(c, names):
    found = {r[0] for r in c.execute('SELECT name FROM technologies')}
    if found != set(names):
        raise ValueError(f'Database technology catalog differs from CSV: {len(found)} vs {len(names)}')


def rebuild_skill_vector(c, user_id, names, catalog_hash):
    index = {name: i for i, name in enumerate(names)}
    values = np.zeros(len(names), dtype='<f4')
    rows = c.execute('''SELECT rt.technology,tr.score,tr.source FROM resumes r
        JOIN resume_technologies rt ON rt.resume_id=r.id
        JOIN skill_test_results tr ON tr.user_id=r.user_id AND tr.technology=rt.technology
        WHERE r.user_id=?''', (user_id,))
    sources = set()
    for technology, score, source in rows:
        if technology in index:
            values[index[technology]] = score / 100
            sources.add(source)
    vector_source = 'mixed' if len(sources) > 1 else next(iter(sources), 'none')
    c.execute('''INSERT INTO candidate_skill_vectors VALUES(?,?,?,?,?,?)
      ON CONFLICT(user_id) DO UPDATE SET catalog_sha256=excluded.catalog_sha256,
      dimensions=excluded.dimensions,vector=excluded.vector,source=excluded.source,
      updated_at=excluded.updated_at''',
      (user_id, catalog_hash, len(names), values.tobytes(), vector_source, now()))


def record_verified_skill_test(c, user_id, technology, score):
    """Server/trusted import path for actual per-technology results."""
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        raise ValueError('score must be an integer from 0 to 100')
    names, catalog_hash = catalog()
    if technology not in names:
        raise ValueError('Technology is not in technology_stack_FINAL')
    owned = c.execute('''SELECT 1 FROM resumes r JOIN resume_technologies rt ON rt.resume_id=r.id
        WHERE r.user_id=? AND rt.technology=?''', (user_id, technology)).fetchone()
    if not owned:
        raise ValueError('Technology is not in this candidate stack')
    c.execute('''INSERT INTO skill_test_results VALUES(?,?,?,?,?)
        ON CONFLICT(user_id,technology) DO UPDATE SET score=excluded.score,
        source=excluded.source,tested_at=excluded.tested_at''',
        (user_id, technology, score, 'verified', now()))
    rebuild_skill_vector(c, user_id, names, catalog_hash)


def populate_demo(db=None):
    """Idempotently seed synthetic results; never overwrite verified test scores."""
    init_db(db)
    names, catalog_hash = catalog()
    count = {'candidates': 0, 'skill_results': 0, 'activity': 0, 'achievements': 0}
    with connect(db) as c:
        ensure_catalog(c, names)
        rows = c.execute('''SELECT u.id, rt.technology FROM users u
            LEFT JOIN resumes r ON r.user_id=u.id
            LEFT JOIN resume_technologies rt ON rt.resume_id=r.id
            WHERE u.role='candidate' ORDER BY u.id''')
        for user_id, grouped in itertools.groupby(rows, key=lambda r: r[0]):
            count['candidates'] += 1
            technologies = [r[1] for r in grouped if r[1] is not None]
            tests = [(user_id, tech, 48 + int(5 * unit('skill-v2', user_id, tech)), 'synthetic_demo', None)
                     for tech in technologies]
            c.executemany('''INSERT INTO skill_test_results VALUES(?,?,?,?,?)
                ON CONFLICT(user_id,technology) DO UPDATE SET score=excluded.score
                WHERE skill_test_results.source='synthetic_demo' ''', tests)
            count['skill_results'] += len(tests)
            x = unit('activity-days-v1', user_id)
            days = 0 if x < 0.2 else 1 + int(22 * unit('activity-days-count-v1', user_id))
            daily = int(days * unit('activity-tests-v1', user_id) * 0.7)
            c.execute('''INSERT INTO candidate_activity VALUES(?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET active_days_30=excluded.active_days_30,
                daily_tests_30=excluded.daily_tests_30,activity_score=excluded.activity_score,
                updated_at=excluded.updated_at WHERE candidate_activity.source='synthetic_demo' ''',
                      (user_id, days, daily, demo_neutral(activity_score(days, daily)), 'synthetic_demo', now()))
            count['activity'] += 1
            if unit('fsp-presence-v1', user_id) < 0.08:
                n = 1 + int(unit('fsp-count-v1', user_id) * 2)
                for k in range(n):
                    level = ('regional', 'national', 'international')[int(3 * unit('fsp-level-v1', user_id, k))]
                    stage = 'final' if unit('fsp-stage-v1', user_id, k) > 0.35 else 'qualifier'
                    p = unit('fsp-place-v1', user_id, k)
                    place = (1 + int(3 * p)) if p < 0.45 else None
                    achievement_id = hashlib.sha256(f'demo-fsp:{user_id}:{k}'.encode()).hexdigest()
                    c.execute('INSERT OR IGNORE INTO candidate_fsp_achievements VALUES(?,?,?,?,?,?,?,?)',
                              (achievement_id, user_id, level, stage, place, 'synthetic_demo', '', None))
                    count['achievements'] += 1
            if count['candidates'] % 1000 == 0:
                c.commit()
        # Rebuild after inserts so any verified score already in the DB takes priority.
        rows = c.execute('''SELECT u.id,rt.technology,tr.score,tr.source FROM users u
            LEFT JOIN resumes r ON r.user_id=u.id
            LEFT JOIN resume_technologies rt ON rt.resume_id=r.id
            LEFT JOIN skill_test_results tr ON tr.user_id=u.id AND tr.technology=rt.technology
            WHERE u.role='candidate' ORDER BY u.id''')
        index = {name: i for i, name in enumerate(names)}
        for n, (user_id, grouped) in enumerate(itertools.groupby(rows, key=lambda r: r[0]), 1):
            values = np.zeros(len(names), dtype='<f4')
            sources = set()
            for _, tech, score, source in grouped:
                if tech in index and score is not None:
                    values[index[tech]] = score / 100
                    sources.add(source)
            vector_source = 'mixed' if len(sources) > 1 else next(iter(sources), 'none')
            c.execute('''INSERT INTO candidate_skill_vectors VALUES(?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET catalog_sha256=excluded.catalog_sha256,
                dimensions=excluded.dimensions,vector=excluded.vector,source=excluded.source,
                updated_at=excluded.updated_at''',
                (user_id, catalog_hash, len(names), values.tobytes(), vector_source, now()))
            if n % 1000 == 0:
                c.commit()
    return {**count, 'dimensions': len(names), 'catalog_sha256': catalog_hash,
            'provenance': 'synthetic_demo; not measured test/activity/FSP results'}


def vacancy_experience_text(vacancy):
    """Deliberately excludes technology and skill requirement columns."""
    duties = (vacancy['responsibilities'] or '').strip()
    if not duties:
        description = (vacancy['description'] or '').strip()
        heading = re.search(
            r'\b(?:обязанности|основные задачи|задачи|что предстоит делать|'
            r'чем предстоит заниматься|чем вы будете заниматься)\s*[:：]',
            description, re.IGNORECASE)
        if heading:
            section = description[heading.end():]
            end = re.search(
                r'\b(?:требования|мы ожидаем|нам важно|что мы предлагаем|'
                r'условия работы|условия|будет плюсом)\s*[:：]',
                section, re.IGNORECASE)
            duties = section[:end.start() if end else None].strip()
            if len(duties) < 30:
                duties = ''
    title = (vacancy['vacancy_name'] or '').strip()
    if not duties:
        return title[:2200]
    beginning = re.search(r'\b(?:обязанности|задачи|что предстоит делать)\s*:?\s*', duties, re.IGNORECASE)
    if beginning and beginning.start() < len(duties) * 0.6:
        duties = duties[beginning.end():]
    ending = re.search(r'\b(?:требования|мы ожидаем|кандидат должен|нам важно)\s*:?\s*', duties, re.IGNORECASE)
    if ending and ending.start() > 30:
        duties = duties[:ending.start()]
    return (title + '\n' + duties)[:2200].strip()


def store_vacancy_query_vector(c, vacancy_id, vector):
    vacancy = c.execute('SELECT vacancy_name,responsibilities,description FROM vacancies WHERE id=?', (vacancy_id,)).fetchone()
    if not vacancy:
        raise ValueError('Vacancy not found')
    text = vacancy_experience_text(vacancy)
    if not text:
        raise ValueError('Vacancy has no title or duties to compare with experience')
    value = np.asarray(vector, dtype='<f4')
    if value.shape != (DIMENSIONS,) or not np.isfinite(value).all():
        raise ValueError('Invalid vacancy query vector')
    norm = np.linalg.norm(value)
    if norm <= 0:
        raise ValueError('Zero vacancy query vector')
    value = value / norm
    c.execute('''INSERT INTO vacancy_query_vectors VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(vacancy_id) DO UPDATE SET model=excluded.model,revision=excluded.revision,
      task=excluded.task,dimensions=excluded.dimensions,vector=excluded.vector,
      text_sha256=excluded.text_sha256,created_at=excluded.created_at''',
      (vacancy_id, MODEL, REVISION, 'retrieval.query', DIMENSIONS, value.astype('<f4').tobytes(),
       hashlib.sha256(text.encode()).hexdigest(), now()))


def rank_vacancy(c, vacancy_id, limit=20, candidate_ids=None, weights=None, verified_only=False):
    if not 1 <= limit <= 1000:
        raise ValueError('limit must be 1..1000')
    weights = WEIGHTS if weights is None else weights
    if set(weights) != set(WEIGHTS) or any(v < 0 for v in weights.values()) or abs(sum(weights.values()) - 1) > 1e-6:
        raise ValueError('weights must contain four nonnegative components summing to 1')
    names, catalog_hash = catalog()
    vacancy = c.execute('SELECT * FROM vacancies WHERE id=?', (vacancy_id,)).fetchone()
    if not vacancy:
        raise ValueError('Vacancy not found')
    q = c.execute('SELECT * FROM vacancy_query_vectors WHERE vacancy_id=?', (vacancy_id,)).fetchone()
    text = vacancy_experience_text(vacancy)
    if not q or q['model'] != MODEL or q['revision'] != REVISION or q['task'] != 'retrieval.query' or not text or q['text_sha256'] != hashlib.sha256(text.encode()).hexdigest():
        raise ValueError('Vacancy query vector missing or stale; run index-vacancies')
    query = np.frombuffer(q['vector'], dtype='<f4')
    if query.shape != (DIMENSIONS,):
        raise ValueError('Invalid stored vacancy vector')
    requested = extract_technologies(vacancy['description'])
    technology_source = 'description_heuristics'
    if not requested:
        requested = [r[0] for r in c.execute('SELECT technology FROM vacancy_technologies WHERE vacancy_id=?', (vacancy_id,))]
        technology_source = 'stored_catalog_fallback'
    name_index = {name: i for i, name in enumerate(names)}
    required_indexes = [name_index[name] for name in requested if name in name_index]
    sql = '''SELECT r.id resume_id,r.user_id,r.position_name,r.specialization,r.all_technologies_json candidate_stack_json,u.origin,u.name candidate_name,
         sv.vector skill_vector,sv.catalog_sha256,sv.dimensions,sv.source skill_source,
         a.activity_score,a.source activity_source,vg.grade verified_grade
         FROM resumes r JOIN users u ON u.id=r.user_id
         LEFT JOIN candidate_skill_vectors sv ON sv.user_id=r.user_id
         LEFT JOIN candidate_activity a ON a.user_id=r.user_id
         LEFT JOIN candidate_verified_grades vg ON vg.user_id=r.user_id AND vg.specialization=r.specialization
         WHERE r.specialization=?'''
    params = [vacancy['specialization']]
    if verified_only:sql += ' AND vg.grade IS NOT NULL'
    if candidate_ids is not None:
        if not candidate_ids:
            return []
        sql += f" AND r.id IN ({','.join('?' for _ in candidate_ids)})"
        params.extend(candidate_ids)
    candidates = [dict(row) for row in c.execute(sql, params)]
    for row in candidates:
        if row['origin']=='registration':
            refresh_real_activity(c,row['user_id'])
            row['activity_score']=c.execute('SELECT activity_score FROM candidate_activity WHERE user_id=?',
                                            (row['user_id'],)).fetchone()[0]
    user_ids = [row['user_id'] for row in candidates]
    experience = {uid: [] for uid in user_ids}
    achievements = {uid: [] for uid in user_ids}
    for offset in range(0, len(user_ids), 400):
        batch = user_ids[offset:offset + 400]
        marks = ','.join('?' for _ in batch)
        for row in c.execute(f'''SELECT iv.user_id,iv.item_id,w.kind,iv.vector FROM item_vectors iv
          JOIN work_items w ON w.id=iv.item_id WHERE iv.user_id IN ({marks})
          AND iv.model=? AND iv.revision=? AND iv.task='retrieval.passage' AND iv.dimensions=?''',
          [*batch, MODEL, REVISION, DIMENSIONS]):
            vector = np.frombuffer(row['vector'], dtype='<f4')
            if vector.shape == (DIMENSIONS,):
                experience[row['user_id']].append((max(0.0, float(np.dot(query, vector))), row['item_id'], row['kind']))
        for row in c.execute(f'''SELECT user_id,level,stage,place,source FROM candidate_fsp_achievements
          WHERE user_id IN ({marks})''', batch):
            achievements[row['user_id']].append(row)
    results = []
    for row in candidates:
        uid = row['user_id']
        best = sorted(experience[uid], key=lambda item: (-item[0], item[1]))[0] if experience[uid] else None
        exp_score = best[0] if best else 0.0
        skill_blob = row['skill_vector']
        if skill_blob and row['catalog_sha256'] == catalog_hash and row['dimensions'] == len(names) and required_indexes:
            values = np.frombuffer(skill_blob, dtype='<f4')
            skill_score = float(np.mean(np.clip(values[required_indexes], 0, 1)))
        else:
            skill_score = 0.0
        active_score = float(row['activity_score'] or 0.0)
        verified_fsp = [a for a in achievements[uid] if a['source'] != 'synthetic_demo']
        if verified_fsp:
            fsp = fsp_score(verified_fsp)
        elif row['origin'] == 'dataset' or achievements[uid]:
            fsp = demo_neutral(fsp_score(achievements[uid]))
        else:
            fsp = 0.0
        components = {'experience': exp_score, 'skills': skill_score, 'activity': active_score, 'fsp': fsp}
        final = sum(weights[k] * components[k] for k in WEIGHTS)
        results.append({'resume_id': row['resume_id'], 'user_id': uid,
            'candidate_name':row['candidate_name'], 'position_name': row['position_name'], 'specialization': row['specialization'],
            'contactable':row['origin']=='registration',
            'candidate_stack':json.loads(row['candidate_stack_json'] or '[]'),
            'verified_grade': row['verified_grade'],
            'score': round(final, 6), 'components': {k: round(v, 6) for k, v in components.items()},
            'experience_blocks_found': len(experience[uid]), 'requested_technologies': len(required_indexes),
            'vacancy_technologies': requested, 'technology_source': technology_source,
            'best_experience_item_id': best[1] if best else None,
            'best_experience_kind': best[2] if best else None,
            'skill_source': row['skill_source'], 'activity_source': row['activity_source'],
            'fsp_sources': sorted({a['source'] for a in achievements[uid]})})
    results.sort(key=lambda r: (-r['score'], r['resume_id']))
    return results[:limit]


def load_ranked(db, vacancy_id, limit=20):
    with connect(db) as c:
        return rank_vacancy(c, vacancy_id, limit)
