"""Create a blinded specialization and grade review set from the current database.

Run from site/: python3 -m backend.build_grade_aware_review
The review CSV intentionally contains no resume IDs, model scores, ranks or sampling strata.
"""

import argparse
import csv
import json
import random
import secrets
from collections import Counter
from pathlib import Path

import numpy as np

from .db import ROOT, connect
from .embedding_worker import DIMENSIONS, MODEL, REVISION
from .expand_eval import ADJACENT
from .ranking import SCORING_VERSION, rank_vacancy, vacancy_experience_text


BASE = ROOT / 'out' / 'four_signal_eval'
DEFAULT_OUT = BASE / 'round4_grade_aware'
SEED = 20261011
ELIGIBLE_BANDS = (('upper_rank_band', 9), ('middle_rank_band', 8), ('lower_rank_band', 8))
FIELDS = (
    'review_id', 'vacancy_name', 'vacancy_specialization_current', 'vacancy_grade_current',
    'vacancy_duties', 'vacancy_requirements', 'vacancy_stack',
    'candidate_position', 'candidate_specialization_current', 'candidate_grade_current',
    'candidate_experience_years', 'candidate_stack', 'candidate_experience_blocks',
    'vacancy_grade_corrected_optional', 'candidate_grade_corrected_optional',
    'candidate_specialization_corrected_optional', 'specialization_fit_0_1',
    'grade_fit_0_1', 'relevance_0_3', 'human_rank_1_best_optional', 'reviewer_notes',
)
REVIEW_FIELDS = FIELDS[13:]


def reviewed_ids():
    result = set()
    for name in ('eval_pairs.csv', 'round2/eval_pairs.csv', 'round3/eval_pairs.csv'):
        with (BASE / name).open(encoding='utf-8-sig', newline='') as source:
            result.update(row['resume_id'] for row in csv.DictReader(source))
    return result


def vacancy_ids():
    result = []
    for name in ('eval_pairs.csv', 'round2/eval_pairs.csv'):
        with (BASE / name).open(encoding='utf-8-sig', newline='') as source:
            result.extend(row['vacancy_id'] for row in csv.DictReader(source))
    result = list(dict.fromkeys(result))
    if len(result) != 10:
        raise ValueError('Expected ten previously reviewed vacancies')
    return result


def pick_band(ids, count, rng, used, old_ids):
    available = [rid for rid in ids if rid not in used]
    fresh = [rid for rid in available if rid not in old_ids]
    prior = [rid for rid in available if rid in old_ids]
    if len(available) < count:
        raise ValueError(f'Only {len(available)} available candidates for quota {count}')
    selected = rng.sample(fresh, min(len(fresh), count))
    if len(selected) < count:
        selected += rng.sample(prior, count - len(selected))
    used.update(selected)
    return selected


def rank_eligible(c, vacancy_id):
    return [item for item in rank_vacancy(c, vacancy_id, limit=1000)
            if not item['contactable']]


def select_eligible(ranked, rng, used, old_ids):
    ids = [row['resume_id'] for row in ranked]
    n = len(ids)
    if n < 25:
        raise ValueError(f'Only {n} currently eligible dataset candidates; need 25')
    bands = {
        'upper_rank_band': ids[:max(20, n // 10)],
        'middle_rank_band': ids[n // 3:2 * n // 3],
        'lower_rank_band': ids[2 * n // 3:],
    }
    selected = []
    for name, count in ELIGIBLE_BANDS:
        selected.extend((rid, name) for rid in pick_band(bands[name], count, rng, used, old_ids))
    return selected


def candidate_pool(c, specializations, grades, used):
    spec_marks = ','.join('?' for _ in specializations)
    grade_marks = ','.join('?' for _ in grades)
    rows = c.execute(f'''SELECT r.id,r.user_id,r.specialization,cg.grade
      FROM resumes r JOIN users u ON u.id=r.user_id
      JOIN candidate_grades cg ON cg.user_id=r.user_id
      WHERE u.origin='dataset' AND r.specialization IN ({spec_marks})
      AND cg.grade IN ({grade_marks}) ORDER BY r.id''',
      [*specializations, *grades]).fetchall()
    return [dict(row) for row in rows if row['id'] not in used]


def best_experience_scores(c, query, candidates):
    scores = {row['id']: 0.0 for row in candidates}
    for start in range(0, len(candidates), 300):
        batch = candidates[start:start + 300]
        marks = ','.join('?' for _ in batch)
        for row in c.execute(f'''SELECT w.resume_id,iv.vector FROM work_items w
          JOIN item_vectors iv ON iv.item_id=w.id
          WHERE w.resume_id IN ({marks}) AND iv.model=? AND iv.revision=?
          AND iv.task='retrieval.passage' AND iv.dimensions=?''',
          [*[item['id'] for item in batch], MODEL, REVISION, DIMENSIONS]):
            vector = np.frombuffer(row['vector'], dtype='<f4')
            if vector.shape == (DIMENSIONS,):
                scores[row['resume_id']] = max(scores[row['resume_id']],
                                               max(0.0, float(np.dot(query, vector))))
    return scores


def pick_hard(c, query, pool, count, rng, used, old_ids):
    fresh = [row for row in pool if row['id'] not in old_ids and row['id'] not in used]
    prior = [row for row in pool if row['id'] in old_ids and row['id'] not in used]
    candidates = fresh if len(fresh) >= count else fresh + prior
    if len(candidates) < count:
        raise ValueError(f'Only {len(candidates)} candidates available for hard quota {count}')
    sample = rng.sample(candidates, min(240, len(candidates)))
    scores = best_experience_scores(c, query, sample)
    choices = sorted(sample, key=lambda row: (-scores[row['id']], row['id']))[:count]
    ids = [row['id'] for row in choices]
    used.update(ids)
    return ids


def select_mismatches(c, vacancy, query, rng, used, old_ids):
    spec, grade = vacancy['specialization'], vacancy['grade']
    wrong_grade_quotas = {'Junior': (('Middle', 5),),
                          'Middle': (('Junior', 3), ('Senior', 2)),
                          'Senior': (('Middle', 5),)}[grade]
    result = []
    for alternative, count in wrong_grade_quotas:
        pool = candidate_pool(c, (spec,), (alternative,), used)
        result.extend((rid, 'same_specialization_wrong_grade') for rid in
                      pick_hard(c, query, pool, count, rng, used, old_ids))
    adjacent = ADJACENT[spec]
    for neighbor in adjacent:
        pool = candidate_pool(c, (neighbor,), (grade,), used)
        result.extend((rid, 'adjacent_specialization_same_grade') for rid in
                      pick_hard(c, query, pool, 1, rng, used, old_ids))
    other_specs = [row[0] for row in c.execute('SELECT DISTINCT specialization FROM resumes ORDER BY specialization')
                   if row[0] not in {spec, *adjacent}]
    rng.shuffle(other_specs)
    distant = 0
    for other in other_specs:
        pool = candidate_pool(c, (other,), (grade,), used)
        if not pool:
            continue
        result.extend((rid, 'distant_specialization_same_grade') for rid in
                      pick_hard(c, query, pool, 1, rng, used, old_ids))
        distant += 1
        if distant == 2:
            break
    if len(result) != 10:
        raise ValueError(f'Expected ten mismatch candidates, got {len(result)}')
    return result


def experience_text(c, resume_id):
    blocks = c.execute('''SELECT kind,title,company,start_date,end_date,description
      FROM work_items WHERE resume_id=? ORDER BY ordinal,id''', (resume_id,)).fetchall()
    if not blocks:
        return 'Выделенных блоков работы или проектов нет.'
    result = []
    for number, block in enumerate(blocks, 1):
        kind = 'Проект' if block['kind'] == 'project' else 'Работа' if block['kind'] == 'work' else 'Опыт'
        heading = f'{number}. {kind}'
        if block['title']:
            heading += f" — {block['title']}"
        if block['company']:
            heading += f" ({block['company']})"
        if block['start_date'] or block['end_date']:
            heading += f" [{block['start_date'] or '?'} — {block['end_date'] or '?'}]"
        result.append(f"{heading}\n{(block['description'] or '').strip()}")
    return '\n\n'.join(result)


def vacancy_requirements(vacancy):
    parts = []
    if vacancy['experience_requirements'] is not None:
        parts.append(f"Требуемый опыт: {vacancy['experience_requirements']} лет")
    for label, field in (('Требования к кандидату', 'position_requirements'),
                         ('Дополнительные требования', 'additional_requirements'),
                         ('Навыки', 'skills_text')):
        value = (vacancy[field] or '').strip()
        if value:
            parts.append(f'{label}: {value}')
    return '\n\n'.join(parts) or 'Требования отдельно не указаны.'


def make_blind_row(c, vacancy, resume, review_id):
    return {
        'review_id': review_id,
        'vacancy_name': vacancy['vacancy_name'],
        'vacancy_specialization_current': vacancy['specialization'],
        'vacancy_grade_current': vacancy['grade'],
        'vacancy_duties': vacancy_experience_text(vacancy),
        'vacancy_requirements': vacancy_requirements(vacancy),
        'vacancy_stack': ', '.join(json.loads(vacancy['all_technologies_json'] or '[]')),
        'candidate_position': resume['position_name'] or '',
        'candidate_specialization_current': resume['specialization'],
        'candidate_grade_current': resume['grade'],
        'candidate_experience_years': resume['experience_years'] if resume['experience_years'] is not None else '',
        'candidate_stack': ', '.join(json.loads(resume['all_technologies_json'] or '[]')),
        'candidate_experience_blocks': experience_text(c, resume['id']),
        **{field: '' for field in REVIEW_FIELDS},
    }


def make_sample(db, out):
    paths = (out / 'blind_review.csv', out / '.blind_review_key.json',
             out / 'sampling_manifest.json', out / '.model_snapshot.json', out / 'README.md')
    if any(path.exists() for path in paths):
        raise FileExistsError(f'{out} already contains review data; no files were overwritten')
    rng = random.Random(SEED)
    old_ids = reviewed_ids()
    used = set()
    vacancies = vacancy_ids()
    choices = {}
    snapshots = {}
    with connect(db) as c:
        # Reserve grade-matched candidates first; one ML / AI Middle pool is small.
        for vacancy_id in vacancies:
            ranked = rank_eligible(c, vacancy_id)
            choices[vacancy_id] = select_eligible(ranked, rng, used, old_ids)
            snapshots[vacancy_id] = {row['resume_id']: {'rank_in_full_current_output': index,
                                                         'score': row['score']}
                                     for index, row in enumerate(ranked, 1)
                                     if row['resume_id'] in {rid for rid, _ in choices[vacancy_id]}}
        for vacancy_id in vacancies:
            vacancy = c.execute('SELECT * FROM vacancies WHERE id=?', (vacancy_id,)).fetchone()
            vector = c.execute('SELECT vector FROM vacancy_query_vectors WHERE vacancy_id=?',
                               (vacancy_id,)).fetchone()
            if not vector:
                raise ValueError(f'Vacancy vector missing: {vacancy_id}')
            query = np.frombuffer(vector['vector'], dtype='<f4')
            if query.shape != (DIMENSIONS,):
                raise ValueError(f'Invalid vacancy vector: {vacancy_id}')
            choices[vacancy_id] += select_mismatches(c, vacancy, query, rng, used, old_ids)
        blind = []
        key = {}
        manifest = []
        for vacancy_id in vacancies:
            vacancy = c.execute('SELECT * FROM vacancies WHERE id=?', (vacancy_id,)).fetchone()
            selected = choices[vacancy_id]
            if len(selected) != 35:
                raise ValueError(f'Expected 35 candidates for {vacancy_id}')
            rng.shuffle(selected)
            for resume_id, stratum in selected:
                resume = c.execute('''SELECT r.*,cg.grade FROM resumes r
                  JOIN users u ON u.id=r.user_id JOIN candidate_grades cg ON cg.user_id=r.user_id
                  WHERE r.id=? AND u.origin='dataset' ''', (resume_id,)).fetchone()
                if not resume:
                    raise ValueError(f'No dataset resume and grade: {resume_id}')
                review_id = 'G-' + secrets.token_hex(6).upper()
                blind.append(make_blind_row(c, vacancy, resume, review_id))
                key[review_id] = {'vacancy_id': vacancy_id, 'resume_id': resume_id,
                                  'sampling_stratum': stratum}
            manifest.append({'vacancy_id': vacancy_id, 'vacancy_name': vacancy['vacancy_name'],
                             'vacancy_specialization': vacancy['specialization'],
                             'vacancy_grade': vacancy['grade'], 'pairs': len(selected),
                             'strata': dict(Counter(stratum for _, stratum in selected)),
                             'previously_reviewed_resumes': sum(rid in old_ids for rid, _ in selected)})
    if len(blind) != 350 or len(used) != 350 or len(key) != 350:
        raise RuntimeError('Expected 350 unique candidate pairs and review IDs')
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'blind_review.csv').open('w', encoding='utf-8-sig', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(blind)
    (out / '.blind_review_key.json').write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / '.model_snapshot.json').write_text(json.dumps({'scoring_version': SCORING_VERSION,
        'selected_eligible_ranks': snapshots}, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {'pairs': len(blind), 'vacancies': len(vacancies),
               'unique_resume_ids': len(used), 'previously_reviewed_resume_ids': len(old_ids),
               'seed': SEED, 'scoring_version': SCORING_VERSION,
               'strata': dict(Counter(item['sampling_stratum'] for item in key.values())),
               'per_vacancy': manifest,
               'selection': 'Stratified challenge set: 25 candidates passing current specialization/grade filters, '
                            '5 of same specialization with a different grade, 3 adjacent and 2 distant '
                            'specializations of the vacancy grade. Not random production traffic.'}
    (out / 'sampling_manifest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'README.md').write_text(
        '# Слепая разметка специализации и грейда\n\n'
        'Откройте `blind_review.csv`. 350 строк сгруппированы по десяти вакансиям; '
        'внутри каждой вакансии порядок случайный. Места, скоры, ID и группы отбора модели скрыты.\n\n'
        'Заполните три обязательных столбца:\n\n'
        '- `specialization_fit_0_1`: 1, если фактический опыт кандидата подходит специализации вакансии; иначе 0.\n'
        '- `grade_fit_0_1`: 1, если фактический уровень кандидата подходит уровню вакансии; иначе 0.\n'
        '- `relevance_0_3`: общая релевантность с учётом опыта, специализации, грейда и стека: '
        '0 — не подходит, 1 — частичное совпадение, 2 — хороший кандидат, 3 — отличный кандидат. '
        'Оцените её независимо от двух флагов: кандидат из смежной области или другого грейда '
        'может быть полезен, и это поможет проверить жёсткий фильтр.\n\n'
        'Необязательные поля `vacancy_grade_corrected_optional`, `candidate_grade_corrected_optional` '
        '(Junior/Middle/Senior) и `candidate_specialization_corrected_optional` заполняйте только '
        'если текущая запись БД явно ошибочна. `human_rank_1_best_optional` — места 1–35 '
        'внутри вакансии, если хотите дополнительно измерить согласие полного порядка; '
        'в этом случае заполните все 35 мест без повторов. '
        '`reviewer_notes` — комментарий. Остальные столбцы не меняйте.\n\n'
        'Верните только заполненный `blind_review.csv`.\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'fsp.sqlite3')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(make_sample(args.db, args.out), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
