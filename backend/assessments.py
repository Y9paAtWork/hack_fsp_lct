"""Server-owned technology tests, grade rules and Moscow daily practice."""
import hashlib
import json
import os
import random
import threading
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from fastapi import HTTPException

from .db import now
from .ranking import catalog, record_verified_skill_test, refresh_real_activity

BASE = Path(__file__).parent
LEVELS = ('Junior', 'Middle', 'Senior')
THRESHOLDS = {'Junior': 75, 'Middle': 80, 'Senior': 85}
RULE_VERSION = 'grade-rules-1.0-tech-only-v1'
BANK_VERSION = 'technology-bank-2026-10-unreviewed-v1'
DAILY_VERSION = 'general-it-106-v1'
DAILY_SELECTION_VERSION = 'topic-selection-v1'
CALIBRATION_VERSION = 'jina-v3-text-matching-cosine-v1'
MOSCOW = ZoneInfo('Europe/Moscow')
ENCODER_LOCK = threading.Lock()
DAILY_TOPICS = {
    'Backend': ('HTTP','Databases','Architecture'),
    'Frontend': ('Web','HTTP','Testing'),
    'Fullstack': ('Web','HTTP','Databases'),
    'ML / AI': ('ML','Data','Algorithms'),
    'Data Science': ('ML','Data','Algorithms'),
    'Data Engineering': ('Data','Databases','Architecture'),
    'Data Analytics': ('Data','Databases','Algorithms'),
    'System Analysis': ('Productivity','Architecture','Data'),
    'DevOps / SRE': ('DevOps','Observability','Operating Systems'),
    'QA': ('Testing','Algorithms','Web'),
    'Mobile': ('Web','Testing','Architecture'),
    'Cybersecurity': ('Security','Networks','Operating Systems'),
    'System / Network': ('Networks','Security','Operating Systems'),
    'Database': ('Databases','Data','Architecture'),
    'Software Engineering': ('Algorithms','Architecture','Data Structures'),
    'Other IT': ('Git','Algorithms','HTTP'),
}

@lru_cache(maxsize=1)
def matrix():
    return json.loads((BASE / 'grade_matrix.json').read_text(encoding='utf-8'))['specializations']


def seed_daily(c):
    questions = json.loads((BASE / 'daily_questions.json').read_text(encoding='utf-8'))
    c.executemany('''INSERT INTO daily_questions
        (id,grade,topic,question_type,question_text,options_json,correct_answer_json,points,explanation,source_version)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        correct_answer_json=excluded.correct_answer_json
        WHERE daily_questions.source_version=excluded.source_version
        AND daily_questions.correct_answer_json!=excluded.correct_answer_json''', [(
        q['id'], q['grade'], q['topic'], q['question_type'], q['question_text'], q['options_json'],
        json.dumps(json.loads(q['correct_answer']) if q['question_type']=='multi' else q['correct_answer'], ensure_ascii=False),
        q['points'], q['explanation'], DAILY_VERSION
    ) for q in questions])


def seed_technology_questions(c):
    if c.execute('SELECT count(*) FROM test_questions').fetchone()[0] >= 1730:
        return
    questions = json.loads((BASE / 'technology_questions.json').read_text(encoding='utf-8'))
    columns = list(questions[0])
    placeholders = ','.join('?' for _ in columns)
    c.executemany(f"INSERT OR IGNORE INTO test_questions({','.join(columns)}) VALUES({placeholders})",
                  [tuple(q[k] for k in columns) for q in questions])


def question_public(row):
    return {'id': row['id'], 'type': row['question_type'], 'text': row['question_text'],
            'options': json.loads(row['options_json'] or '[]'),
            'code_snippet': row['code_snippet'] if 'code_snippet' in row.keys() else None}


def selected_specializations(c,user_id):
    row=c.execute('SELECT profile_json FROM candidate_profiles WHERE user_id=?',(user_id,)).fetchone()
    return json.loads(row[0]).get('selected',[]) if row else []


def stack(c, user_id, specialization=None):
    names=[r[0] for r in c.execute('''SELECT rt.technology FROM resumes r JOIN resume_technologies rt
      ON rt.resume_id=r.id WHERE r.user_id=? ORDER BY rt.technology''', (user_id,))]
    if specialization is None:return names
    row=c.execute('SELECT profile_json FROM candidate_profiles WHERE user_id=?',(user_id,)).fetchone()
    wanted=set(json.loads(row[0]).get('stacks',{}).get(specialization,[])) if row else set()
    return [name for name in names if name in wanted]


def latest_attempt(c, user_id, technology, grade):
    return c.execute('''SELECT * FROM assessment_attempts WHERE user_id=? AND technology=? AND grade=?
      ORDER BY started_at DESC LIMIT 1''', (user_id, technology, grade)).fetchone()


def retry_allowed(completed_at, clock=None):
    return (clock or datetime.now(timezone.utc)) >= datetime.fromisoformat(completed_at) + timedelta(days=30)


def user_test_grade(c, user_id, specialization=None):
    specialization=specialization or active_spec(c,user_id)
    row=c.execute('''SELECT current_grade FROM candidate_specialization_grades
      WHERE user_id=? AND specialization=?''',(user_id,specialization)).fetchone()
    if row:grade=row[0]
    else:
        row=c.execute('SELECT current_grade FROM candidate_test_grades WHERE user_id=?',(user_id,)).fetchone()
        if not row:row=c.execute('SELECT grade FROM candidate_initial_grades WHERE user_id=?',(user_id,)).fetchone()
        if not row:row=c.execute('SELECT grade FROM candidate_grades WHERE user_id=?',(user_id,)).fetchone()
        if not row:row=c.execute('SELECT grade FROM candidate_grade_estimates WHERE user_id=?',(user_id,)).fetchone()
        grade=row[0] if row and row[0] in LEVELS else 'Junior'
    verified=c.execute('SELECT grade FROM candidate_verified_grades WHERE user_id=? AND specialization=?',
                       (user_id,specialization)).fetchone()
    if verified and LEVELS.index(verified[0])>LEVELS.index(grade):grade=verified[0]
    return grade


def grade_unlocked(c, user_id, tech, grade, own_grade):
    return grade == own_grade


def current_grade_progress(c,user_id,specialization=None):
    spec=specialization or active_spec(c,user_id)
    current=user_test_grade(c,user_id,spec)
    row=c.execute('SELECT grade FROM candidate_verified_grades WHERE user_id=? AND specialization=?',
                  (user_id,spec)).fetchone()
    confirmed=row[0] if row else None
    can_advance=(current!='Senior' and confirmed in LEVELS and
                 LEVELS.index(confirmed)>=LEVELS.index(current))
    return {'grade':current,'specialization':spec,'confirmed_grade':confirmed,
            'can_advance':bool(can_advance),'next_grade':LEVELS[LEVELS.index(current)+1] if current!='Senior' else None}


def advance_test_grade(c,user_id,specialization=None):
    progress=current_grade_progress(c,user_id,specialization)
    if not progress['can_advance']:
        raise HTTPException(409,'Сначала подтвердите текущий грейд по матрице технологий')
    c.execute('''INSERT INTO candidate_specialization_grades
      (user_id,specialization,declared_grade,current_grade,declared_at,updated_at)
      VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,specialization) DO UPDATE SET
      current_grade=excluded.current_grade,updated_at=excluded.updated_at''',
      (user_id,progress['specialization'],user_test_grade(c,user_id,progress['specialization']),progress['next_grade'],now(),now()))
    return current_grade_progress(c,user_id,progress['specialization'])


def catalog_tests(c, user_id, specialization=None):
    rows = []
    specialization=specialization or active_spec(c,user_id)
    own_grade=user_test_grade(c,user_id,specialization)
    for tech in stack(c, user_id,specialization):
        available = c.execute('''SELECT grade,count(*) FROM test_questions WHERE technology=? AND
          (question_type!='write_code' OR technology IN ('Python','JavaScript','SQL'))
          GROUP BY grade''', (tech,)).fetchall()
        for grade, count in available:
            if not grade_unlocked(c,user_id,tech,grade,own_grade):continue
            prev = latest_attempt(c, user_id, tech, grade)
            retry_at = None
            if prev and prev['status'] == 'completed':
                retry_at = (datetime.fromisoformat(prev['completed_at']) + timedelta(days=30)).isoformat()
            locked = bool(prev and prev['completed_at'] and not retry_allowed(prev['completed_at']))
            rows.append({'technology': tech, 'grade': grade, 'questions': count,
                         'last_score': prev['score'] if prev else None, 'retry_at': retry_at,
                         'available': not locked or bool(prev and prev['status'] == 'started'),
                         'own_grade':own_grade})
    return rows


def start_technology(c, user_id, tech, grade, specialization=None):
    specialization=specialization or active_spec(c,user_id)
    if tech not in stack(c, user_id,specialization) or grade not in LEVELS:
        raise HTTPException(404, 'Технология или уровень не найдены в вашем стеке')
    own_grade=user_test_grade(c,user_id,specialization)
    if not grade_unlocked(c,user_id,tech,grade,own_grade):
        raise HTTPException(409,'Этот уровень пока недоступен для вашего грейда')
    latest = latest_attempt(c, user_id, tech, grade)
    if latest and latest['status'] == 'started':
        ids = json.loads(latest['question_ids_json'])
        return {'attempt_id': latest['id'], 'technology': tech, 'grade': grade,
                'questions': [question_public(c.execute('SELECT * FROM test_questions WHERE id=?', (qid,)).fetchone()) for qid in ids]}
    if latest and latest['completed_at'] and not retry_allowed(latest['completed_at']):
        raise HTTPException(409, 'Пересдача доступна через 30 суток после последней попытки')
    ids = [r[0] for r in c.execute('''SELECT id FROM test_questions WHERE technology=? AND grade=?
      AND (question_type!='write_code' OR technology IN ('Python','JavaScript','SQL')) ORDER BY id''', (tech, grade))]
    if not ids:
        raise HTTPException(404, 'Для этого уровня пока нет поддерживаемых заданий')
    attempt_id = uuid.uuid4().hex
    c.execute('INSERT INTO assessment_attempts VALUES(?,?,?,?,?,?,?,?,?,?)',
              (attempt_id, user_id, tech, grade, json.dumps(ids), 'started', None, now(), None, BANK_VERSION))
    return {'attempt_id': attempt_id, 'technology': tech, 'grade': grade,
            'questions': [question_public(c.execute('SELECT * FROM test_questions WHERE id=?', (qid,)).fetchone()) for qid in ids]}


@lru_cache(maxsize=1)
def text_encoder():
    from .embedding_worker import LocalJina
    bundled = BASE.parent.parent / 'models' / 'jina-embeddings-v3'
    model_path = os.environ.get('FSP_JINA_MODEL_PATH') or (str(bundled) if bundled.is_dir() else None)
    return LocalJina(model_path=model_path, task='text-matching')


def text_score(answer, ideal):
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 6000:
        return 0.0
    with ENCODER_LOCK:
        vectors = np.asarray(text_encoder().encode([answer, ideal]), dtype=np.float32)
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    cosine = float(np.dot(vectors[0], vectors[1]))
    # Calibration v1: conservative linear map; validate against expert labels before production confirmation.
    return round(float(np.clip((cosine - 0.55) / 0.35, 0, 1)), 6)


def objective_score(kind, answer, correct):
    if kind == 'single':
        return 1.0 if isinstance(answer, str) and answer in correct and len(correct) == 1 else 0.0
    if kind == 'multi':
        return 1.0 if isinstance(answer, list) and len(answer) == len(set(answer)) and set(answer) == set(correct) else 0.0
    raise ValueError(kind)


def grade_question(row, answer):
    kind = row['question_type']
    correct = json.loads(row['correct_answers_json'])
    if kind in ('single', 'multi'):
        return objective_score(kind, answer, correct), 'exact-key-v1'
    if kind in ('short_answer', 'code_debug'):
        if not correct: raise RuntimeError('Missing reference answer')
        return max(text_score(answer, ideal) for ideal in correct), CALIBRATION_VERSION
    if kind == 'write_code':
        from .sandbox import grade_code
        return grade_code(row['technology'], answer, json.loads(row['tests_json'])), 'docker-hidden-tests-v1'
    raise RuntimeError('Unsupported question type')


def submit_technology(c, user_id, attempt_id, answers):
    attempt = c.execute('SELECT * FROM assessment_attempts WHERE id=? AND user_id=?', (attempt_id, user_id)).fetchone()
    if not attempt: raise HTTPException(404, 'Попытка не найдена')
    if attempt['status'] != 'started': raise HTTPException(409, 'Попытка уже завершена')
    if attempt['technology'] not in stack(c,user_id):
        raise HTTPException(409, 'Верните технологию в стек перед завершением теста')
    ids = json.loads(attempt['question_ids_json'])
    if not isinstance(answers, dict) or set(answers) != set(ids):
        raise HTTPException(422, 'Ответьте на все вопросы попытки')
    graded = []
    for qid in ids:
        row = c.execute('SELECT * FROM test_questions WHERE id=?', (qid,)).fetchone()
        try: value, version = grade_question(row, answers[qid])
        except Exception as exc:
            # No score is recorded if Jina or Docker worker is unavailable.
            raise HTTPException(503, f'Оценивание недоступно: {type(exc).__name__}') from exc
        graded.append((qid, answers[qid], value, version, float(row['points'])))
    score = round(100 * sum(v * p for _, _, v, _, p in graded) / sum(p for *_, p in graded))
    at = now()
    c.executemany('INSERT INTO assessment_answers VALUES(?,?,?,?,?)',
                  [(attempt_id, qid, json.dumps(ans, ensure_ascii=False), val, version) for qid, ans, val, version, _ in graded])
    c.execute('UPDATE assessment_attempts SET status=?,score=?,completed_at=? WHERE id=?', ('completed', score, at, attempt_id))
    tech, grade = attempt['technology'], attempt['grade']
    old = c.execute('SELECT best_score FROM technology_level_results WHERE user_id=? AND technology=? AND grade=?',
                    (user_id, tech, grade)).fetchone()
    best = max(score, old[0] if old else 0)
    c.execute('''INSERT INTO technology_level_results VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(user_id,technology,grade) DO UPDATE SET best_score=excluded.best_score,
      passed=excluded.passed,attempt_id=excluded.attempt_id,tested_at=excluded.tested_at,
      source_version=excluded.source_version''',
      (user_id, tech, grade, best, int(best >= THRESHOLDS[grade]), attempt_id, at, BANK_VERSION))
    record_verified_skill_test(c, user_id, tech, score)
    progress = update_grades(c, user_id)
    return {'score': score, 'technology': tech, 'grade': grade, 'passed': score >= THRESHOLDS[grade],
            'next_level_available': score >= 90 and grade != 'Senior', 'progress': progress}


def grade_progress(c, user_id):
    profile = c.execute('SELECT profile_json FROM candidate_profiles WHERE user_id=?', (user_id,)).fetchone()
    selected = json.loads(profile[0]).get('selected', []) if profile else []
    result = {}
    passed = {(r['technology'], r['grade']) for r in c.execute('''SELECT technology,grade FROM technology_level_results
      WHERE user_id=? AND passed=1''', (user_id,))}
    for spec in selected:
        rules = matrix().get(spec)
        if not rules: continue
        confirmed_row = c.execute('SELECT grade FROM candidate_verified_grades WHERE user_id=? AND specialization=?',
                                  (user_id, spec)).fetchone()
        confirmed = confirmed_row[0] if confirmed_row else None
        target = LEVELS[min(LEVELS.index(confirmed) + 1, 2)] if confirmed else 'Junior'
        levels = {}
        for level in LEVELS:
            requirement = rules['levels'][level]
            groups = []
            for group in rules['groups']:
                matches = [tech for tech in group['alternatives'] if any((tech, grade) in passed for grade in LEVELS)]
                target_matches = [tech for tech in group['alternatives'] if any((tech, LEVELS[j]) in passed
                                  for j in range(LEVELS.index(level), len(LEVELS)))]
                groups.append({'name': group['name'], 'done': len(matches) >= group['minimum_technologies'],
                               'at_target': len(target_matches) >= group['minimum_technologies'],
                               'matched': matches, 'needed': group['minimum_technologies'],
                               'alternatives': group['alternatives']})
            done = sum(g['done'] for g in groups)
            at_target = sum(g['at_target'] for g in groups)
            mandatory = all(g['done'] for g in groups[:requirement['mandatory_first_n_groups']])
            eligible = done >= requirement['minimum_confirmed_groups'] and at_target >= requirement['minimum_groups_at_target_grade'] and mandatory
            levels[level] = {'eligible': eligible, 'groups_done': done,
                             'groups_required': requirement['minimum_confirmed_groups'],
                             'at_target_done': at_target,
                             'at_target_required': requirement['minimum_groups_at_target_grade'],
                             'mandatory_first': requirement['mandatory_first_n_groups'], 'groups': groups}
        current=user_test_grade(c,user_id,spec)
        can_advance=current!='Senior' and confirmed in LEVELS and LEVELS.index(confirmed)>=LEVELS.index(current)
        result[spec] = {'confirmed_grade': confirmed, 'current_grade':current,
                        'can_advance':bool(can_advance),
                        'next_test_grade':LEVELS[LEVELS.index(current)+1] if current!='Senior' else None,
                        'next_grade': target, 'levels': levels,
                        'rule_version': RULE_VERSION}
    return result


def update_grades(c, user_id):
    progress = grade_progress(c, user_id)
    for spec, entry in progress.items():
        confirmed = entry['confirmed_grade']
        for level in LEVELS:
            if confirmed and LEVELS.index(level) <= LEVELS.index(confirmed): continue
            if not entry['levels'][level]['eligible']: break
            c.execute('''INSERT INTO candidate_verified_grades VALUES(?,?,?,?,?,?)
              ON CONFLICT(user_id,specialization) DO UPDATE SET grade=excluded.grade,
              rule_version=excluded.rule_version,source_version=excluded.source_version,
              confirmed_at=excluded.confirmed_at''',
              (user_id, spec, level, RULE_VERSION, BANK_VERSION, now()))
            confirmed = level
    return grade_progress(c, user_id)


def moscow_day(clock=None):
    return (clock or datetime.now(timezone.utc)).astimezone(MOSCOW).date().isoformat()


def active_spec(c, user_id):
    row = c.execute('SELECT profile_json FROM candidate_profiles WHERE user_id=?', (user_id,)).fetchone()
    if not row: return 'Other IT'
    profile = json.loads(row[0])
    selected = profile.get('selected') or []
    if profile.get('active') in selected and profile['active'] in matrix():
        return profile['active']
    return next((s for s in selected if s in matrix()), 'Other IT')


def daily_public(c, user_id, clock=None):
    day = moscow_day(clock)
    row = c.execute('SELECT * FROM daily_sessions WHERE user_id=? AND day_msk=?', (user_id, day)).fetchone()
    if not row:
        spec = active_spec(c, user_id)
        grade = user_test_grade(c,user_id)
        bank = [(r[0],r[1]) for r in c.execute('SELECT id,topic FROM daily_questions WHERE grade=? ORDER BY id', (grade,))]
        pool = [qid for qid,_ in bank]
        if len(pool) < 3: raise HTTPException(503, 'Недостаточно ежедневных вопросов')
        rng = random.Random(hashlib.sha256(f'{user_id}:{day}:{spec}:{grade}:{DAILY_VERSION}:{DAILY_SELECTION_VERSION}'.encode()).digest())
        preferred = [qid for qid,topic in bank if topic in DAILY_TOPICS.get(spec,())]
        ids = rng.sample(preferred,min(2,len(preferred)))
        ids += rng.sample([qid for qid in pool if qid not in ids],3-len(ids))
        rng.shuffle(ids)
        c.execute('INSERT OR IGNORE INTO daily_sessions VALUES(?,?,?,?,?,?,?,?)',
                  (user_id, day, spec, grade, json.dumps(ids), None, None, None))
        row = c.execute('SELECT * FROM daily_sessions WHERE user_id=? AND day_msk=?', (user_id, day)).fetchone()
    ids = json.loads(row['question_ids_json'])
    questions = []
    for qid in ids:
        q = c.execute('SELECT * FROM daily_questions WHERE id=?', (qid,)).fetchone()
        questions.append(question_public(q))
    return {'date_msk': day, 'specialization': row['specialization'], 'grade': row['grade'],
            'completed': row['completed_at'] is not None, 'score': row['score'], 'questions': questions}


def submit_daily(c, user_id, answers, clock=None):
    session = daily_public(c, user_id, clock)
    if session['completed']: raise HTTPException(409, 'Сегодняшний тест уже завершён')
    ids = [q['id'] for q in session['questions']]
    if not isinstance(answers, dict) or set(answers) != {str(i) for i in ids}:
        raise HTTPException(422, 'Ответьте на три вопроса')
    values = []
    for qid in ids:
        row = c.execute('SELECT * FROM daily_questions WHERE id=?', (qid,)).fetchone()
        correct = json.loads(row['correct_answer_json'])
        answer = answers[str(qid)]
        kind = row['question_type']
        if kind in ('single', 'multi'):
            val = objective_score(kind, answer, correct if isinstance(correct, list) else [correct])
        elif kind == 'input_number':
            try: val = float(str(answer).replace(',', '.')) == float(correct)
            except (TypeError, ValueError): val = False
        else:
            val = isinstance(answer, str) and answer.strip().casefold() == str(correct).strip().casefold()
        values.append((float(val), row['points']))
    score = round(100 * sum(v*p for v,p in values) / sum(p for _,p in values))
    at = now()
    c.execute('UPDATE daily_sessions SET answers_json=?,score=?,completed_at=? WHERE user_id=? AND day_msk=? AND completed_at IS NULL',
              (json.dumps(answers, ensure_ascii=False), score, at, user_id, session['date_msk']))
    c.execute('INSERT INTO candidate_daily_tests VALUES(?,?,?,?)', (uuid.uuid4().hex, user_id, score, at))
    refresh_real_activity(c, user_id)
    return {'date_msk': session['date_msk'], 'score': score}


def activity_calendar(c, user_id, clock=None):
    day = moscow_day(clock)
    visits = {r[0] for r in c.execute('SELECT day_msk FROM candidate_activity_days_msk WHERE user_id=?', (user_id,))}
    tests = {r[0]: r[1] for r in c.execute('SELECT day_msk,score FROM daily_sessions WHERE user_id=? AND completed_at IS NOT NULL', (user_id,))}
    end = datetime.fromisoformat(day).date()
    return [{'date': (end - timedelta(days=offset)).isoformat(),
             'visited': (end - timedelta(days=offset)).isoformat() in visits,
             'test_score': tests.get((end - timedelta(days=offset)).isoformat())}
            for offset in range(364, -1, -1)]
