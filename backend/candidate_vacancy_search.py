"""Rank open public vacancies against one registered candidate's experience."""

import hashlib
import json
import threading
from pathlib import Path

import numpy as np

from .db import ROOT, connect
from .embedding_worker import DIMENSIONS, MODEL, REVISION, aggregate_items, run_batch
from .index_all_vacancies import _load_encoder
from .ranking import WEIGHTS, catalog, fsp_score, refresh_real_activity, vacancy_experience_text
from .matching_requirements import usable_salary_range
from .profile import shared_technologies

_encoder = None
_encoder_lock = threading.Lock()


def ensure_experience_vectors(db, user_id, encoder=None):
    """Index only this user's pending work/project blocks on the first search."""
    with connect(db) as c:
        missing = c.execute('''SELECT count(*) FROM work_items w
            WHERE w.user_id=? AND EXISTS(SELECT 1 FROM embedding_chunks ch WHERE ch.item_id=w.id)
            AND NOT EXISTS(SELECT 1 FROM item_vectors iv WHERE iv.item_id=w.id
                AND iv.model=? AND iv.revision=? AND iv.task='retrieval.passage' AND iv.dimensions=?)''',
            (user_id, MODEL, REVISION, DIMENSIONS)).fetchone()[0]
    if not missing:
        return
    with _encoder_lock:
        with connect(db) as c:
            hashes = [row[0] for row in c.execute('''SELECT DISTINCT ch.content_sha256
                FROM embedding_chunks ch JOIN work_items w ON w.id=ch.item_id
                WHERE w.user_id=?''', (user_id,))]
            for offset in range(0, len(hashes), 400):
                aggregate_items(c, hashes[offset:offset+400], REVISION)
            c.execute('''UPDATE embedding_chunks SET status='pending',error=NULL
                WHERE item_id IN (SELECT id FROM work_items WHERE user_id=?) AND status='failed' ''',
                (user_id,))
        with connect(db) as c:
            needed = c.execute('''SELECT 1 FROM embedding_chunks ch JOIN work_items w ON w.id=ch.item_id
                WHERE w.user_id=? AND ch.status='pending' LIMIT 1''', (user_id,)).fetchone()
        if not needed:
            return
        global _encoder
        if encoder is None:
            if _encoder is None:
                _encoder = _load_encoder(ROOT.parent / 'models' / 'jina-embeddings-v3')
            encoder = _encoder
        while run_batch(encoder, db=db, batch=8, user_id=user_id):
            pass


def rank_for_candidate(c, user_id, specialization='', page=1, limit=20):
    if page < 1 or not 1 <= limit <= 100:
        raise ValueError('Invalid pagination')
    row = c.execute('SELECT profile_json FROM candidate_profiles WHERE user_id=?', (user_id,)).fetchone()
    if not row:
        raise ValueError('Candidate profile not found')
    profile = json.loads(row[0])
    selected = list(dict.fromkeys(profile.get('selected') or []))
    if specialization and specialization not in selected:
        raise ValueError('Специализация не выбрана в профиле')
    specs = [specialization] if specialization else selected
    empty = {'items': [], 'total': 0, 'page': page, 'limit': limit,
             'weights': WEIGHTS, 'scoring_version': 'candidate-vacancies-grade-v2',
             'experience_blocks': 0, 'experience_pending': 0, 'stale_vacancies': 0}
    if not specs:
        return empty

    blocks = {spec: [] for spec in specs}
    for item in c.execute('''SELECT w.id,w.specialization,w.title,w.kind,iv.vector
        FROM work_items w JOIN item_vectors iv ON iv.item_id=w.id
        WHERE w.user_id=? AND iv.model=? AND iv.revision=?
          AND iv.task='retrieval.passage' AND iv.dimensions=?''',
        (user_id, MODEL, REVISION, DIMENSIONS)):
        if item['specialization'] in blocks:
            vector = np.frombuffer(item['vector'], dtype='<f4')
            if vector.shape == (DIMENSIONS,) and np.isfinite(vector).all():
                blocks[item['specialization']].append((item['id'], item['title'], item['kind'], vector))
    pending = c.execute('''SELECT count(*) FROM work_items w
        WHERE w.user_id=? AND w.specialization IN ('''+','.join('?' for _ in specs)+''')
          AND EXISTS(SELECT 1 FROM embedding_chunks ch WHERE ch.item_id=w.id)
          AND NOT EXISTS(SELECT 1 FROM item_vectors iv WHERE iv.item_id=w.id
            AND iv.model=? AND iv.revision=? AND iv.task='retrieval.passage' AND iv.dimensions=?)''',
        [user_id, *specs, MODEL, REVISION, DIMENSIONS]).fetchone()[0]

    names,catalog_hash=catalog()
    name_index={name:i for i,name in enumerate(names)}
    grade_vectors={}
    for row in c.execute('''SELECT grade,catalog_sha256,dimensions,vector
      FROM candidate_skill_grade_vectors WHERE user_id=?''',(user_id,)):
        if row['catalog_sha256']==catalog_hash and row['dimensions']==len(names):
            grade_vectors[row['grade']]=np.frombuffer(row['vector'],dtype='<f4')
    shared_stack=set(shared_technologies(profile))
    stacks = {spec: shared_stack for spec in specs}
    grades = {r['specialization']:r['current_grade'] for r in c.execute(
        'SELECT specialization,current_grade FROM candidate_specialization_grades WHERE user_id=?',(user_id,))}
    salary_row=c.execute('SELECT expected_salary_min FROM resumes WHERE user_id=?',(user_id,)).fetchone()
    expected_salary_min=salary_row[0] if salary_row else None
    refresh_real_activity(c, user_id)
    activity_row = c.execute('SELECT activity_score FROM candidate_activity WHERE user_id=?',
                             (user_id,)).fetchone()
    activity = float(activity_row[0]) if activity_row else 0.0
    achievements = c.execute('''SELECT level,stage,place FROM candidate_fsp_achievements
        WHERE user_id=? AND source!='synthetic_demo' ''', (user_id,)).fetchall()
    fsp = fsp_score(achievements)

    marks = ','.join('?' for _ in specs)
    technologies = {}
    for tech in c.execute('''SELECT vt.vacancy_id,vt.technology FROM vacancy_technologies vt
        JOIN vacancies v ON v.id=vt.vacancy_id
        WHERE v.visibility='public' AND v.status='open' AND v.specialization IN ('''+marks+')', specs):
        technologies.setdefault(tech['vacancy_id'], []).append(tech['technology'])

    results = []
    stale = 0
    for vacancy in c.execute('''SELECT v.id,v.vacancy_name,v.specialization,v.region,v.description,
        v.responsibilities,v.position_requirements,v.all_technologies_json,v.employer_id,
        v.grade,v.salary_min,v.salary_max,v.required_technologies_json,
        v.created_at,ep.company_name,q.model,q.revision,q.task,q.dimensions,q.vector,q.text_sha256
        FROM vacancies v LEFT JOIN vacancy_query_vectors q ON q.vacancy_id=v.id
        LEFT JOIN employer_profiles ep ON ep.user_id=v.employer_id
        WHERE v.visibility='public' AND v.status='open' AND v.specialization IN ('''+marks+')', specs):
        if vacancy['grade']!=grades.get(vacancy['specialization']):
            continue
        mandatory=set(json.loads(vacancy['required_technologies_json'] or '[]'))
        if not mandatory.issubset(stacks[vacancy['specialization']]):
            continue
        text = vacancy_experience_text(vacancy)
        if (vacancy['model'] != MODEL or vacancy['revision'] != REVISION or
                vacancy['task'] != 'retrieval.query' or vacancy['dimensions'] != DIMENSIONS or
                not text or vacancy['text_sha256'] != hashlib.sha256(text.encode()).hexdigest()):
            stale += 1
            continue
        query = np.frombuffer(vacancy['vector'], dtype='<f4')
        if query.shape != (DIMENSIONS,) or not np.isfinite(query).all():
            stale += 1
            continue
        candidates = blocks[vacancy['specialization']]
        best = max(((max(0.0, min(1.0, float(np.dot(query, vector)))), item_id, title, kind)
                    for item_id, title, kind, vector in candidates), default=None)
        experience = best[0] if best else 0.0
        requested = technologies.get(vacancy['id'], [])
        grade_vector=grade_vectors.get(vacancy['grade'])
        skill=(sum(float(grade_vector[name_index[tech]])
                   for tech in requested if grade_vector is not None and tech in name_index)
               / len(requested)) if requested else 0.0
        components = {'experience': experience, 'skills': skill, 'activity': activity, 'fsp': fsp}
        score = sum(WEIGHTS[key] * components[key] for key in WEIGHTS)
        results.append({'id': vacancy['id'], 'vacancy_name': vacancy['vacancy_name'],
                        'specialization': vacancy['specialization'], 'region': vacancy['region'],
                        'description': vacancy['description'], 'responsibilities': vacancy['responsibilities'],
                        'position_requirements': vacancy['position_requirements'],
                        'all_technologies_json': vacancy['all_technologies_json'],
                        'grade':vacancy['grade'],'salary_min':vacancy['salary_min'],'salary_max':vacancy['salary_max'],
                        'required_technologies':sorted(mandatory),
                        'salary_mismatch':bool(usable_salary_range(vacancy['salary_min'],vacancy['salary_max']) and expected_salary_min is not None and expected_salary_min>vacancy['salary_max']),
                        'employer_id': vacancy['employer_id'], 'company_name': vacancy['company_name'],
                        'score': round(score, 6),
                        'components': {key: round(value, 6) for key, value in components.items()},
                        'best_experience': {'id': best[1], 'title': best[2], 'kind': best[3]} if best else None,
                        'matched_technologies': sum(tech in stacks[vacancy['specialization']]
                                                    for tech in requested),
                        'requested_technologies': len(requested)})
    results.sort(key=lambda item: (-item['score'], -item['components']['experience'], item['id']))
    return {'items': results[(page-1)*limit:page*limit], 'total': len(results),
            'page': page, 'limit': limit, 'weights': WEIGHTS,
            'scoring_version': 'candidate-vacancies-v1',
            'experience_blocks': sum(map(len, blocks.values())),
            'experience_pending': pending, 'stale_vacancies': stale}
