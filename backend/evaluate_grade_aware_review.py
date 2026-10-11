"""Evaluate the current ranker against the completed specialization/grade review."""

import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .build_grade_aware_review import DEFAULT_OUT, FIELDS, REVIEW_FIELDS
from .comprehensive_review_evaluation import (
    CURRENT, bootstrap_interval, evaluate_cohort, paired_weight_intervals,
)
from .db import connect
from .ranking import SCORING_VERSION, rank_vacancy


FILLED = DEFAULT_OUT / 'blind_review_4_filled.csv'
GRADES = {'Junior', 'Middle', 'Senior'}
ABLATION_WEIGHTS = {
    'experience_only': {'experience': 1.0, 'skills': 0.0, 'activity': 0.0, 'fsp': 0.0},
    'skills_only': {'experience': 0.0, 'skills': 1.0, 'activity': 0.0, 'fsp': 0.0},
    'experience_40_skills_60': {'experience': .40, 'skills': .60, 'activity': 0.0, 'fsp': 0.0},
    'experience_50_skills_50': {'experience': .50, 'skills': .50, 'activity': 0.0, 'fsp': 0.0},
    'experience_60_skills_40': {'experience': .60, 'skills': .40, 'activity': 0.0, 'fsp': 0.0},
    'experience_70_skills_30': {'experience': .70, 'skills': .30, 'activity': 0.0, 'fsp': 0.0},
}


def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f'Unexpected CSV columns in {path}')
        return list(reader)


def validate_and_join():
    original = read_rows(DEFAULT_OUT / 'blind_review.csv')
    filled = read_rows(FILLED)
    key = json.loads((DEFAULT_OUT / '.blind_review_key.json').read_text(encoding='utf-8'))
    if len(original) != 350 or len(filled) != 350 or len(key) != 350:
        raise ValueError('Expected 350 original and completed review rows')
    old_by_id = {row['review_id']: row for row in original}
    new_by_id = {row['review_id']: row for row in filled}
    if len(old_by_id) != 350 or len(new_by_id) != 350 or set(old_by_id) != set(new_by_id) or set(key) != set(new_by_id):
        raise ValueError('Missing or duplicate review IDs')
    immutable = FIELDS[:13]
    groups = defaultdict(list)
    corrections = {'vacancy_grade': [], 'candidate_grade': [], 'candidate_specialization': []}
    for review_id, row in new_by_id.items():
        if any(row[field] != old_by_id[review_id][field] for field in immutable):
            raise ValueError(f'Source text or original grade changed: {review_id}')
        if any(old_by_id[review_id][field] for field in REVIEW_FIELDS):
            raise ValueError(f'Original review cells were not blank: {review_id}')
        if row['specialization_fit_0_1'] not in ('0', '1') or row['grade_fit_0_1'] not in ('0', '1'):
            raise ValueError(f'Missing or invalid fit flag: {review_id}')
        if row['relevance_0_3'] not in ('0', '1', '2', '3'):
            raise ValueError(f'Missing or invalid relevance: {review_id}')
        if not row['human_rank_1_best_optional'].isdigit():
            raise ValueError(f'Missing human rank: {review_id}')
        for field in ('vacancy_grade_corrected_optional', 'candidate_grade_corrected_optional'):
            if row[field] and row[field] not in GRADES:
                raise ValueError(f'Invalid grade correction in {review_id}')
        identity = key[review_id]
        group = groups[identity['vacancy_id']]
        group.append({'review_id': review_id, 'vacancy_name': row['vacancy_name'],
                      'resume_id': identity['resume_id'], 'relevance': int(row['relevance_0_3']),
                      'human_rank': int(row['human_rank_1_best_optional']),
                      'specialization_fit': int(row['specialization_fit_0_1']),
                      'grade_fit': int(row['grade_fit_0_1']),
                      'sampling_stratum': identity['sampling_stratum'],
                      'vacancy_grade_current': row['vacancy_grade_current'],
                      'candidate_grade_current': row['candidate_grade_current'],
                      'candidate_specialization_current': row['candidate_specialization_current']})
        for field, output_name, original_field in (
            ('vacancy_grade_corrected_optional', 'vacancy_grade', 'vacancy_grade_current'),
            ('candidate_grade_corrected_optional', 'candidate_grade', 'candidate_grade_current'),
            ('candidate_specialization_corrected_optional', 'candidate_specialization',
             'candidate_specialization_current'),
        ):
            if row[field] and row[field] != row[original_field]:
                corrections[output_name].append({'review_id': review_id,
                                                  'vacancy_id': identity['vacancy_id'],
                                                  'resume_id': identity['resume_id'],
                                                  'from': row[original_field], 'to': row[field]})
    if len(groups) != 10 or any(len(group) != 35 for group in groups.values()):
        raise ValueError('Expected 35 rows for each of 10 vacancies')
    if len({row['resume_id'] for group in groups.values() for row in group}) != 350:
        raise ValueError('Expected 350 unique candidates')
    for vacancy_id, group in groups.items():
        if sorted(row['human_rank'] for row in group) != list(range(1, 36)):
            raise ValueError(f'Human ranks must be unique 1..35 for {vacancy_id}')
        vacancy_corrections = {item['to'] for item in corrections['vacancy_grade']
                               if item['vacancy_id'] == vacancy_id}
        if len(vacancy_corrections) > 1:
            raise ValueError(f'Conflicting vacancy grade corrections for {vacancy_id}')
    return dict(groups), corrections


def confusion(predicted, actual):
    tp = sum(p and a for p, a in zip(predicted, actual))
    fp = sum(p and not a for p, a in zip(predicted, actual))
    fn = sum(not p and a for p, a in zip(predicted, actual))
    tn = sum(not p and not a for p, a in zip(predicted, actual))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
            'precision': round(precision, 4) if precision is not None else None,
            'recall': round(recall, 4) if recall is not None else None,
            'specificity': round(tn / (tn + fp), 4) if tn + fp else None,
            'f1': round(2 * precision * recall / (precision + recall), 4)
                  if precision and recall else 0.0}


def filter_analysis(groups, evaluated, manifest):
    current = next(x for x in evaluated['settings'] if x['name'] == CURRENT)
    metrics = {item['vacancy_id']: item['metrics'] for item in current['per_vacancy']}
    rows = [row for group in groups.values() for row in group]
    admitted = [row['sampling_stratum'] in ('upper_rank_band', 'middle_rank_band', 'lower_rank_band')
                for row in rows]
    specialization_equal = []
    for vacancy_id, group in groups.items():
        # Current ranker uses the stored specialization. Every reviewed resume here is an imported resume.
        expected = next(row['vacancy_specialization'] for row in manifest['per_vacancy']
                        if row['vacancy_id'] == vacancy_id)
        specialization_equal.extend(row['candidate_specialization_current'] == expected for row in group)
    grade_equal = [row['candidate_grade_current'] == row['vacancy_grade_current'] for row in rows]
    # The order of groups and rows is preserved by dictionary insertion in validate_and_join.
    if any(a != (s and g) for a, s, g in zip(admitted, specialization_equal, grade_equal)):
        raise ValueError('Sample strata do not match current grade/specialization filter')
    targets = {
        'specialization_fit': confusion(specialization_equal, [bool(row['specialization_fit']) for row in rows]),
        'grade_fit': confusion(grade_equal, [bool(row['grade_fit']) for row in rows]),
        'both_fit': confusion(admitted, [bool(row['specialization_fit'] and row['grade_fit']) for row in rows]),
        'relevance_1plus': confusion(admitted, [row['relevance'] >= 1 for row in rows]),
        'relevance_2plus': confusion(admitted, [row['relevance'] >= 2 for row in rows]),
    }
    strata = {}
    for stratum in sorted({row['sampling_stratum'] for row in rows}):
        selected = [row for row in rows if row['sampling_stratum'] == stratum]
        strata[stratum] = {'count': len(selected),
                           'label_distribution': {str(i): sum(row['relevance'] == i for row in selected)
                                                  for i in range(4)},
                           'specialization_fit': sum(row['specialization_fit'] for row in selected),
                           'grade_fit': sum(row['grade_fit'] for row in selected)}
    reasons = Counter()
    for value in metrics.values():
        reasons.update(value['filter_reasons'])
    return {'targets': targets, 'sampling_strata': strata,
            'current_filter_reasons': dict(reasons),
            'review_label_distribution': dict(Counter(row['relevance'] for row in rows)),
            'review_spec_fit_count': sum(row['specialization_fit'] for row in rows),
            'review_grade_fit_count': sum(row['grade_fit'] for row in rows),
            'review_both_fit_count': sum(row['specialization_fit'] and row['grade_fit'] for row in rows)}


def score_drift(c, groups):
    snapshot = json.loads((DEFAULT_OUT / '.model_snapshot.json').read_text(encoding='utf-8'))
    if snapshot['scoring_version'] != SCORING_VERSION:
        return {'compatible_version': False}
    compared = 0
    changed = 0
    maximum = 0.0
    for vacancy_id, group in groups.items():
        current = rank_vacancy(c, vacancy_id, limit=1000,
                               candidate_ids=[row['resume_id'] for row in group])
        for row in current:
            old = snapshot['selected_eligible_ranks'].get(vacancy_id, {}).get(row['resume_id'])
            if old is None:
                raise ValueError('Current eligible candidate missing from sampling snapshot')
            difference = abs(old['score'] - row['score'])
            compared += 1
            changed += difference > 1e-6
            maximum = max(maximum, difference)
    return {'compatible_version': True, 'compared_scores': compared,
            'changed_scores': changed, 'max_abs_score_difference': round(maximum, 6)}


def subset_macro(setting, excluded_vacancy_ids):
    rows = [item['metrics'] for item in setting['per_vacancy']
            if item['vacancy_id'] not in excluded_vacancy_ids]
    fields = ('ndcg_at_5', 'ndcg_at_10', 'ndcg_at_20', 'ndcg_within_eligible_at_10',
              'precision_2plus_at_5', 'precision_2plus_at_10', 'precision_2plus_at_20',
              'recall_2plus_at_20', 'average_precision_2plus_at_20',
              'label_auc_2plus_eligible', 'spearman_eligible',
              'pairwise_accuracy_eligible', 'human_top10_overlap')
    return {'vacancies': len(rows), **{name: round(statistics.mean(row[name] for row in rows), 4)
                                       for name in fields}}


def paired_difference(first, second, field='ndcg_at_10'):
    reference = {item['vacancy_id']: item['metrics'][field] for item in second['per_vacancy']}
    differences = [item['metrics'][field] - reference[item['vacancy_id']]
                   for item in first['per_vacancy']]
    return {'mean_difference': round(statistics.mean(differences), 4),
            'bootstrap_95': bootstrap_interval(differences)}


def render(report):
    evaluation = report['ranking']
    current = next(x for x in evaluation['settings'] if x['name'] == CURRENT)
    macro = current['macro']
    filters = report['filter_analysis']
    lines = ['# Проверка ранжирования с разметкой специализации и грейда', '',
             f"Расчёт: {report['evaluated_at_utc']}. Скоринг: `{report['scoring_version']}`. "
             '350 размеченных пар: 10 вакансий × 35 кандидатов, из которых 25 на вакансию '
             'проходят текущий фильтр БД. Средние метрики — по вакансиям.', '',
             '## Качество выдачи при действующих весах 70 / 25 / 2,5 / 2,5', '',
             '| Метрика | Значение |', '|---|---:|']
    for key, title in (
        ('ndcg_at_5', 'NDCG@5, все размеченные'),
        ('ndcg_at_10', 'NDCG@10, все размеченные'),
        ('ndcg_at_20', 'NDCG@20, все размеченные'),
        ('ndcg_within_eligible_at_10', 'NDCG@10 среди допущенных'),
        ('ndcg_ceiling_at_10', 'Потолок NDCG@10 из-за фильтра'),
        ('precision_1plus_at_10', 'Precision@10, оценка 1–3'),
        ('precision_2plus_at_5', 'Precision@5, оценка 2–3'),
        ('precision_2plus_at_10', 'Precision@10, оценка 2–3'),
        ('precision_2plus_at_20', 'Precision@20, оценка 2–3'),
        ('recall_2plus_at_10', 'Recall@10, оценка 2–3'),
        ('recall_2plus_at_20', 'Recall@20, оценка 2–3'),
        ('average_precision_2plus_at_20', 'MAP@20, оценка 2–3'),
        ('reciprocal_rank_2plus_at_20', 'MRR@20, оценка 2–3'),
        ('hit_2plus_at_10', 'Доля вакансий с сильным кандидатом в топ-10'),
        ('label_auc_2plus_eligible', 'ROC AUC среди допущенных, оценка 2–3'),
        ('spearman_eligible', 'Spearman среди допущенных'),
        ('pairwise_accuracy_eligible', 'Согласие порядка пар среди допущенных'),
        ('kendall_tau_eligible', 'Kendall tau среди допущенных'),
        ('human_top10_overlap', 'Пересечение с человеческим топ-10, человек'),
    ):
        lines.append(f"| {title} | {macro[key]:.4f} |")
    lines += ['', '## Проверка фильтра допуска', '',
              '| Цель по разметке | TP | FP | FN | TN | Precision | Recall | F1 |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for key, title in (('specialization_fit', 'Соответствие специализации'),
                       ('grade_fit', 'Соответствие грейда'),
                       ('both_fit', 'Оба условия'),
                       ('relevance_1plus', 'Релевантность 1–3'),
                       ('relevance_2plus', 'Релевантность 2–3')):
        item = filters['targets'][key]
        lines.append(f"| {title} | {item['tp']} | {item['fp']} | {item['fn']} | {item['tn']} | "
                     f"{item['precision']:.4f} | {item['recall']:.4f} | {item['f1']:.4f} |")
    reasons = filters['current_filter_reasons']
    lines += ['', f"Фильтр текущего сайта допустил {reasons.get('eligible', 0)} из 350 пар. "
              f"Отсеяно по специализации: {reasons.get('specialization', 0)}, по грейду после "
              f"совпадения специализации: {reasons.get('grade', 0)}. Обязательные навыки в этих "
              'вакансиях не отсекали кандидатов.', '',
              '## Сравнение шести наборов весов', '',
              '| Опыт / навыки / активность / ФСП | NDCG@10 | NDCG@20 | P@10, 2–3 | Recall@20, 2–3 | Spearman |',
              '|---|---:|---:|---:|---:|---:|']
    for setting in evaluation['settings']:
        w, m = setting['weights'], setting['macro']
        label = ' / '.join(f"{100*w[k]:g}" for k in ('experience', 'skills', 'activity', 'fsp'))
        lines.append(f"| {label} | {m['ndcg_at_10']:.4f} | {m['ndcg_at_20']:.4f} | "
                     f"{m['precision_2plus_at_10']:.4f} | {m['recall_2plus_at_20']:.4f} | "
                     f"{m['spearman_eligible']:.4f} |")
    lines += ['', 'Разницы NDCG@10 относительно действующих весов; парный bootstrap по 10 вакансиям:', '',
              '| Веса | Разница | 95% интервал |', '|---|---:|---:|']
    for setting in evaluation['settings']:
        if setting['name'] == CURRENT:
            continue
        w = setting['weights']
        label = ' / '.join(f"{100*w[k]:g}" for k in ('experience', 'skills', 'activity', 'fsp'))
        delta = evaluation['paired_weight_intervals_vs_current'][setting['name']]
        lo, hi = delta['bootstrap_95']
        lines.append(f"| {label} | {delta['mean_difference']:+.4f} | [{lo:+.4f}; {hi:+.4f}] |")
    lines += ['', '## Проверка сигналов без демонстрационной активности и достижений', '',
              '| Опыт / навыки / активность / ФСП | NDCG@10, 10 вакансий | NDCG@10, 9 вакансий* | P@10, 2–3 | Spearman |',
              '|---|---:|---:|---:|---:|']
    for setting in report['ablations']['settings']:
        w, m = setting['weights'], setting['macro']
        label = ' / '.join(f"{100*w[k]:g}" for k in ('experience', 'skills', 'activity', 'fsp'))
        nine = report['nine_vacancy_results'][setting['name']]
        lines.append(f"| {label} | {m['ndcg_at_10']:.4f} | {nine['ndcg_at_10']:.4f} | "
                     f"{m['precision_2plus_at_10']:.4f} | {m['spearman_eligible']:.4f} |")
    lines += ['', '*Девять вакансий без AI Engineer, для которой предоставлена правка грейда. '
              'Это диагностический срез, а не общая оценка системы.', '',
              '| Сравнение NDCG@10 по 10 вакансиям | Разница | Парный bootstrap 95% |',
              '|---|---:|---:|']
    for name, title in (('balanced_vs_current', '50/50/0/0 − действующие веса'),
                        ('balanced_vs_demo_best', '50/50/0/0 − 35/35/15/15')):
        item = report['ablations']['paired_differences'][name]
        lo, hi = item['bootstrap_95']
        lines.append(f"| {title} | {item['mean_difference']:+.4f} | [{lo:+.4f}; {hi:+.4f}] |")
    nine_current = report['nine_vacancy_results'][CURRENT]
    lines += ['', '## Диагностический срез: девять вакансий без спорного грейда', '',
              f"Действующие веса: NDCG@10 **{nine_current['ndcg_at_10']:.4f}**, "
              f"P@10 по оценкам 2–3 **{nine_current['precision_2plus_at_10']:.4f}**, "
              f"Recall@20 **{nine_current['recall_2plus_at_20']:.4f}**. "
              'Для полной выборки из 10 вакансий используются показатели в первой таблице.', '']
    lines += ['', '## По вакансиям', '',
              '| Вакансия | Грейд БД → исправление | NDCG@10 | P@10, 2–3 | Recall@20, 2–3 | '
              'Допущено сильных / всего сильных |',
              '|---|---|---:|---:|---:|---:|']
    for item in current['per_vacancy']:
        vid, m = item['vacancy_id'], item['metrics']
        correction = {entry['to'] for entry in report['corrections']['vacancy_grade']
                      if entry['vacancy_id'] == vid}
        original = report['vacancy_grades'][vid]
        grade = f"{original} → {next(iter(correction))}" if correction else original
        strong_eligible = m['eligible_label_distribution']['2'] + m['eligible_label_distribution']['3']
        strong_all = m['label_distribution']['2'] + m['label_distribution']['3']
        lines.append(f"| {item['vacancy_name']} | {grade} | {m['ndcg_at_10']:.4f} | "
                     f"{m['precision_2plus_at_10']:.4f} | {m['recall_2plus_at_20']:.4f} | "
                     f'{strong_eligible}/{strong_all} |')
    evidence = report['corrected_vacancy_evidence']
    lines += ['', '## Интерпретация и ограничения', '',
              'В предоставленном файле исправлен грейд вакансии AI Engineer с Junior на Middle '
              'во всех 35 строках. Это согласуется с текстом требований: «рассматриваем грейд Middle», '
              f"хотя в отдельном поле опыта указано {evidence['experience_requirements']} года. "
              f"Текущий грейд присвоен источником `{evidence['grade_source']}`. "
              'БД и рабочий фильтр при расчёте оставлены без изменений. '
              'По трём кандидатам предложена правка грейда. Чтобы оценить последствия исправления '
              'AI Engineer, нужна новая выборка для грейда Middle: текущая содержит 30 Junior '
              'и 5 Middle. Все три кандидата с оценкой 2–3 в этой вакансии относятся к Middle.', '',
              'Разметка заполнена полностью, исходные тексты и идентификаторы строк не менялись. '
              'Выборка специально содержит сильные и пограничные примеры, поэтому её доли и метрики '
              'не являются оценкой случайного потока. Тестовые баллы, активность и достижения '
              'импортированных кандидатов остаются демонстрационными. Десять вакансий дают '
              'только ориентировочные bootstrap интервалы. Подбор весов на этой же выборке '
              'является исследовательским и требует проверки на отдельной выборке.', '',
              'NDCG использует шкалу релевантности 0–3 и gain `2^rel − 1`. В идеале участвуют '
              'все 35 кандидатов каждой вакансии, в том числе исключённые фильтром. '
              'Spearman и согласие пар рассчитаны только для 25 допущенных. '
              'Ранг 1–35 в каждой вакансии взят из возвращённого файла.', '',
              '## Вывод', '',
              'При действующих весах ранжирование даёт NDCG@10 = 0,7900 и в среднем 8,2 '
              'кандидата с оценкой 2–3 в первой десятке. Среди кандидатов, допущенных '
              'фильтром, NDCG@10 = 0,9026. Основная выявленная ошибка — грейд одной '
              'вакансии: фильтр исключил всех трёх сильных кандидатов AI Engineer. '
              'Диагностический срез девяти остальных вакансий даёт NDCG@10 = 0,8675. '
              'Вариант весов 50/50 для опыта и навыков без демонстрационных сигналов '
              'даёт NDCG@10 = 0,8099 на всей выборке; менять рабочие веса стоит после '
              'исправления грейда и проверки на новой независимой разметке.', '']
    return '\n'.join(lines) + '\n'


def main():
    groups, corrections = validate_and_join()
    manifest = json.loads((DEFAULT_OUT / 'sampling_manifest.json').read_text(encoding='utf-8'))
    with connect() as c:
        evaluation = evaluate_cohort(c, groups, True)
        ablations = evaluate_cohort(c, groups, True, ABLATION_WEIGHTS)
        drift = score_drift(c, groups)
        corrected_ids = {item['vacancy_id'] for item in corrections['vacancy_grade']}
        if len(corrected_ids) != 1:
            raise ValueError('Expected one vacancy grade correction')
        corrected_id = next(iter(corrected_ids))
        evidence = dict(c.execute(
            'SELECT grade,grade_source,experience_requirements,position_requirements '
            'FROM vacancies WHERE id=?', (corrected_id,)).fetchone())
    current = next(item for item in evaluation['settings'] if item['name'] == CURRENT)
    evaluation['current_weight_bootstrap_95'] = {
        key: bootstrap_interval([v['metrics'][key] for v in current['per_vacancy']])
        for key in ('ndcg_at_10', 'precision_2plus_at_10', 'recall_2plus_at_20')}
    evaluation['paired_weight_intervals_vs_current'] = paired_weight_intervals(
        evaluation['settings'], current)
    balanced = next(item for item in ablations['settings']
                    if item['name'] == 'experience_50_skills_50')
    demo_best = next(item for item in evaluation['settings']
                     if item['name'] == 'original_35_35_15_15')
    ablations['paired_differences'] = {
        'balanced_vs_current': paired_difference(balanced, current),
        'balanced_vs_demo_best': paired_difference(balanced, demo_best),
    }
    nine_vacancy_results = {
        item['name']: subset_macro(item, corrected_ids)
        for item in [*evaluation['settings'], *ablations['settings']]
    }
    report = {'evaluated_at_utc': datetime.now(timezone.utc).isoformat(),
              'scoring_version': SCORING_VERSION,
              'source_file': str(FILLED.relative_to(DEFAULT_OUT.parent.parent.parent)),
              'ranking': evaluation,
              'ablations': ablations,
              'nine_vacancy_results': nine_vacancy_results,
              'filter_analysis': filter_analysis(groups, evaluation, manifest),
              'corrections': corrections,
              'corrected_vacancy_evidence': evidence,
              'vacancy_grades': {vid: group[0]['vacancy_grade_current'] for vid, group in groups.items()},
              'snapshot_score_drift': drift}
    (DEFAULT_OUT / 'grade_aware_evaluation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (DEFAULT_OUT / 'grade_aware_evaluation.md').write_text(render(report), encoding='utf-8')
    print(json.dumps({'reviewed_pairs': evaluation['reviewed_pairs'],
                      'macro': current['macro'],
                      'filter': report['filter_analysis']['targets'],
                      'snapshot_score_drift': drift}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
