"""Evaluate both blind-review rounds as one vacancy-level benchmark."""

import argparse
import json
import random
import statistics
from pathlib import Path

from .db import ROOT
from .review_evaluation import evaluate, validate_reviews


DEFAULT_OUT = ROOT / 'out' / 'four_signal_eval'
DEFAULT_REVIEWS = ('blind_review_filled.csv', 'blind_review_2_filled.csv')


def load_round(directory, review):
    _, _, _, groups = validate_reviews(
        directory / 'blind_review.csv', review, directory / '.blind_review_key.json',
        directory / 'eval_pairs.csv')
    return groups


def paired_interval(first, second, field, repetitions=20000):
    """Resample vacancies, keeping the two rankings paired within each vacancy."""
    a = {v['vacancy_id']: v[field] for v in first['per_vacancy']}
    b = {v['vacancy_id']: v[field] for v in second['per_vacancy']}
    if a.keys() != b.keys():
        raise ValueError('Weight settings have different vacancy sets')
    differences = [a[key] - b[key] for key in sorted(a)]
    rng = random.Random(20261010)
    draws = sorted(statistics.mean(rng.choices(differences, k=len(differences)))
                   for _ in range(repetitions))
    return {
        'mean_difference': round(statistics.mean(differences), 4),
        'bootstrap_95_interval': [round(draws[int(.025 * repetitions)], 4),
                                  round(draws[int(.975 * repetitions)], 4)],
        'positive_vacancies': sum(delta > 0 for delta in differences),
        'negative_vacancies': sum(delta < 0 for delta in differences),
        'tied_vacancies': sum(delta == 0 for delta in differences),
    }


def summarize(combined, round_reports):
    by_name = {item['name']: item for item in combined['settings']}
    selected = by_name['experience_70_25_025_025']
    comparisons = {
        name: paired_interval(setting, selected, 'ndcg_at_10')
        for name, setting in by_name.items() if name != selected['name']
    }
    per_round = {
        label: {
            'reviewed_pairs': report['reviewed_pairs'],
            'vacancies': len(report['settings'][0]['per_vacancy']),
            'settings': {item['name']: item['macro'] for item in report['settings']},
        }
        for label, report in round_reports.items()
    }
    excluded = selected['per_vacancy']
    return {
        'reviewed_pairs': combined['reviewed_pairs'],
        'vacancies': len(excluded),
        'per_round': per_round,
        'settings': combined['settings'],
        'ndcg_at_10_difference_vs_70_25': comparisons,
        'excluded_positive_pairs': sum(v['excluded_positive_count'] for v in excluded),
        'excluded_strong_pairs': sum(v['excluded_strong_count'] for v in excluded),
    }


def markdown(summary):
    lines = [
        '# Оценка ранжирования по двум раундам слепой разметки', '',
        f"{summary['reviewed_pairs']} пар, {summary['vacancies']} вакансий; "
        'по 30 кандидатов на вакансию. Средние метрики рассчитаны по вакансиям, '
        'а не по отдельным парам. Настройки весов выбраны до второго раунда.', '',
        '| Опыт / навыки / активность / ФСП | NDCG@10 | NDCG@20 | Spearman (допущенные) | Pairwise accuracy | Совпадение топ-10 |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    for setting in summary['settings']:
        w, m = setting['weights'], setting['macro']
        weight_text = ' / '.join(f'{100*w[key]:g}' for key in ('experience', 'skills', 'activity', 'fsp'))
        lines.append(f"| {weight_text} | {m['ndcg_at_10']:.4f} | {m['ndcg_at_20']:.4f} | "
                     f"{m['eligible_spearman']:.4f} | {m['eligible_pairwise_accuracy']:.4f} | "
                     f"{m['human_top10_overlap']:.1f}/10 |")
    lines += ['', '## Устойчивость различий', '',
              'Ниже разница NDCG@10 относительно действующих весов 70 / 25 / 2.5 / 2.5. '
              'Интервалы 95% получены парным bootstrap по 10 вакансиям (20 000 повторов); '
              'при таком числе вакансий они ориентировочные.', '',
              '| Веса | Разница | Интервал 95% | Лучше / хуже / равно по вакансиям |',
              '|---|---:|---:|---:|']
    settings = {item['name']: item for item in summary['settings']}
    for name, delta in summary['ndcg_at_10_difference_vs_70_25'].items():
        w = settings[name]['weights']
        label = ' / '.join(f'{100*w[key]:g}' for key in ('experience', 'skills', 'activity', 'fsp'))
        lo, hi = delta['bootstrap_95_interval']
        lines.append(f"| {label} | {delta['mean_difference']:+.4f} | [{lo:+.4f}; {hi:+.4f}] | "
                     f"{delta['positive_vacancies']} / {delta['negative_vacancies']} / {delta['tied_vacancies']} |")
    lines += ['', '## По раундам', '',
              '| Раунд | Пар | Вакансий | NDCG@10, 70/25 | Spearman, 70/25 |',
              '|---|---:|---:|---:|---:|']
    for label, round_data in summary['per_round'].items():
        m = round_data['settings']['experience_70_25_025_025']
        lines.append(f"| {label} | {round_data['reviewed_pairs']} | {round_data['vacancies']} | "
                     f"{m['ndcg_at_10']:.4f} | {m['eligible_spearman']:.4f} |")
    lines += ['', '## Действующие веса по вакансиям', '',
              '| Вакансия | NDCG@10 | Spearman | Совпадение топ-10 | Положительных вне фильтра | Из них оценка 2–3 |',
              '|---|---:|---:|---:|---:|---:|']
    current = settings['experience_70_25_025_025']
    for v in current['per_vacancy']:
        lines.append(f"| {v['vacancy_name']} | {v['ndcg_at_10']:.4f} | "
                     f"{v['eligible_spearman']:.4f} | {v['human_top10_overlap']}/10 | "
                     f"{v['excluded_positive_count']} | {v['excluded_strong_count']} |")
    lines += ['', f"Всего фильтр специализации исключил {summary['excluded_positive_pairs']} "
              f"положительно оценённых кандидатов, включая {summary['excluded_strong_pairs']} "
              'с оценкой 2–3. NDCG сравнивает выдачу только из допущенных кандидатов с идеальным '
              'порядком всех 30; Spearman и pairwise accuracy считают только внутри допущенной группы. '
              'Recall@20 здесь не различает настройки весов: для каждой вакансии допущено ровно 20 кандидатов.', '',
              'Выборка целенаправленно включает сложные и пограничные случаи; эти числа не являются '
              'оценкой качества на случайном потоке вакансий.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'fsp.sqlite3')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--review-round1', type=Path)
    parser.add_argument('--review-round2', type=Path)
    args = parser.parse_args()
    paths = ((args.out, args.review_round1 or args.out / DEFAULT_REVIEWS[0]),
             (args.out / 'round2', args.review_round2 or args.out / 'round2' / DEFAULT_REVIEWS[1]))
    rounds = {}
    combined_groups = {}
    seen_resumes = set()
    for label, (directory, review_path) in zip(('Раунд 1', 'Раунд 2'), paths):
        groups = load_round(directory, review_path)
        if set(groups) & set(combined_groups):
            raise ValueError('Vacancy is repeated across rounds')
        resume_ids = {row['resume_id'] for group in groups.values() for row in group}
        if resume_ids & seen_resumes:
            raise ValueError('Resume is repeated across rounds')
        seen_resumes |= resume_ids
        combined_groups.update(groups)
        rounds[label] = evaluate(args.db, groups)
    result = summarize(evaluate(args.db, combined_groups), rounds)
    (args.out / 'combined_human_evaluation.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.out / 'combined_human_evaluation.md').write_text(markdown(result), encoding='utf-8')
    print(json.dumps({
        'reviewed_pairs': result['reviewed_pairs'], 'vacancies': result['vacancies'],
        'settings': [{'name': item['name'], **item['macro']} for item in result['settings']],
        'ndcg_at_10_difference_vs_70_25': result['ndcg_at_10_difference_vs_70_25'],
        'excluded_positive_pairs': result['excluded_positive_pairs'],
        'excluded_strong_pairs': result['excluded_strong_pairs'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
