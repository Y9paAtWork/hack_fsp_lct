"""Create a 100-candidate blind-evaluation source for one existing vacancy."""

import argparse
import csv
import json
import random
from collections import Counter
from pathlib import Path

import numpy as np

from .blind_review import experience_text
from .db import ROOT, connect
from .embedding_worker import DIMENSIONS, MODEL, REVISION
from .expand_eval import ADJACENT, FIELDS, candidates_in_specialization, pick
from .ranking import rank_vacancy, vacancy_experience_text


ROOT_OUT = ROOT / 'out' / 'four_signal_eval'
OUT = ROOT_OUT / 'round3'
VACANCY_ID = '4a8a15e6436154498a01ef9205336ece'  # AI Engineer
QUOTAS = {
    'same_specialization_with_experience': 70,
    'same_specialization_without_experience': 10,
    'adjacent_specialization': 10,
    'distant_specialization': 10,
}


def previous_resume_ids():
    ids = set()
    for path in (ROOT_OUT / 'eval_pairs.csv', ROOT_OUT / 'round2' / 'eval_pairs.csv'):
        with path.open(encoding='utf-8-sig', newline='') as file:
            ids.update(row['resume_id'] for row in csv.DictReader(file))
    return ids


def adjacent_candidates(c, query, specializations, used, rng, count):
    """Take nearest experience vectors from a reproducible pool in each adjacent field."""
    quotas = [count // len(specializations)] * len(specializations)
    for i in range(count % len(specializations)):
        quotas[i] += 1
    chosen = []
    for specialization, quota in zip(specializations, quotas):
        pool = [rid for rid in candidates_in_specialization(c, specialization, True)
                if rid not in used]
        sampled = rng.sample(pool, min(140, len(pool)))
        if len(sampled) < quota:
            raise ValueError(f'Not enough adjacent candidates in {specialization}')
        user_to_resume = {}
        for start in range(0, len(sampled), 300):
            batch = sampled[start:start + 300]
            marks = ','.join('?' for _ in batch)
            for row in c.execute(f'SELECT id,user_id FROM resumes WHERE id IN ({marks})', batch):
                user_to_resume[row['user_id']] = row['id']
        similarity = {rid: 0.0 for rid in sampled}
        users = list(user_to_resume)
        for start in range(0, len(users), 300):
            batch = users[start:start + 300]
            marks = ','.join('?' for _ in batch)
            for row in c.execute(f'''SELECT user_id,vector FROM item_vectors
                WHERE user_id IN ({marks}) AND model=? AND revision=?
                AND task='retrieval.passage' AND dimensions=?''',
                [*batch, MODEL, REVISION, DIMENSIONS]):
                vector = np.frombuffer(row['vector'], dtype='<f4')
                if vector.shape == (DIMENSIONS,):
                    rid = user_to_resume[row['user_id']]
                    similarity[rid] = max(similarity[rid], max(0.0, float(np.dot(query, vector))))
        selected = sorted(sampled, key=lambda rid: (-similarity[rid], rid))[:quota]
        chosen.extend(selected)
        used.update(selected)
    return chosen


def distant_candidates(c, excluded_specializations, used, rng, count):
    specs = [row[0] for row in c.execute('SELECT DISTINCT specialization FROM resumes ORDER BY specialization')
             if row[0] not in excluded_specializations]
    rng.shuffle(specs)
    chosen = []
    for spec in specs:
        pool = [rid for rid in candidates_in_specialization(c, spec, True) if rid not in used]
        if pool:
            chosen.extend(pick(pool, 1, rng, used))
        if len(chosen) == count:
            return chosen
    raise ValueError(f'Only {len(chosen)} distant specializations available; need {count}')


def make_sample(db=ROOT / 'data' / 'fsp.sqlite3', out=OUT):
    source = out / 'eval_pairs.csv'
    if any((out / name).exists() for name in
           ('eval_pairs.csv', 'sampling_manifest.json', 'blind_review.csv', '.blind_review_key.json')):
        raise FileExistsError(f'{out} already contains evaluation data; preserve the fixed sample')
    rng = random.Random(20261010 + 3)
    used = previous_resume_ids()
    prior_count = len(used)
    with connect(db) as c:
        vacancy = c.execute('SELECT * FROM vacancies WHERE id=?', (VACANCY_ID,)).fetchone()
        if not vacancy:
            raise ValueError(f'Unknown vacancy {VACANCY_ID}')
        q = c.execute('SELECT vector FROM vacancy_query_vectors WHERE vacancy_id=?', (VACANCY_ID,)).fetchone()
        if not q:
            raise ValueError('Index the vacancy first')
        query = np.frombuffer(q['vector'], dtype='<f4')
        if query.shape != (DIMENSIONS,):
            raise ValueError('Invalid vacancy query vector')
        ranked = rank_vacancy(c, VACANCY_ID, limit=1000)
        with_experience = [row['resume_id'] for row in ranked
                           if row['experience_blocks_found'] > 0 and row['resume_id'] not in used]
        without_experience = [rid for rid in candidates_in_specialization(
            c, vacancy['specialization'], False) if rid not in used]
        selected = []
        selected += [(rid, 'same_specialization_with_experience') for rid in
                     pick(with_experience, QUOTAS['same_specialization_with_experience'], rng, used)]
        selected += [(rid, 'same_specialization_without_experience') for rid in
                     pick(without_experience, QUOTAS['same_specialization_without_experience'], rng, used)]
        adjacent_specs = ADJACENT[vacancy['specialization']]
        selected += [(rid, 'adjacent_specialization') for rid in adjacent_candidates(
            c, query, adjacent_specs, used, rng, QUOTAS['adjacent_specialization'])]
        selected += [(rid, 'distant_specialization') for rid in distant_candidates(
            c, {vacancy['specialization'], *adjacent_specs}, used, rng,
            QUOTAS['distant_specialization'])]
        rows = []
        for resume_id, stratum in selected:
            resume = c.execute('SELECT specialization,position_name FROM resumes WHERE id=?',
                               (resume_id,)).fetchone()
            rows.append({
                'vacancy_id': VACANCY_ID,
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
    if len(rows) != 100 or len({row['resume_id'] for row in rows}) != 100:
        raise RuntimeError('Expected exactly 100 distinct candidates')
    out.mkdir(parents=True, exist_ok=True)
    with source.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        'round': 3, 'vacancy_id': VACANCY_ID, 'vacancy_name': rows[0]['vacancy_name'],
        'new_pairs': len(rows), 'previously_reviewed_resumes_excluded': prior_count,
        'strata': dict(Counter(row['sampling_stratum'] for row in rows)),
        'selection': 'stratified sample for one vacancy; not a random production sample',
        'seed': 20261013,
    }
    (out / 'sampling_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'fsp.sqlite3')
    parser.add_argument('--out', type=Path, default=OUT)
    args = parser.parse_args()
    print(json.dumps(make_sample(args.db, args.out), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
