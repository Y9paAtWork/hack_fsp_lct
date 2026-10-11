"""Prepare four-signal demo data, index vacancies and run honest offline checks."""

import argparse
import csv
import json
import math
import random
import statistics
import time
from pathlib import Path

from .db import ROOT, connect, init_db
from .embedding_worker import LocalJina, REVISION, normalize_vectors
from .ranking import (WEIGHTS, SCORING_VERSION, catalog, load_ranked,
                      populate_demo, rank_vacancy, store_vacancy_query_vector,
                      vacancy_experience_text)

DEFAULT_OUT = ROOT / 'out' / 'four_signal_eval'
WEIGHT_SETS = {
    'original_35_35_15_15': {'experience': .35, 'skills': .35, 'activity': .15, 'fsp': .15},
    'balanced_50_40_05_05': {'experience': .50, 'skills': .40, 'activity': .05, 'fsp': .05},
    'experience_55_35_05_05': {'experience': .55, 'skills': .35, 'activity': .05, 'fsp': .05},
    'experience_60_30_05_05': {'experience': .60, 'skills': .30, 'activity': .05, 'fsp': .05},
    'experience_70_25_025_025': {'experience': .70, 'skills': .25, 'activity': .025, 'fsp': .025},
    'experience_80_20_00_00': {'experience': .80, 'skills': .20, 'activity': 0, 'fsp': 0},
}


def select_vacancies(c):
    result = []
    for spec in ('ML / AI', 'Cybersecurity'):
        row = c.execute('''SELECT id FROM vacancies WHERE specialization=?
            AND length(trim(coalesce(responsibilities,'')))>500
            ORDER BY length(responsibilities) DESC, id LIMIT 1''', (spec,)).fetchone()
        if row:
            result.append(row[0])
    return result


def index_vacancies(db, vacancy_ids, model_path=None):
    init_db(db)
    with connect(db) as c:
        pairs = []
        for vid in vacancy_ids:
            row = c.execute('SELECT id,vacancy_name,responsibilities,description FROM vacancies WHERE id=?', (vid,)).fetchone()
            if not row:
                raise ValueError(f'Unknown vacancy {vid}')
            text = vacancy_experience_text(row)
            if not text:
                raise ValueError(f'Vacancy {vid} has no experience duties')
            pairs.append((vid, text))
    source = Path(model_path) if model_path else ROOT.parent / 'models' / 'jina-embeddings-v3'
    if source.is_dir():
        metadata = source / '.cache' / 'huggingface' / 'download' / 'model.safetensors.metadata'
        if not metadata.exists() or metadata.read_text().splitlines()[0] != REVISION:
            raise ValueError('Local Jina weights do not match the revision of stored experience vectors')
    load_started = time.perf_counter()
    encoder = LocalJina(model_path=str(source) if source.is_dir() else None,
                        task='retrieval.query')
    load_seconds = time.perf_counter() - load_started
    started = time.perf_counter()
    vectors = normalize_vectors(encoder.encode([text for _, text in pairs]), len(pairs))
    elapsed = time.perf_counter() - started
    with connect(db) as c:
        for (vid, _), vector in zip(pairs, vectors):
            store_vacancy_query_vector(c, vid, vector)
    return {'vacancies_indexed': len(pairs), 'model_load_seconds': round(load_seconds, 3),
            'encode_seconds': round(elapsed, 3),
            'model_path': str(source) if source.is_dir() else 'Hugging Face cache'}


def make_eval_pairs(c, vacancy_ids, out):
    """Make a deterministic labeling queue; no fabricated relevance labels."""
    rng = random.Random(20261010)
    fields = ['vacancy_id', 'vacancy_name', 'vacancy_specialization',
              'vacancy_experience_text', 'resume_id', 'candidate_specialization',
              'position_name', 'candidate_experience_text', 'relevance_0_3', 'reviewer_notes']
    rows = []
    for vid in vacancy_ids:
        vacancy = c.execute('SELECT * FROM vacancies WHERE id=?', (vid,)).fetchone()
        same_with = [r[0] for r in c.execute('''SELECT r.id FROM resumes r WHERE r.specialization=?
            AND EXISTS(SELECT 1 FROM item_vectors iv WHERE iv.user_id=r.user_id)
            ORDER BY r.id''', (vacancy['specialization'],))]
        same_without = [r[0] for r in c.execute('''SELECT r.id FROM resumes r WHERE r.specialization=?
            AND NOT EXISTS(SELECT 1 FROM item_vectors iv WHERE iv.user_id=r.user_id)
            ORDER BY r.id''', (vacancy['specialization'],))]
        other = [r[0] for r in c.execute('''SELECT r.id FROM resumes r WHERE r.specialization<>?
            AND EXISTS(SELECT 1 FROM item_vectors iv WHERE iv.user_id=r.user_id)
            ORDER BY r.id''', (vacancy['specialization'],))]
        ids = (rng.sample(same_with, min(15, len(same_with))) +
               rng.sample(same_without, min(5, len(same_without))) +
               rng.sample(other, min(10, len(other))))
        for rid in ids:
            resume = c.execute('SELECT id,user_id,specialization,position_name FROM resumes WHERE id=?', (rid,)).fetchone()
            blocks = [r[0] for r in c.execute('''SELECT description FROM work_items WHERE user_id=?
                ORDER BY ordinal LIMIT 3''', (resume['user_id'],))]
            rows.append({'vacancy_id': vid, 'vacancy_name': vacancy['vacancy_name'],
                'vacancy_specialization': vacancy['specialization'],
                'vacancy_experience_text': vacancy_experience_text(vacancy),
                'resume_id': rid, 'candidate_specialization': resume['specialization'],
                'position_name': resume['position_name'],
                'candidate_experience_text': '\n---\n'.join(blocks),
                'relevance_0_3': '', 'reviewer_notes': ''})
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return {'pairs': len(rows), 'path': str(out), 'unlabeled': len(rows)}


def has_reviewed_pairs(path):
    if not path.exists():
        return False
    with path.open(encoding='utf-8-sig', newline='') as file:
        return any(row.get('relevance_0_3', '').strip() or row.get('reviewer_notes', '').strip()
                   for row in csv.DictReader(file))


def dcg(relevances, k):
    return sum((2**value - 1) / math.log2(i + 2) for i, value in enumerate(relevances[:k]))


def evaluate_labels(c, path):
    with Path(path).open(encoding='utf-8-sig', newline='') as file:
        pairs = list(csv.DictReader(file))
    if not pairs:
        raise ValueError('Evaluation dataset is empty')
    if any(row['relevance_0_3'].strip() not in ('0', '1', '2', '3') for row in pairs):
        return {'quality_status': 'awaiting_human_labels', 'labeled': sum(
            row['relevance_0_3'].strip() in ('0', '1', '2', '3') for row in pairs),
            'total': len(pairs), 'ndcg_at_10': None, 'recall_at_20': None}
    grouped = {}
    for row in pairs:
        grouped.setdefault(row['vacancy_id'], {})[row['resume_id']] = int(row['relevance_0_3'])
    per_vacancy = []
    for vid, labels in grouped.items():
        ranked = rank_vacancy(c, vid, limit=1000, candidate_ids=list(labels))
        judged = [labels[r['resume_id']] for r in ranked]
        # Candidates excluded by specialization are correctly placed after eligible candidates.
        ranked_ids = {r['resume_id'] for r in ranked}
        judged.extend(labels[rid] for rid in labels if rid not in ranked_ids)
        ideal = sorted(labels.values(), reverse=True)
        positives = sum(v > 0 for v in labels.values())
        per_vacancy.append({'vacancy_id': vid,
            'ndcg_at_10': dcg(judged, 10) / dcg(ideal, 10) if dcg(ideal, 10) else None,
            'recall_at_20': sum(v > 0 for v in judged[:20]) / positives if positives else None,
            'mrr': next((1 / (i + 1) for i, v in enumerate(judged) if v > 0), 0.0)})
    return {'quality_status': 'human_labeled', 'per_vacancy': per_vacancy,
            'macro_ndcg_at_10': sum(x['ndcg_at_10'] for x in per_vacancy if x['ndcg_at_10'] is not None) /
              max(1, sum(x['ndcg_at_10'] is not None for x in per_vacancy)),
            'macro_recall_at_20': sum(x['recall_at_20'] for x in per_vacancy if x['recall_at_20'] is not None) /
              max(1, sum(x['recall_at_20'] is not None for x in per_vacancy))}


def benchmark(db, vacancy_ids, out):
    out.mkdir(parents=True, exist_ok=True)
    results = []
    markdown = ['# Четырёхсигнальный подбор — диагностический прогон', '',
                ('Веса: опыт {experience}, технологии {skills}, активность {activity}, ФСП {fsp}.').format(**WEIGHTS),
                'Тесты, активность и ФСП здесь синтетические; оценка качества требует ручной разметки.', '']
    with connect(db) as c:
        for vid in vacancy_ids:
            vacancy = c.execute('SELECT vacancy_name,specialization FROM vacancies WHERE id=?', (vid,)).fetchone()
            started = time.perf_counter()
            rows = rank_vacancy(c, vid, limit=20)
            seconds = time.perf_counter() - started
            repeats = []
            for _ in range(5):
                started = time.perf_counter()
                rank_vacancy(c, vid, limit=20)
                repeats.append(time.perf_counter() - started)
            warm_median = statistics.median(repeats)
            warm_p95 = sorted(repeats)[-1]
            count = c.execute('SELECT count(*) FROM resumes WHERE specialization=?', (vacancy['specialization'],)).fetchone()[0]
            with_vectors = c.execute('''SELECT count(*) FROM resumes r WHERE r.specialization=?
                AND EXISTS(SELECT 1 FROM item_vectors iv WHERE iv.user_id=r.user_id)''',
                (vacancy['specialization'],)).fetchone()[0]
            coverage = sum(r['experience_blocks_found'] > 0 for r in rows)
            results.append({'vacancy_id': vid, 'vacancy_name': vacancy['vacancy_name'],
                'specialization': vacancy['specialization'], 'same_specialization_candidates': count,
                'candidates_with_experience_vectors': with_vectors,
                'first_ranking_seconds': round(seconds, 4),
                'warm_median_seconds': round(warm_median, 4),
                'warm_max_of_five_seconds': round(warm_p95, 4),
                'top20_experience_coverage': coverage,
                'top20': rows})
            markdown += [f"## {vacancy['vacancy_name']} — {vacancy['specialization']}",
                         '', f'Кандидатов нужной специализации: {count}; с вектором опыта: {with_vectors}; без него: {count-with_vectors}; первое ранжирование: {seconds:.3f} с; медиана 5 повторов: {warm_median:.3f} с.', '',
                         '| # | Резюме | Итог | Опыт | Навыки | Активность | ФСП |',
                         '|---:|---|---:|---:|---:|---:|---:|']
            for i, row in enumerate(rows, 1):
                s = row['components']
                markdown.append(f"| {i} | {row['resume_id']} | {row['score']:.3f} | "
                    f"{s['experience']:.3f} | {s['skills']:.3f} | {s['activity']:.3f} | {s['fsp']:.3f} |")
            markdown.append('')
        eval_path = out / 'eval_pairs.csv'
        if not has_reviewed_pairs(eval_path):
            dataset = make_eval_pairs(c, vacancy_ids, eval_path)
        else:
            dataset = {'path': str(eval_path), 'preserved_existing_labels': True}
        quality = evaluate_labels(c, eval_path)
        counts = {name: c.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                  for name in ('candidate_skill_vectors', 'candidate_skill_grade_vectors', 'skill_test_results',
                               'candidate_activity', 'candidate_fsp_achievements')}
    metrics = {'scoring_version': SCORING_VERSION, 'weights': WEIGHTS,
               'signals': 'skills/activity/FSP synthetic_demo; experience real Jina vectors',
               'catalog_dimensions': len(catalog()[0]), 'database_counts': counts,
               'dataset': dataset, 'vacancies': results, 'quality': quality}
    (out / 'metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'top20.md').write_text('\n'.join(markdown), encoding='utf-8')
    return {k: v for k, v in metrics.items() if k != 'vacancies'} | {
        'rankings': [{'vacancy_id': r['vacancy_id'], 'first_seconds': r['first_ranking_seconds'],
                      'warm_median_seconds': r['warm_median_seconds'],
                      'candidates': r['same_specialization_candidates'],
                      'with_experience_vectors': r['candidates_with_experience_vectors']} for r in results]}


def compare_weights(db, vacancy_ids, out):
    """Compare operational diagnostics, never present them as labeled relevance."""
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261010)
    report = {'quality_status': 'unlabeled; diagnostics only', 'vacancies': []}
    review = ['# Примеры для проверки весов', '',
              'Тексты ниже нужны для ручной оценки релевантности; численные диагностические показатели не заменяют разметку.', '']
    with connect(db) as c:
        for vid in vacancy_ids:
            vacancy = c.execute('SELECT vacancy_name,responsibilities,specialization FROM vacancies WHERE id=?', (vid,)).fetchone()
            pool = rank_vacancy(c, vid, limit=1000)
            experience_only = {r['resume_id'] for r in sorted(pool,
                key=lambda r: (-r['components']['experience'], r['resume_id']))[:20]}
            item = {'vacancy_id': vid, 'vacancy_name': vacancy['vacancy_name'],
                    'specialization': vacancy['specialization'], 'candidate_count': len(pool),
                    'settings': []}
            review += [f"## {vacancy['vacancy_name']} — {vacancy['specialization']}", '',
                       'Обязанности: ' + (vacancy['responsibilities'] or '')[:1500], '']
            union_top = {}
            for name, weights in WEIGHT_SETS.items():
                ranked = sorted(pool, key=lambda r: (-sum(weights[k] * r['components'][k]
                    for k in weights), r['resume_id']))
                top = ranked[:20]
                top_ids = {r['resume_id'] for r in top}
                stability = []
                for _ in range(30):
                    perturbed = sorted(pool, key=lambda r: (-sum(weights[k] * r['components'][k]
                        for k in weights) -
                        weights['skills'] * (rng.uniform(-.02, .02) if r['components']['skills'] else 0) -
                        weights['activity'] * (rng.uniform(-.02, .02) if r['activity_source'] == 'synthetic_demo' else 0) -
                        weights['fsp'] * (rng.uniform(-.02, .02) if 'synthetic_demo' in r['fsp_sources'] or not r['fsp_sources'] else 0),
                        r['resume_id']))[:20]
                    stability.append(len(top_ids & {r['resume_id'] for r in perturbed}) / len(top))
                setting = {'name': name, 'weights': weights,
                    'mean_top20_experience': round(statistics.mean(r['components']['experience'] for r in top), 4),
                    'mean_top20_skills': round(statistics.mean(r['components']['skills'] for r in top), 4),
                    'no_experience_in_top20': sum(r['experience_blocks_found'] == 0 for r in top),
                    'overlap_with_experience_only_top20': len(top_ids & experience_only),
                    'mean_top20_overlap_under_demo_noise': round(statistics.mean(stability), 4),
                    'top20_resume_ids': [r['resume_id'] for r in top]}
                item['settings'].append(setting)
                review += [f"### {name}", '',
                           f"Средний скор опыта в топ-20: {setting['mean_top20_experience']:.4f}; "
                           f"средний скор технологий: {setting['mean_top20_skills']:.4f}; "
                           f"пересечение с топом только по опыту: {setting['overlap_with_experience_only_top20']}/20.", '',
                           '| Место | Резюме | Опыт | Навыки | Лучший блок |',
                           '|---:|---|---:|---:|---|']
                for rank, row in enumerate(top[:5], 1):
                    union_top[row['resume_id']] = row
                    review.append(f"| {rank} | {row['resume_id']} | {row['components']['experience']:.3f} | "
                                  f"{row['components']['skills']:.3f} | {row['best_experience_kind'] or 'нет'} |")
                review.append('')
            review += ['### Опыт кандидатов, встретившихся в топ-5', '']
            for rid, row in union_top.items():
                block = c.execute('SELECT title,description FROM work_items WHERE id=?',
                                  (row['best_experience_item_id'],)).fetchone() if row['best_experience_item_id'] else None
                review += [f"#### {rid} — {row['position_name'] or 'без должности'}", '',
                           (f"{block['title'] or ''}: {block['description'] or ''}" if block else 'Готового вектора опыта нет.')[:1800], '']
            report['vacancies'].append(item)
    (out / 'weight_comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'weight_review.md').write_text('\n'.join(review), encoding='utf-8')
    return {'quality_status': report['quality_status'], 'reports': [str(out / 'weight_comparison.json'),
            str(out / 'weight_review.md')], 'summaries': [{k: v for k, v in item.items() if k != 'settings'} |
            {'settings': [{k: v for k, v in setting.items() if k != 'top20_resume_ids'}
                          for setting in item['settings']]} for item in report['vacancies']]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('populate-demo', 'index-vacancies', 'benchmark', 'evaluate', 'compare-weights'))
    parser.add_argument('--db')
    parser.add_argument('--vacancy-id', action='append', dest='vacancy_ids')
    parser.add_argument('--model-path')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    init_db(args.db)
    with connect(args.db) as c:
        ids = args.vacancy_ids or select_vacancies(c)
    if args.command == 'populate-demo':
        result = populate_demo(args.db)
    elif args.command == 'index-vacancies':
        result = index_vacancies(args.db, ids, args.model_path)
    elif args.command == 'benchmark':
        result = benchmark(args.db, ids, args.out)
    elif args.command == 'compare-weights':
        result = compare_weights(args.db, ids, args.out)
    else:
        with connect(args.db) as c:
            result = evaluate_labels(c, args.out / 'eval_pairs.csv')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
