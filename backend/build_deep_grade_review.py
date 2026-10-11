"""Build a larger blind review: four vacancies and 150 distinct candidates each.

Run from site/: python3 -m backend.build_deep_grade_review
Only blind_review.csv is intended for the reviewer. IDs, ranks, scores and sampling
strata remain in local sidecar files so the returned review can be evaluated.
"""

import argparse
import csv
import json
import random
import secrets
from collections import Counter
from pathlib import Path

import numpy as np

from .build_grade_aware_review import (
    BASE, FIELDS, REVIEW_FIELDS, candidate_pool, make_blind_row,
    pick_hard, rank_eligible, reviewed_ids,
)
from .db import ROOT, connect
from .embedding_worker import DIMENSIONS
from .expand_eval import ADJACENT
from .ranking import SCORING_VERSION


OUT = BASE / 'round5_deep_grade_aware'
SEED = 20261012
VACANCY_IDS = (
    '2703ca61d1e057d8baf63bdb051b40e7',  # Backend / Middle
    '74229f1ab27953d5bac41aee46bcc188',  # Frontend / Middle
    '434f0c836eb6582da0fb647e1fd091d6',  # System Analysis / Middle
    '346a937506fd587f80ad21d68efe5ebf',  # QA / Senior
)
ELIGIBLE_PER_BAND = 40
MISMATCHES_PER_VACANCY = 30
PER_VACANCY = 150


def previously_reviewed_ids():
    ids = reviewed_ids()
    key = BASE / 'round4_grade_aware' / '.blind_review_key.json'
    ids.update(row['resume_id'] for row in json.loads(key.read_text(encoding='utf-8')).values())
    return ids


def take_fresh(ids, count, rng, used, previously_reviewed):
    choices = [item for item in ids if item not in used and item not in previously_reviewed]
    if len(choices) < count:
        raise ValueError(f'Need {count} never-reviewed candidates; only {len(choices)} available')
    selected = rng.sample(choices, count)
    used.update(selected)
    return selected


def select_eligible(ranked, rng, used, previously_reviewed):
    ids = [row['resume_id'] for row in ranked]
    if len(ids) < PER_VACANCY:
        raise ValueError(f'Only {len(ids)} ranked candidates for a 150-person vacancy sample')
    first, second = len(ids) // 3, 2 * len(ids) // 3
    bands = (
        ('upper_rank_band', ids[:first]),
        ('middle_rank_band', ids[first:second]),
        ('lower_rank_band', ids[second:]),
    )
    return [(rid, name) for name, band in bands
            for rid in take_fresh(band, ELIGIBLE_PER_BAND, rng, used, previously_reviewed)]


def hard_fresh(c, query, pool, count, rng, used, previously_reviewed):
    fresh_pool = [row for row in pool if row['id'] not in previously_reviewed]
    return pick_hard(c, query, fresh_pool, count, rng, used, previously_reviewed)


def select_mismatches(c, vacancy, query, rng, used, previously_reviewed):
    spec, grade = vacancy['specialization'], vacancy['grade']
    wrong_grades = (('Junior', 8), ('Senior', 7)) if grade == 'Middle' else (('Middle', 15),)
    selected = []
    for alternate, count in wrong_grades:
        pool = candidate_pool(c, (spec,), (alternate,), used)
        selected += [(rid, 'same_specialization_wrong_grade') for rid in
                     hard_fresh(c, query, pool, count, rng, used, previously_reviewed)]
    for neighbor, count in zip(ADJACENT[spec], (4, 3, 3)):
        pool = candidate_pool(c, (neighbor,), (grade,), used)
        selected += [(rid, 'adjacent_specialization_same_grade') for rid in
                     hard_fresh(c, query, pool, count, rng, used, previously_reviewed)]
    other_specs = [row[0] for row in c.execute(
        'SELECT DISTINCT specialization FROM resumes ORDER BY specialization')
        if row[0] not in {spec, *ADJACENT[spec]}]
    rng.shuffle(other_specs)
    for other in other_specs:
        pool = candidate_pool(c, (other,), (grade,), used)
        if not any(row['id'] not in previously_reviewed for row in pool):
            continue
        rid = hard_fresh(c, query, pool, 1, rng, used, previously_reviewed)[0]
        selected.append((rid, 'distant_specialization_same_grade'))
        if len(selected) == MISMATCHES_PER_VACANCY:
            break
    if len(selected) != MISMATCHES_PER_VACANCY:
        raise ValueError(f'Expected {MISMATCHES_PER_VACANCY} mismatches, got {len(selected)}')
    return selected


def make_sample(db, out):
    paths = (out / 'blind_review.csv', out / '.blind_review_key.json',
             out / 'sampling_manifest.json', out / '.model_snapshot.json', out / 'README.md')
    if any(path.exists() for path in paths):
        raise FileExistsError(f'{out} already contains review data; no files were overwritten')
    rng = random.Random(SEED)
    previously_reviewed = previously_reviewed_ids()
    used = set()
    choices = {}
    snapshots = {}
    blind = []
    key = {}
    manifest = []
    with connect(db) as c:
        for vacancy_id in VACANCY_IDS:
            ranked = rank_eligible(c, vacancy_id)
            choices[vacancy_id] = select_eligible(ranked, rng, used, previously_reviewed)
            chosen = {rid for rid, _ in choices[vacancy_id]}
            snapshots[vacancy_id] = {
                row['resume_id']: {'rank_in_full_current_output': position,
                                   'score': row['score']}
                for position, row in enumerate(ranked, 1) if row['resume_id'] in chosen
            }
        for vacancy_id in VACANCY_IDS:
            vacancy = c.execute('SELECT * FROM vacancies WHERE id=?', (vacancy_id,)).fetchone()
            vector = c.execute('SELECT vector FROM vacancy_query_vectors WHERE vacancy_id=?',
                               (vacancy_id,)).fetchone()
            if vector is None:
                raise ValueError(f'Vacancy vector missing: {vacancy_id}')
            query = np.frombuffer(vector['vector'], dtype='<f4')
            if query.shape != (DIMENSIONS,):
                raise ValueError(f'Invalid vacancy vector: {vacancy_id}')
            choices[vacancy_id].extend(select_mismatches(
                c, vacancy, query, rng, used, previously_reviewed))
        for vacancy_id in VACANCY_IDS:
            vacancy = c.execute('SELECT * FROM vacancies WHERE id=?', (vacancy_id,)).fetchone()
            selected = choices[vacancy_id]
            if len(selected) != PER_VACANCY:
                raise ValueError(f'Expected {PER_VACANCY} candidates for {vacancy_id}')
            rng.shuffle(selected)
            for resume_id, stratum in selected:
                resume = c.execute('''SELECT r.*,cg.grade FROM resumes r
                  JOIN users u ON u.id=r.user_id JOIN candidate_grades cg ON cg.user_id=r.user_id
                  WHERE r.id=? AND u.origin='dataset' ''', (resume_id,)).fetchone()
                if not resume:
                    raise ValueError(f'No dataset resume and grade: {resume_id}')
                review_id = 'D-' + secrets.token_hex(6).upper()
                blind.append(make_blind_row(c, vacancy, resume, review_id))
                key[review_id] = {'vacancy_id': vacancy_id, 'resume_id': resume_id,
                                  'sampling_stratum': stratum}
            manifest.append({'vacancy_id': vacancy_id, 'vacancy_name': vacancy['vacancy_name'],
                             'vacancy_specialization': vacancy['specialization'],
                             'vacancy_grade': vacancy['grade'], 'pairs': len(selected),
                             'strata': dict(Counter(stratum for _, stratum in selected)),
                             'previously_reviewed_resumes': sum(
                                 rid in previously_reviewed for rid, _ in selected)})
    if len(blind) != 4 * PER_VACANCY or len(used) != len(blind) or len(key) != len(blind):
        raise RuntimeError('Expected 600 distinct candidate pairs and review IDs')
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'blind_review.csv').open('w', encoding='utf-8-sig', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(blind)
    (out / '.blind_review_key.json').write_text(
        json.dumps(key, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / '.model_snapshot.json').write_text(json.dumps({
        'scoring_version': SCORING_VERSION, 'selected_eligible_ranks': snapshots,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {'pairs': len(blind), 'vacancies': len(VACANCY_IDS),
               'candidates_per_vacancy': PER_VACANCY,
               'unique_resume_ids': len(used),
               'previously_reviewed_resume_ids_in_sample': len(used & previously_reviewed),
               'seed': SEED, 'scoring_version': SCORING_VERSION,
               'strata': dict(Counter(item['sampling_stratum'] for item in key.values())),
               'per_vacancy': manifest,
               'selection': 'Stratified challenge set: 120 candidates passing current specialization/grade '
                            'filters (40 from each rank tercile), 15 same-specialization wrong-grade, '
                            '10 adjacent-specialization same-grade, and 5 distant-specialization '
                            'same-grade candidates per vacancy. No previously reviewed resumes. '
                            'Not random production traffic.'}
    (out / 'sampling_manifest.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'README.md').write_text(
        '# Слепая разметка: 4 вакансии × 150 кандидатов\n\n'
        'Откройте `blind_review.csv`. В нём 600 строк, по 150 на вакансию. '
        'Внутри каждой вакансии кандидаты перемешаны. Скрытые ID резюме, места, '
        'оценки модели и группы отбора в файле отсутствуют.\n\n'
        'Заполните обязательные поля:\n\n'
        '- `specialization_fit_0_1`: 1, если реальный опыт соответствует специализации вакансии, иначе 0.\n'
        '- `grade_fit_0_1`: 1, если уровень кандидата подходит вакансии, иначе 0.\n'
        '- `relevance_0_3`: общая оценка: 0 — не подходит, 1 — частично подходит, '
        '2 — хороший кандидат, 3 — отличный кандидат. Оценивайте её независимо от двух флагов.\n\n'
        'Необязательные поля с исправлениями грейда или специализации заполняйте только '
        'при явной ошибке. `human_rank_1_best_optional` можно не заполнять; если нужна '
        'проверка полного порядка, проставьте уникальные места 1–150 внутри каждой '
        'вакансии. `reviewer_notes` — свободный комментарий.\n\n'
        'Не меняйте исходные колонки и `review_id`. Верните заполненный CSV, '
        'оставив скрытые служебные файлы на этом устройстве.\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'fsp.sqlite3')
    parser.add_argument('--out', type=Path, default=OUT)
    args = parser.parse_args()
    print(json.dumps(make_sample(args.db, args.out), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
