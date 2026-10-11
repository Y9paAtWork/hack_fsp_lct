"""Evaluate one vacancy using relevance labels from review rounds 2 and 3."""

import csv
import json
from pathlib import Path

from .db import ROOT, connect
from .ranking import rank_vacancy
from .ranking_cli import WEIGHT_SETS, dcg


OUT = ROOT / 'out' / 'four_signal_eval'
VACANCY_ID = '4a8a15e6436154498a01ef9205336ece'


def labels_from(path):
    with path.open(encoding='utf-8-sig', newline='') as file:
        rows = [row for row in csv.DictReader(file) if row['vacancy_id'] == VACANCY_ID]
    labels = {}
    for row in rows:
        resume_id = row['resume_id']
        label = row['relevance_0_3'].strip()
        if resume_id in labels or label not in ('0', '1', '2', '3'):
            raise ValueError(f'Duplicate resume or missing label in {path}: {resume_id}')
        labels[resume_id] = int(label)
    return labels


def ndcg(values, ideal, k):
    denominator = dcg(ideal, k)
    return round(dcg(values, k) / denominator, 4) if denominator else None


def evaluate(db=ROOT / 'data' / 'fsp.sqlite3', out=OUT):
    old = labels_from(out / 'round2' / 'eval_pairs.csv')
    new = labels_from(out / 'round3' / 'eval_pairs.csv')
    if len(old) != 30 or len(new) != 100 or old.keys() & new.keys():
        raise ValueError('Expected 30 old and 100 new distinct labels for AI Engineer')
    labels = old | new
    ideal = sorted(labels.values(), reverse=True)
    positive_total = sum(value > 0 for value in ideal)
    strong_total = sum(value >= 2 for value in ideal)
    result = {'vacancy_id': VACANCY_ID, 'reviewed_candidates': len(labels),
              'round2_candidates': len(old), 'round3_candidates': len(new),
              'human_rank_combined': None, 'settings': []}
    with connect(db) as c:
        for name, weights in WEIGHT_SETS.items():
            model = rank_vacancy(c, VACANCY_ID, limit=1000, candidate_ids=list(labels), weights=weights)
            model_ids = [row['resume_id'] for row in model]
            judged = [labels[rid] for rid in model_ids]
            eligible_ideal = sorted(judged, reverse=True)
            excluded = [labels[rid] for rid in labels if rid not in set(model_ids)]
            result['settings'].append({
                'name': name, 'weights': weights,
                'eligible_candidates': len(model_ids),
                'excluded_positive': sum(value > 0 for value in excluded),
                'excluded_strong': sum(value >= 2 for value in excluded),
                'ndcg_at_10': ndcg(judged, ideal, 10),
                'ndcg_at_20': ndcg(judged, ideal, 20),
                'ndcg_ceiling_at_10_with_filter': ndcg(eligible_ideal, ideal, 10),
                'ndcg_within_eligible_at_10': ndcg(judged, eligible_ideal, 10),
                'recall_at_20': round(sum(value > 0 for value in judged[:20]) / positive_total, 4)
                if positive_total else None,
                'recall_strong_at_20': round(sum(value >= 2 for value in judged[:20]) / strong_total, 4)
                if strong_total else None,
                'top_10_label_distribution': {str(value): judged[:10].count(value) for value in range(4)},
            })
    result['positive_total'] = positive_total
    result['strong_total'] = strong_total
    prior = json.loads((out / 'combined_human_evaluation.json').read_text(encoding='utf-8'))
    prior_by_name = {item['name']: item for item in prior['settings']}
    for setting in result['settings']:
        previous = prior_by_name[setting['name']]['per_vacancy']
        other_vacancies = [item for item in previous if item['vacancy_id'] != VACANCY_ID]
        if len(other_vacancies) != 9:
            raise ValueError('Expected nine other vacancies in the previous benchmark')
        setting['updated_ten_vacancy_macro'] = {
            metric: round((sum(item[metric] for item in other_vacancies) + setting[metric]) / 10, 4)
            for metric in ('ndcg_at_10', 'ndcg_at_20')
        }
    return result


def markdown(result):
    lines = [
        '# AI Engineer: объединённая оценка 130 кандидатов', '',
        'Использованы оценки релевантности 0–3 из двух независимых слепых раундов: '
        '30 кандидатов второго раунда и 100 третьего. Кандидаты не повторяются. '
        'Человеческие места 1–30 и 1–100 нельзя объединять в единый порядок, '
        'поэтому Spearman и совпадение с человеческим топ-10 здесь не рассчитываются.', '',
        '| Опыт / навыки / активность / ФСП | NDCG@10 | NDCG@20 | Recall@20 | Recall 2–3@20 |',
        '|---|---:|---:|---:|---:|',
    ]
    for setting in result['settings']:
        w = setting['weights']
        label = ' / '.join(f'{w[key] * 100:g}' for key in ('experience', 'skills', 'activity', 'fsp'))
        lines.append(f"| {label} | {setting['ndcg_at_10']:.4f} | "
                     f"{setting['ndcg_at_20']:.4f} | {setting['recall_at_20']:.4f} | "
                     f"{setting['recall_strong_at_20']:.4f} |")
    current = next(item for item in result['settings'] if item['name'] == 'experience_70_25_025_025')
    lines += ['', f"Фильтр допускает {current['eligible_candidates']} из {result['reviewed_candidates']} "
              f"кандидатов. Вне выдачи остаются {current['excluded_positive']} положительно оценённых, "
              f"включая {current['excluded_strong']} с оценкой 2–3. "
              f"Потолок NDCG@10 при таком фильтре — {current['ndcg_ceiling_at_10_with_filter']:.4f}.", '',
              '## Обновлённое среднее по десяти вакансиям', '',
              'Для AI Engineer взят NDCG по 130 кандидатам; для остальных девяти вакансий '
              'оставлены прежние оценки по 30 кандидатам.', '',
              '| Опыт / навыки / активность / ФСП | NDCG@10 | NDCG@20 |',
              '|---|---:|---:|']
    for setting in result['settings']:
        w = setting['weights']
        label = ' / '.join(f'{w[key] * 100:g}' for key in ('experience', 'skills', 'activity', 'fsp'))
        m = setting['updated_ten_vacancy_macro']
        lines.append(f"| {label} | {m['ndcg_at_10']:.4f} | {m['ndcg_at_20']:.4f} |")
    lines += ['',
              'Выборка стратифицирована и содержит сложные примеры; абсолютные значения метрик '
              'не являются оценкой качества на случайном потоке вакансий.', '']
    return '\n'.join(lines)


def main():
    result = evaluate()
    target = OUT / 'round3'
    (target / 'combined_ai_130_evaluation.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (target / 'combined_ai_130_evaluation.md').write_text(markdown(result), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
