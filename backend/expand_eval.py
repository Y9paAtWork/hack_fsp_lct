"""Create a larger, stratified evaluation sample without touching round one."""

import csv
import json
import random
from collections import Counter
from pathlib import Path

import numpy as np

from .blind_review import experience_text
from .db import ROOT, connect
from .embedding_worker import DIMENSIONS, MODEL, REVISION
from .ranking import rank_vacancy, vacancy_experience_text


ROUND_ONE = ROOT / 'out' / 'four_signal_eval'
OUT = ROUND_ONE / 'round2'
VACANCIES = (
    '4a8a15e6436154498a01ef9205336ece',  # ML / AI
    '5e6addc039d651a2bfdace05e987a366',  # Cybersecurity
    '2703ca61d1e057d8baf63bdb051b40e7',  # Backend
    '74229f1ab27953d5bac41aee46bcc188',  # Frontend
    '434f0c836eb6582da0fb647e1fd091d6',  # System Analysis
    '0166352dc74e510eaff1b38e9c50d6fe',  # DevOps / SRE
    '346a937506fd587f80ad21d68efe5ebf',  # QA
    '43424b59f12957f295e5b75936f05a3a',  # Data Analytics
)
ADJACENT = {
    'ML / AI': ('Data Science', 'Data Engineering', 'Data Analytics'),
    'Cybersecurity': ('System / Network', 'DevOps / SRE', 'Other IT'),
    'Backend': ('Fullstack', 'Software Engineering', 'Data Engineering'),
    'Frontend': ('Fullstack', 'Software Engineering', 'Mobile'),
    'System Analysis': ('Data Analytics', 'Software Engineering', 'Backend'),
    'DevOps / SRE': ('System / Network', 'Backend', 'Software Engineering'),
    'QA': ('Software Engineering', 'Backend', 'Frontend'),
    'Data Analytics': ('Data Science', 'Data Engineering', 'System Analysis'),
}
FIELDS = [
    'vacancy_id', 'vacancy_name', 'vacancy_specialization', 'vacancy_experience_text',
    'resume_id', 'candidate_specialization', 'position_name', 'candidate_experience_text',
    'sampling_stratum', 'relevance_0_3', 'reviewer_notes',
]


def pick(rows, count, rng, used):
    options = [row for row in rows if row not in used]
    if len(options) < count:
        raise ValueError(f'Need {count} distinct candidates; only {len(options)} available')
    selected = rng.sample(options, count)
    used.update(selected)
    return selected


def candidates_in_specialization(c, specialization, with_vectors):
    exists = '''EXISTS(SELECT 1 FROM item_vectors iv WHERE iv.user_id=r.user_id
        AND iv.model=? AND iv.revision=? AND iv.task='retrieval.passage' AND iv.dimensions=?)'''
    negation = '' if with_vectors else 'NOT '
    sql = f'''SELECT r.id FROM resumes r WHERE r.specialization=? AND {negation}{exists} ORDER BY r.id'''
    return [row[0] for row in c.execute(sql, (specialization, MODEL, REVISION, DIMENSIONS))]


def hard_adjacent_candidates(c, query, specs, used, rng):
    """Choose cross-specialization examples with similar experience, not by stack."""
    sampled = []
    for spec in specs:
        pool = candidates_in_specialization(c, spec, with_vectors=True)
        pool = [resume_id for resume_id in pool if resume_id not in used]
        sampled.extend(rng.sample(pool, min(140, len(pool))))
    if len(sampled) < 5:
        raise ValueError('Not enough adjacent-specialization resumes')
    rows = {}
    for start in range(0, len(sampled), 300):
        batch = sampled[start:start + 300]
        marks = ','.join('?' for _ in batch)
        for row in c.execute(f'SELECT id,user_id FROM resumes WHERE id IN ({marks})', batch):
            rows[row['user_id']] = row['id']
    similarity = {resume_id: 0.0 for resume_id in sampled}
    users = list(rows)
    for start in range(0, len(users), 300):
        batch = users[start:start + 300]
        marks = ','.join('?' for _ in batch)
        for row in c.execute(f'''SELECT user_id,vector FROM item_vectors
            WHERE user_id IN ({marks}) AND model=? AND revision=?
            AND task='retrieval.passage' AND dimensions=?''',
            [*batch, MODEL, REVISION, DIMENSIONS]):
            vector = np.frombuffer(row['vector'], dtype='<f4')
            if vector.shape == (DIMENSIONS,):
                resume_id = rows[row['user_id']]
                similarity[resume_id] = max(similarity[resume_id], max(0.0, float(np.dot(query, vector))))
    selected = sorted(sampled, key=lambda resume_id: (-similarity[resume_id], resume_id))[:5]
    used.update(selected)
    return selected


def distant_candidates(c, current_spec, adjacent_specs, used, rng):
    excluded = {current_spec, *adjacent_specs}
    all_specs = [row[0] for row in c.execute('SELECT DISTINCT specialization FROM resumes ORDER BY specialization')]
    distant = [spec for spec in all_specs if spec not in excluded]
    rng.shuffle(distant)
    selected = []
    for spec in distant:
        pool = candidates_in_specialization(c, spec, with_vectors=True)
        pool = [resume_id for resume_id in pool if resume_id not in used]
        if pool:
            selected.extend(pick(pool, 1, rng, used))
        if len(selected) == 5:
            return selected
    raise ValueError('Not enough distant-specialization resumes')


def make_sample(db=ROOT / 'data' / 'fsp.sqlite3', out=OUT):
    source = out / 'eval_pairs.csv'
    if source.exists():
        raise FileExistsError(f'{source} already exists; preserve the fixed sample')
    rng = random.Random(20261010 + 2)
    with (ROUND_ONE / 'eval_pairs.csv').open(encoding='utf-8-sig', newline='') as file:
        previous = list(csv.DictReader(file))
    used = {row['resume_id'] for row in previous}
    sample = []
    manifest = []
    with connect(db) as c:
        for vacancy_id in VACANCIES:
            vacancy = c.execute('SELECT * FROM vacancies WHERE id=?', (vacancy_id,)).fetchone()
            if not vacancy:
                raise ValueError(f'Unknown vacancy {vacancy_id}')
            query_row = c.execute('SELECT vector FROM vacancy_query_vectors WHERE vacancy_id=?', (vacancy_id,)).fetchone()
            if not query_row:
                raise ValueError(f'Index vacancy first: {vacancy_id}')
            query = np.frombuffer(query_row['vector'], dtype='<f4')
            if query.shape != (DIMENSIONS,):
                raise ValueError(f'Invalid vacancy vector: {vacancy_id}')
            ranked = rank_vacancy(c, vacancy_id, limit=1000)
            with_vectors = [row['resume_id'] for row in ranked if row['experience_blocks_found'] > 0]
            selected = []
            selected += [(resume_id, 'top_experience') for resume_id in pick(with_vectors[:max(30, len(with_vectors) // 10)], 8, rng, used)]
            middle = with_vectors[len(with_vectors) // 4:len(with_vectors) * 3 // 4]
            selected += [(resume_id, 'middle_experience') for resume_id in pick(middle, 5, rng, used)]
            lower = with_vectors[len(with_vectors) * 3 // 4:]
            selected += [(resume_id, 'lower_experience') for resume_id in pick(lower, 2, rng, used)]
            no_vectors = candidates_in_specialization(c, vacancy['specialization'], with_vectors=False)
            selected += [(resume_id, 'no_experience_vector') for resume_id in pick(no_vectors, 5, rng, used)]
            adjacent = ADJACENT[vacancy['specialization']]
            selected += [(resume_id, 'adjacent_specialization') for resume_id in
                         hard_adjacent_candidates(c, query, adjacent, used, rng)]
            selected += [(resume_id, 'distant_specialization') for resume_id in
                         distant_candidates(c, vacancy['specialization'], adjacent, used, rng)]
            if len(selected) != 30:
                raise RuntimeError(f'Expected 30 pairs, got {len(selected)} for {vacancy_id}')
            for resume_id, stratum in selected:
                resume = c.execute('SELECT id,specialization,position_name FROM resumes WHERE id=?', (resume_id,)).fetchone()
                sample.append({
                    'vacancy_id': vacancy_id,
                    'vacancy_name': vacancy['vacancy_name'],
                    'vacancy_specialization': vacancy['specialization'],
                    'vacancy_experience_text': vacancy_experience_text(vacancy),
                    'resume_id': resume_id,
                    'candidate_specialization': resume['specialization'],
                    'position_name': resume['position_name'] or '',
                    'candidate_experience_text': experience_text(c, resume_id),
                    'sampling_stratum': stratum,
                    'relevance_0_3': '',
                    'reviewer_notes': '',
                })
            manifest.append({
                'vacancy_id': vacancy_id, 'vacancy_name': vacancy['vacancy_name'],
                'specialization': vacancy['specialization'],
                'pairs': len(selected),
                'strata': dict(Counter(stratum for _, stratum in selected)),
            })
    out.mkdir(parents=True, exist_ok=True)
    with source.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sample)
    metadata = {'round': 2, 'new_pairs': len(sample), 'existing_pairs': len(previous),
                'total_after_review': len(sample) + len(previous),
                'selection': 'stratified challenge set; not a random sample of production traffic',
                'vacancies': manifest}
    (out / 'sampling_manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return metadata


if __name__ == '__main__':
    print(json.dumps(make_sample(), ensure_ascii=False, indent=2))
