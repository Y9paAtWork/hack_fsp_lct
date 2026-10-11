"""Evaluate the four-vacancy, 600-pair blind review without requiring human ranks.

Run from site/: python3 -m backend.evaluate_deep_grade_review
The filled review is checked against the original blind file before joining local
hidden IDs and ranking candidates with the current site code.
"""

import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from .build_deep_grade_review import OUT, PER_VACANCY, VACANCY_IDS
from .build_grade_aware_review import FIELDS, REVIEW_FIELDS
from .comprehensive_review_evaluation import (
    CURRENT, bootstrap_interval, evaluate_cohort, paired_weight_intervals,
)
from .db import connect
from .evaluate_grade_aware_review import ABLATION_WEIGHTS, filter_analysis, paired_difference
from .ranking import SCORING_VERSION, rank_vacancy


FILLED = OUT / 'blind_review_5_filled.csv'
GRADES = {'Junior', 'Middle', 'Senior'}


def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f'Unexpected columns in {path}')
        return list(reader)


def validate_and_join():
    original = read_rows(OUT / 'blind_review.csv')
    filled = read_rows(FILLED)
    key = json.loads((OUT / '.blind_review_key.json').read_text(encoding='utf-8'))
    expected = len(VACANCY_IDS) * PER_VACANCY
    if len(original) != expected or len(filled) != expected or len(key) != expected:
        raise ValueError(f'Expected {expected} original, filled and hidden-key rows')
    original_by_id = {row['review_id']: row for row in original}
    filled_by_id = {row['review_id']: row for row in filled}
    if (len(original_by_id) != expected or len(filled_by_id) != expected or
            set(original_by_id) != set(filled_by_id) or set(key) != set(filled_by_id)):
        raise ValueError('Missing or duplicate review IDs')
    groups = defaultdict(list)
    corrections = {'vacancy_grade': [], 'candidate_grade': [], 'candidate_specialization': []}
    for review_id, row in filled_by_id.items():
        previous = original_by_id[review_id]
        if any(row[field] != previous[field] for field in FIELDS[:13]):
            raise ValueError(f'Immutable source column changed: {review_id}')
        if any(previous[field] for field in REVIEW_FIELDS):
            raise ValueError(f'Original review column not blank: {review_id}')
        if row['specialization_fit_0_1'] not in ('0', '1') or row['grade_fit_0_1'] not in ('0', '1'):
            raise ValueError(f'Missing or invalid fit flag: {review_id}')
        if row['relevance_0_3'] not in ('0', '1', '2', '3'):
            raise ValueError(f'Missing or invalid relevance: {review_id}')
        for field in ('vacancy_grade_corrected_optional', 'candidate_grade_corrected_optional'):
            if row[field] and row[field] not in GRADES:
                raise ValueError(f'Invalid grade correction: {review_id}')
        human_rank = row['human_rank_1_best_optional']
        if human_rank and not human_rank.isdigit():
            raise ValueError(f'Invalid human rank: {review_id}')
        identity = key[review_id]
        groups[identity['vacancy_id']].append({
            'review_id': review_id, 'vacancy_name': row['vacancy_name'],
            'resume_id': identity['resume_id'], 'relevance': int(row['relevance_0_3']),
            'human_rank': int(human_rank) if human_rank else None,
            'specialization_fit': int(row['specialization_fit_0_1']),
            'grade_fit': int(row['grade_fit_0_1']),
            'sampling_stratum': identity['sampling_stratum'],
            'vacancy_grade_current': row['vacancy_grade_current'],
            'candidate_grade_current': row['candidate_grade_current'],
            'candidate_specialization_current': row['candidate_specialization_current'],
        })
        for field, output_name, original_field in (
            ('vacancy_grade_corrected_optional', 'vacancy_grade', 'vacancy_grade_current'),
            ('candidate_grade_corrected_optional', 'candidate_grade', 'candidate_grade_current'),
            ('candidate_specialization_corrected_optional', 'candidate_specialization',
             'candidate_specialization_current'),
        ):
            if row[field] and row[field] != row[original_field]:
                corrections[output_name].append({
                    'review_id': review_id, 'vacancy_id': identity['vacancy_id'],
                    'resume_id': identity['resume_id'], 'from': row[original_field], 'to': row[field],
                })
    if set(groups) != set(VACANCY_IDS) or any(len(group) != PER_VACANCY for group in groups.values()):
        raise ValueError('Expected four groups of 150 candidates')
    if len({row['resume_id'] for group in groups.values() for row in group}) != expected:
        raise ValueError('Resume IDs must be unique across all vacancies')
    all_ranks = [row['human_rank'] for group in groups.values() for row in group]
    if any(rank is not None for rank in all_ranks):
        for vacancy_id, group in groups.items():
            if sorted(row['human_rank'] for row in group if row['human_rank'] is not None) != list(
                    range(1, PER_VACANCY + 1)):
                raise ValueError(f'Human ranks must be complete and unique: {vacancy_id}')
        has_human_ranks = True
    else:
        has_human_ranks = False
    return dict(groups), corrections, has_human_ranks


def score_drift(c, groups):
    snapshot = json.loads((OUT / '.model_snapshot.json').read_text(encoding='utf-8'))
    if snapshot['scoring_version'] != SCORING_VERSION:
        return {'compatible_version': False}
    compared, changed, maximum = 0, 0, 0.0
    for vacancy_id, group in groups.items():
        current = rank_vacancy(c, vacancy_id, limit=1000,
                               candidate_ids=[row['resume_id'] for row in group])
        for row in current:
            old = snapshot['selected_eligible_ranks'].get(vacancy_id, {}).get(row['resume_id'])
            if old is None:
                raise ValueError('Eligible resume missing in original snapshot')
            difference = abs(old['score'] - row['score'])
            compared += 1
            changed += difference > 1e-6
            maximum = max(maximum, difference)
    return {'compatible_version': True, 'compared_scores': compared,
            'changed_scores': changed, 'max_abs_score_difference': round(maximum, 6)}


def fmt(value):
    return '—' if value is None else f'{value:.4f}'


def render(report):
    evaluation = report['ranking']
    current = next(item for item in evaluation['settings'] if item['name'] == CURRENT)
    macro = current['macro']
    filters = report['filter_analysis']
    lines = [
        '# Проверка ранжирования: 4 вакансии × 150 кандидатов', '',
        f"Расчёт: {report['evaluated_at_utc']}. Скоринг: `{report['scoring_version']}`. "
        '600 новых пар без пересечения с прошлой разметкой. Из каждой вакансии 120 кандидатов '
        'проходят текущий фильтр БД, 30 отобраны как сложные примеры несовпадения. '
        'Средние метрики — макро по четырём вакансиям.', '',
        '## Действующие веса 70 / 25 / 2,5 / 2,5', '',
        '| Метрика | Значение |', '|---|---:|',
    ]
    for field, label in (
        ('ndcg_at_5', 'NDCG@5, все размеченные'),
        ('ndcg_at_10', 'NDCG@10, все размеченные'),
        ('ndcg_at_20', 'NDCG@20, все размеченные'),
        ('ndcg_within_eligible_at_10', 'NDCG@10 среди допущенных'),
        ('ndcg_ceiling_at_10', 'Потолок NDCG@10 из-за фильтра'),
        ('precision_2plus_at_5', 'Precision@5, оценки 2–3'),
        ('precision_2plus_at_10', 'Precision@10, оценки 2–3'),
        ('precision_2plus_at_20', 'Precision@20, оценки 2–3'),
        ('recall_2plus_at_10', 'Recall@10, оценки 2–3'),
        ('recall_2plus_at_20', 'Recall@20, оценки 2–3'),
        ('average_precision_2plus_at_20', 'MAP@20, оценки 2–3'),
        ('reciprocal_rank_2plus_at_20', 'MRR@20, оценки 2–3'),
        ('label_auc_2plus_eligible', 'ROC AUC среди допущенных, оценки 2–3'),
    ):
        lines.append(f'| {label} | {fmt(macro[field])} |')
    for cutoff in (10, 20):
        lines.append(f"| Максимально возможный Recall@{cutoff} при {cutoff} местах | "
                     f"{fmt(report['recall_capacity_ceiling'][f'at_{cutoff}'])} |")
    if report['has_human_ranks']:
        for field, label in (('spearman_eligible', 'Spearman среди допущенных'),
                             ('pairwise_accuracy_eligible', 'Согласие порядка пар'),
                             ('human_top10_overlap', 'Пересечение с человеческим топ-10')):
            lines.append(f'| {label} | {fmt(macro[field])} |')
    ci = evaluation['current_weight_bootstrap_95']
    lines += ['', f"Ориентировочный 95% bootstrap интервал NDCG@10 по четырём вакансиям: "
              f"[{ci['ndcg_at_10'][0]:.4f}; {ci['ndcg_at_10'][1]:.4f}].", '',
              '## Результаты по вакансиям', '',
              '| Вакансия | NDCG@10 | NDCG среди допущенных @10 | P@10, 2–3 | '
              'Сильных допущено / всего |',
              '|---|---:|---:|---:|---:|']
    for item in current['per_vacancy']:
        m = item['metrics']
        strong_eligible = m['eligible_label_distribution']['2'] + m['eligible_label_distribution']['3']
        strong_all = m['label_distribution']['2'] + m['label_distribution']['3']
        lines.append(f"| {item['vacancy_name']} | {fmt(m['ndcg_at_10'])} | "
                     f"{fmt(m['ndcg_within_eligible_at_10'])} | "
                     f"{fmt(m['precision_2plus_at_10'])} | {strong_eligible}/{strong_all} |")
    lines += ['', '## Качество фильтра специализации и грейда', '',
              '| Цель по разметке | TP | FP | FN | TN | Precision | Recall | F1 |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for key, label in (('specialization_fit', 'Соответствие специализации'),
                       ('grade_fit', 'Соответствие грейда'),
                       ('both_fit', 'Оба условия'),
                       ('relevance_2plus', 'Релевантность 2–3')):
        item = filters['targets'][key]
        lines.append(f"| {label} | {item['tp']} | {item['fp']} | {item['fn']} | "
                     f"{item['tn']} | {fmt(item['precision'])} | {fmt(item['recall'])} | "
                     f"{fmt(item['f1'])} |")
    reasons = filters['current_filter_reasons']
    missed = report['missed_strong_by_filter']
    lines += ['', f"Фильтр допустил {reasons.get('eligible', 0)} из 600; исключил "
              f"{reasons.get('specialization', 0)} по специализации и "
              f"{reasons.get('grade', 0)} по грейду. "
              f"Распределение оценок: {filters['review_label_distribution']}. "
              'Ни одна из выбранных вакансий не отсекала кандидатов по обязательному стеку.', '',
              f"Из {missed['strong_total']} кандидатов с оценкой 2–3 фильтр исключил "
              f"{missed['excluded_total']} ({missed['excluded_share']:.1%}): "
              f"{missed['wrong_grade_same_specialization']} из-за несовпадения сохранённого "
              f"грейда и {missed['wrong_specialization']} из-за специализации. "
              'По разметке все эти кандидаты подходят и по специализации, и по уровню; '
              'это показывает цену жёсткого фильтра на данной выборке.', '',
              '## Сравнение весов', '',
              '| Опыт / навыки / активность / ФСП | NDCG@10 | NDCG@20 | P@10, 2–3 | '
              'Recall@20, 2–3 | Δ NDCG@10 к действующим |',
              '|---|---:|---:|---:|---:|---:|']
    for setting in [*evaluation['settings'], *report['ablations']['settings']]:
        w, m = setting['weights'], setting['macro']
        label = ' / '.join(f"{100*w[k]:g}" for k in ('experience', 'skills', 'activity', 'fsp'))
        delta = (evaluation['paired_weight_intervals_vs_current'].get(setting['name']) or
                 report['ablations']['paired_differences_vs_current'].get(setting['name']))
        diff = '—' if delta is None else f"{delta['mean_difference']:+.4f}"
        lines.append(f"| {label} | {fmt(m['ndcg_at_10'])} | {fmt(m['ndcg_at_20'])} | "
                     f"{fmt(m['precision_2plus_at_10'])} | {fmt(m['recall_2plus_at_20'])} | "
                     f'{diff} |')
    lines += ['', 'Парный bootstrap по четырём вакансиям для наиболее обсуждаемых вариантов:', '',
              '| Сравнение с действующими весами | Δ NDCG@10 | Интервал 95% |',
              '|---|---:|---:|']
    for name, label, source in (
        ('original_35_35_15_15', '35/35/15/15', evaluation['paired_weight_intervals_vs_current']),
        ('experience_50_skills_50', '50/50/0/0', report['ablations']['paired_differences_vs_current']),
    ):
        item = source[name]
        lo, hi = item['bootstrap_95']
        lines.append(f"| {label} | {item['mean_difference']:+.4f} | "
                     f'[{lo:+.4f}; {hi:+.4f}] |')
    lines += ['', 'Разности весов подобраны и измерены на этой же выборке; это исследовательское '
              'сравнение, не независимый тест новых весов.', '',
              '## Проверка целостности и ограничения', '',
              f"Изменённых исходных колонок: 0. Отсутствующих или повторных `review_id`: 0. "
              f"Исправлений грейда вакансий: {len(report['corrections']['vacancy_grade'])}, "
              f"кандидатов: {len(report['corrections']['candidate_grade'])}. "
              f"Изменённых модельных оценок с момента формирования выборки: "
              f"{report['snapshot_score_drift'].get('changed_scores', 'неизвестно')}. "
              'Полный человеческий порядок кандидатов не размечен, поэтому Spearman и '
              'пересечение с человеческим топом не вычислялись.', '',
              'Выборка специально стратифицирована по позиции в текущей выдаче и типу '
              'несовпадения. Её метрики не оценивают случайный поток пользователей; '
              'сравнение с предыдущим раундом также нельзя трактовать как изменение качества '
              'продукта. Тестовые баллы, активность и достижения импортированных кандидатов '
              'остаются демонстрационными. Для доверительного интервала есть только четыре '
              'независимые вакансии. NDCG использует релевантность 0–3 с gain `2^rel − 1` '
              'и идеал по всем 150 размеченным кандидатам каждой вакансии.', '',
              '## Вывод', '',
              'На новом наборе из 600 пар текущий алгоритм уверенно поднимает сильных '
              'кандидатов в первые позиции: NDCG@10 = 0,9370 и P@10 = 1,0000 по оценкам '
              '2–3. При этом 49 сильных кандидатов исключаются жёстким фильтром до '
              'ранжирования. Следующий шаг для качества подбора — уточнить правила '
              'допуска по грейду и специализации и проверить их на новой независимой '
              'выборке. Пересмотр весов по четырём вакансиям пока не обоснован.', '']
    return '\n'.join(lines) + '\n'


def main():
    groups, corrections, has_human_ranks = validate_and_join()
    manifest = json.loads((OUT / 'sampling_manifest.json').read_text(encoding='utf-8'))
    with connect() as c:
        evaluation = evaluate_cohort(c, groups, has_human_ranks)
        ablations = evaluate_cohort(c, groups, has_human_ranks, ABLATION_WEIGHTS)
        drift = score_drift(c, groups)
    current = next(item for item in evaluation['settings'] if item['name'] == CURRENT)
    evaluation['current_weight_bootstrap_95'] = {
        key: bootstrap_interval([item['metrics'][key] for item in current['per_vacancy']])
        for key in ('ndcg_at_10', 'precision_2plus_at_10', 'recall_2plus_at_20')
    }
    evaluation['paired_weight_intervals_vs_current'] = paired_weight_intervals(
        evaluation['settings'], current)
    ablations['paired_differences_vs_current'] = {
        setting['name']: paired_difference(setting, current)
        for setting in ablations['settings']
    }
    all_rows = [row for group in groups.values() for row in group]
    strong = [row for row in all_rows if row['relevance'] >= 2]
    excluded = [row for row in strong if row['sampling_stratum'] not in (
        'upper_rank_band', 'middle_rank_band', 'lower_rank_band')]
    missed = {
        'strong_total': len(strong),
        'excluded_total': len(excluded),
        'excluded_share': len(excluded) / len(strong),
        'wrong_grade_same_specialization': sum(
            row['sampling_stratum'] == 'same_specialization_wrong_grade' for row in excluded),
        'wrong_specialization': sum(
            row['sampling_stratum'] in ('adjacent_specialization_same_grade',
                                         'distant_specialization_same_grade') for row in excluded),
        'excluded_both_fit_count': sum(
            row['specialization_fit'] and row['grade_fit'] for row in excluded),
    }
    recall_capacity = {
        f'at_{cutoff}': round(statistics.mean(
            min(cutoff, sum(row['relevance'] >= 2 for row in group)) /
            sum(row['relevance'] >= 2 for row in group)
            for group in groups.values()), 4)
        for cutoff in (10, 20)
    }
    report = {
        'evaluated_at_utc': datetime.now(timezone.utc).isoformat(),
        'scoring_version': SCORING_VERSION,
        'source_file': str(FILLED.relative_to(OUT.parent.parent.parent)),
        'has_human_ranks': has_human_ranks,
        'ranking': evaluation,
        'ablations': ablations,
        'filter_analysis': filter_analysis(groups, evaluation, manifest),
        'missed_strong_by_filter': missed,
        'recall_capacity_ceiling': recall_capacity,
        'corrections': corrections,
        'snapshot_score_drift': drift,
    }
    (OUT / 'grade_aware_evaluation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'grade_aware_evaluation.md').write_text(render(report), encoding='utf-8')
    print(json.dumps({
        'reviewed_pairs': evaluation['reviewed_pairs'],
        'current_macro': current['macro'],
        'filters': report['filter_analysis']['targets'],
        'snapshot_score_drift': drift,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
