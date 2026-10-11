"""Re-evaluate the current candidate ranker against all existing blind labels.

Run from site/: python3 -m backend.comprehensive_review_evaluation
Historical evaluation artifacts are read only; results are written to new files.
"""

import csv
import json
import random
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .db import ROOT, connect
from .ranking import SCORING_VERSION, rank_vacancy
from .ranking_cli import WEIGHT_SETS, dcg
from .review_evaluation import validate_reviews
from .profile import shared_technologies


OUT = ROOT / 'out' / 'four_signal_eval'
K_VALUES = (5, 10, 20)
THRESHOLDS = (1, 2, 3)
CURRENT = 'experience_70_25_025_025'


def load_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as source:
        rows = list(csv.DictReader(source))
    for row in rows:
        if row['relevance_0_3'] not in ('0', '1', '2', '3'):
            raise ValueError(f'Incomplete relevance label in {path}')
        if not row.get('human_rank_1_best', '').isdigit():
            raise ValueError(f'Incomplete human rank in {path}')
    return rows


def groups_for_rounds(*paths):
    groups = defaultdict(list)
    seen = set()
    for path in paths:
        for row in load_rows(path):
            key = (row['vacancy_id'], row['resume_id'])
            if key in seen:
                raise ValueError(f'Duplicate reviewed pair: {key}')
            seen.add(key)
            groups[row['vacancy_id']].append({
                'resume_id': row['resume_id'],
                'vacancy_name': row['vacancy_name'],
                'relevance': int(row['relevance_0_3']),
                'human_rank': int(row['human_rank_1_best']),
            })
    return dict(groups)


def verify_blind_reviews():
    for directory, filename in ((OUT, 'blind_review_filled.csv'),
                                (OUT / 'round2', 'blind_review_2_filled.csv'),
                                (OUT / 'round3', 'blind_review_3_filled.csv')):
        _, pairs, joined, _ = validate_reviews(
            directory / 'blind_review.csv', directory / filename,
            directory / '.blind_review_key.json', directory / 'eval_pairs.csv')
        for row in pairs:
            key = row['vacancy_id'], row['resume_id']
            if (row['relevance_0_3'] != str(joined[key]['relevance']) or
                    row['human_rank_1_best'] != str(joined[key]['human_rank'])):
                raise ValueError(f'Existing evaluation labels differ from blind review: {key}')


def ndcg(ordered, ideal, k):
    ceiling = dcg(ideal, k)
    return dcg(ordered, k) / ceiling if ceiling else None


def spearman(ids, ranks):
    n = len(ids)
    if n < 2:
        return None
    order = sorted(ids, key=lambda rid: ranks[rid])
    positions = {rid: i for i, rid in enumerate(order, 1)}
    distance = sum((i - positions[rid]) ** 2 for i, rid in enumerate(ids, 1))
    return 1 - 6 * distance / (n * (n * n - 1))


def pairwise_accuracy(ids, ranks):
    pairs = [(left, right) for i, left in enumerate(ids) for right in ids[i + 1:]]
    return sum(ranks[left] < ranks[right] for left, right in pairs) / len(pairs) if pairs else None


def label_auc(ids, labels, scores, threshold):
    positive = [rid for rid in ids if labels[rid] >= threshold]
    negative = [rid for rid in ids if labels[rid] < threshold]
    if not positive or not negative:
        return None
    return sum(1.0 if scores[p] > scores[n] else 0.5 if scores[p] == scores[n] else 0.0
               for p in positive for n in negative) / (len(positive) * len(negative))


def binary_metrics(ordered, all_values, k, threshold):
    hits = 0
    precision_sum = 0.0
    first = None
    for i, label in enumerate(ordered[:k], 1):
        if label >= threshold:
            hits += 1
            precision_sum += hits / i
            if first is None:
                first = i
    total = sum(value >= threshold for value in all_values)
    precision = hits / k
    recall = hits / total if total else None
    return {
        'precision': precision,
        'recall': recall,
        'f1': 2 * precision * recall / (precision + recall) if recall and precision else 0.0 if total else None,
        'average_precision': precision_sum / total if total else None,
        'reciprocal_rank': 1 / first if first else 0.0 if total else None,
        'hit': int(hits > 0),
        'count': hits,
    }


def filter_reasons(c, vacancy_id, reviewed_ids):
    vacancy = c.execute('SELECT specialization,grade,required_technologies_json FROM vacancies WHERE id=?',
                        (vacancy_id,)).fetchone()
    required = set(json.loads(vacancy['required_technologies_json'] or '[]'))
    reasons = {}
    for offset in range(0, len(reviewed_ids), 400):
        batch = reviewed_ids[offset:offset + 400]
        marks = ','.join('?' for _ in batch)
        rows = c.execute(f'''SELECT r.id,r.specialization,r.specializations_json,r.all_technologies_json,
            u.origin,cp.profile_json,
            CASE WHEN u.origin='registration' THEN sg.current_grade ELSE cg.grade END candidate_grade
            FROM resumes r JOIN users u ON u.id=r.user_id
            LEFT JOIN candidate_profiles cp ON cp.user_id=r.user_id
            LEFT JOIN candidate_grades cg ON cg.user_id=r.user_id
            LEFT JOIN candidate_specialization_grades sg ON sg.user_id=r.user_id AND sg.specialization=?
            WHERE r.id IN ({marks})''', [vacancy['specialization'], *batch]).fetchall()
        for row in rows:
            specializations = json.loads(row['specializations_json'] or '[]') if row['origin'] == 'registration' else []
            if row['specialization'] != vacancy['specialization'] and vacancy['specialization'] not in specializations:
                reason = 'specialization'
            elif row['candidate_grade'] != vacancy['grade']:
                reason = 'grade'
            else:
                if row['origin'] == 'registration':
                    profile = json.loads(row['profile_json'] or '{}')
                    stack = set(shared_technologies(profile))
                else:
                    stack = set(json.loads(row['all_technologies_json'] or '[]'))
                reason = 'required_technologies' if not required.issubset(stack) else 'eligible'
            reasons[row['id']] = reason
    if set(reasons) != set(reviewed_ids):
        raise ValueError('Some reviewed resumes are no longer present in the database')
    return reasons


def evaluate_vacancy(c, vacancy_id, group, weights, comparable_human_ranks=True):
    labels = {row['resume_id']: row['relevance'] for row in group}
    ranks = {row['resume_id']: row['human_rank'] for row in group}
    model = rank_vacancy(c, vacancy_id, limit=1000, candidate_ids=list(labels), weights=weights)
    ids = [row['resume_id'] for row in model]
    scores = {row['resume_id']: row['score'] for row in model}
    values = [labels[rid] for rid in ids]
    all_values = list(labels.values())
    ideal = sorted(all_values, reverse=True)
    eligible_ideal = sorted(values, reverse=True)
    reasons = filter_reasons(c, vacancy_id, list(labels))
    if set(ids) != {rid for rid, reason in reasons.items() if reason == 'eligible'}:
        raise ValueError('Filter diagnostic differs from current ranking output')
    excluded = [labels[rid] for rid in labels if rid not in set(ids)]
    metrics = {
        'reviewed': len(group),
        'eligible': len(ids),
        'filter_coverage': len(ids) / len(group),
        'filter_reasons': dict(Counter(reasons.values())),
        'excluded_relevant_1plus': sum(v >= 1 for v in excluded),
        'excluded_strong_2plus': sum(v >= 2 for v in excluded),
        'label_distribution': {str(i): all_values.count(i) for i in range(4)},
        'eligible_label_distribution': {str(i): values.count(i) for i in range(4)},
        'no_experience_vector_eligible': sum(row['experience_blocks_found'] == 0 for row in model),
        'top10_label_distribution': {str(i): values[:10].count(i) for i in range(4)},
        'score_of_top1': model[0]['score'] if model else None,
    }
    for k in K_VALUES:
        metrics[f'ndcg_at_{k}'] = ndcg(values, ideal, k)
        metrics[f'ndcg_ceiling_at_{k}'] = ndcg(eligible_ideal, ideal, k)
        metrics[f'ndcg_within_eligible_at_{k}'] = ndcg(values, eligible_ideal, k)
        for threshold in THRESHOLDS:
            binary = binary_metrics(values, all_values, k, threshold)
            for name, result in binary.items():
                metrics[f'{name}_{threshold}plus_at_{k}'] = result
    for threshold in THRESHOLDS:
        metrics[f'label_auc_{threshold}plus_eligible'] = label_auc(ids, labels, scores, threshold)
        total = sum(v >= threshold for v in all_values)
        retained = sum(labels[rid] >= threshold for rid in ids)
        metrics[f'filter_recall_{threshold}plus'] = retained / total if total else None
    if comparable_human_ranks:
        if sorted(ranks.values()) != list(range(1, len(group) + 1)):
            raise ValueError(f'Human ranks are not a single ordering for {vacancy_id}')
        accuracy = pairwise_accuracy(ids, ranks)
        human_best = min(ranks, key=ranks.get)
        metrics.update({
            'spearman_eligible': spearman(ids, ranks),
            'pairwise_accuracy_eligible': accuracy,
            'kendall_tau_eligible': 2 * accuracy - 1 if accuracy is not None else None,
            'human_top5_overlap': len(set(ids[:5]) & {rid for rid, rank in ranks.items() if rank <= 5}),
            'human_top10_overlap': len(set(ids[:10]) & {rid for rid, rank in ranks.items() if rank <= 10}),
            'model_rank_of_human_best': ids.index(human_best) + 1 if human_best in ids else None,
            'human_rank_of_model_best': ranks[ids[0]] if ids else None,
        })
    return metrics


def aggregate(per_vacancy):
    metric_names = set().union(*(set(v['metrics']) for v in per_vacancy))
    macro = {}
    for name in sorted(metric_names):
        if name in ('filter_reasons', 'label_distribution', 'eligible_label_distribution',
                    'top10_label_distribution'):
            continue
        values = [v['metrics'][name] for v in per_vacancy if v['metrics'][name] is not None]
        macro[name] = round(statistics.mean(values), 4) if values else None
    return macro


def evaluate_cohort(c, groups, comparable_human_ranks, weight_sets=None):
    output = {'reviewed_pairs': sum(map(len, groups.values())), 'vacancies': len(groups), 'settings': []}
    for name, weights in (weight_sets if weight_sets is not None else WEIGHT_SETS).items():
        per_vacancy = []
        for vid, group in groups.items():
            metrics = evaluate_vacancy(c, vid, group, weights, comparable_human_ranks)
            per_vacancy.append({'vacancy_id': vid, 'vacancy_name': group[0]['vacancy_name'],
                                'metrics': metrics})
        output['settings'].append({'name': name, 'weights': weights, 'macro': aggregate(per_vacancy),
                                   'per_vacancy': per_vacancy})
    return output


def bootstrap_interval(values, repetitions=20000):
    rng = random.Random(20261011)
    draws = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(repetitions))
    return [round(draws[int(repetitions * 0.025)], 4),
            round(draws[int(repetitions * 0.975)], 4)]


def paired_weight_intervals(settings, current):
    reference = {item['vacancy_id']: item['metrics']['ndcg_at_10'] for item in current['per_vacancy']}
    result = {}
    for setting in settings:
        if setting['name'] == current['name']:
            continue
        differences = [item['metrics']['ndcg_at_10'] - reference[item['vacancy_id']]
                       for item in setting['per_vacancy']]
        result[setting['name']] = {'mean_difference': round(statistics.mean(differences), 4),
                                   'bootstrap_95': bootstrap_interval(differences)}
    return result


def benchmark_full_search(c, vacancy_ids):
    measurements = []
    for vacancy_id in vacancy_ids:
        started = time.perf_counter()
        rows = rank_vacancy(c, vacancy_id, limit=20)
        measurements.append({'vacancy_id': vacancy_id, 'seconds': round(time.perf_counter() - started, 4),
                             'returned': len(rows)})
    seconds = [item['seconds'] for item in measurements]
    return {'queries': len(measurements), 'median_seconds': round(statistics.median(seconds), 4),
            'minimum_seconds': round(min(seconds), 4), 'maximum_seconds': round(max(seconds), 4),
            'measurements': measurements,
            'scope': 'Full local database search, top 20, precomputed Jina vectors; excludes indexing, API and browser'}


def render_markdown(report):
    main = report['main_300']
    ai = report['ai_130']
    current = next(x for x in main['settings'] if x['name'] == CURRENT)
    ai_current = next(x for x in ai['settings'] if x['name'] == CURRENT)
    m, a = current['macro'], ai_current['macro']
    key_metrics = (
        ('ndcg_at_5', 'NDCG@5, весь размеченный пул'),
        ('ndcg_at_10', 'NDCG@10, весь размеченный пул'),
        ('ndcg_at_20', 'NDCG@20, весь размеченный пул'),
        ('ndcg_within_eligible_at_10', 'NDCG@10 среди допущенных'),
        ('ndcg_ceiling_at_10', 'Потолок NDCG@10 при фильтре'),
        ('precision_1plus_at_10', 'Precision@10, оценки 1–3'),
        ('precision_2plus_at_5', 'Precision@5, оценки 2–3'),
        ('precision_2plus_at_10', 'Precision@10, оценки 2–3'),
        ('precision_3plus_at_10', 'Precision@10, оценка 3'),
        ('recall_1plus_at_20', 'Recall@20, оценки 1–3'),
        ('recall_2plus_at_5', 'Recall@5, оценки 2–3'),
        ('recall_2plus_at_10', 'Recall@10, оценки 2–3'),
        ('recall_2plus_at_20', 'Recall@20, оценки 2–3'),
        ('average_precision_1plus_at_20', 'AP@20, оценки 1–3'),
        ('average_precision_2plus_at_20', 'AP@20, оценки 2–3'),
        ('reciprocal_rank_2plus_at_20', 'MRR@20, оценки 2–3'),
        ('hit_2plus_at_10', 'Hit@10, оценки 2–3'),
        ('label_auc_2plus_eligible', 'ROC AUC по скору, оценки 2–3 среди допущенных'),
        ('filter_recall_1plus', 'Доля допущенных, оценки 1–3'),
        ('filter_recall_2plus', 'Доля допущенных, оценки 2–3'),
        ('spearman_eligible', 'Spearman среди допущенных'),
        ('pairwise_accuracy_eligible', 'Согласие порядка пар среди допущенных'),
        ('kendall_tau_eligible', 'Kendall tau среди допущенных'),
        ('human_top5_overlap', 'Пересечение с человеческим топ-5, человек'),
        ('human_top10_overlap', 'Пересечение с человеческим топ-10, человек'),
    )
    lines = ['# Повторная оценка текущего ранжирования', '',
             f"Расчёт: {report['evaluated_at_utc']}. Версия скоринга: `{report['scoring_version']}`. "
             'Использована текущая локальная SQLite, прежние файлы разметки не менялись.', '',
             'Основная выборка: 300 пар, 10 вакансий по 30 кандидатов. '
             'Дополнительная: 130 кандидатов для одной вакансии AI Engineer; '
             '30 и 100 были размечены отдельными раундами. Средние по десяти вакансиям '
             'считаются по вакансиям, а не по парам.', '',
             '## Основные результаты при действующих весах 70 / 25 / 2,5 / 2,5', '',
             '| Метрика | 300 пар, макро | AI Engineer, 130 |', '|---|---:|---:|']
    for name, label in key_metrics:
        left, right = m.get(name), a.get(name)
        lines.append(f"| {label} | {left:.4f} | {right:.4f} |" if left is not None and right is not None
                     else f"| {label} | {left:.4f} | — |" if left is not None
                     else f"| {label} | — | {right:.4f} |" if right is not None
                     else f'| {label} | — | — |')
    lines += ['', '## Сравнение весов: все шесть вариантов', '',
              '| Опыт / навыки / активность / ФСП | NDCG@10 | P@10, 2–3 | Recall@10, 2–3 | AP@20, 2–3 | Spearman |',
              '|---|---:|---:|---:|---:|---:|']
    for setting in main['settings']:
        w = setting['weights']
        label = ' / '.join(f"{w[k] * 100:g}" for k in ('experience', 'skills', 'activity', 'fsp'))
        x = setting['macro']
        lines.append(f"| {label} | {x['ndcg_at_10']:.4f} | {x['precision_2plus_at_10']:.4f} | "
                     f"{x['recall_2plus_at_10']:.4f} | {x['average_precision_2plus_at_20']:.4f} | "
                     f"{x['spearman_eligible']:.4f} |")
    lines += ['', '## Фильтр и ограничения оценки', '',
              f"По основной выборке допущено в среднем {m['eligible']:.1f} из 30 кандидатов на вакансию. "
              f"За пределами выдачи: {sum(v['metrics']['excluded_relevant_1plus'] for v in current['per_vacancy'])} "
              f"с оценкой 1–3, в том числе {sum(v['metrics']['excluded_strong_2plus'] for v in current['per_vacancy'])} "
              'с оценкой 2–3.',
              f"Для AI Engineer допущено {a['eligible']:.0f} из 130; за фильтром "
              f"{a['excluded_relevant_1plus']:.0f} положительных, включая "
              f"{a['excluded_strong_2plus']:.0f} сильных. "
              f"Потолок NDCG@10 при фильтре — {a['ndcg_ceiling_at_10']:.4f}.", '',
              'NDCG использует оценки 0–3 и gain `2^relevance − 1`; идеальный порядок строится '
              'по всем размеченным кандидатам, включая отсеянных. P@K делит число найденных '
              'релевантных на K, Recall@K — на число релевантных во всём размеченном пуле. '
              'AP@20 использует этот же общий знаменатель. Spearman и pairwise accuracy '
              'считаются только среди допущенных кандидатов.', '',
              'Для объединённых 130 кандидатов нет единого человеческого порядка: '
              'их места выставлены отдельно в группах по 30 и 100. Поэтому ранговые '
              'метрики для этой группы не рассчитаны. В слепом файле показывались обязанности '
              'вакансии и опыт кандидата; грейд, стек и зарплата не размечались как отдельные '
              'условия соответствия. Поэтому исключение по грейду нельзя назвать ошибкой '
              'только на основании этой разметки. Выборки стратифицированы и '
              'не представляют случайный поток вакансий. У импортированных резюме '
              'тесты, активность и ФСП остаются демонстрационными.', '']
    intervals = report['main_300']['current_weight_bootstrap_95']
    lines += ['## Разброс по десяти вакансиям', '',
              'Ориентировочные 95% интервалы получены bootstrap по вакансиям, 20 000 повторов.', '',
              '| Метрика | Интервал 95% |', '|---|---:|']
    for field, bounds in intervals.items():
        lines.append(f'| {field} | [{bounds[0]:.4f}; {bounds[1]:.4f}] |')
    lines += ['', 'Разницы NDCG@10 относительно действующих весов, парный bootstrap по вакансиям:', '',
              '| Веса | Разница | Интервал 95% |', '|---|---:|---:|']
    for setting in main['settings']:
        if setting['name'] == CURRENT:
            continue
        w = setting['weights']
        label = ' / '.join(f"{w[k] * 100:g}" for k in ('experience', 'skills', 'activity', 'fsp'))
        delta = main['paired_weight_intervals_vs_current'][setting['name']]
        lo, hi = delta['bootstrap_95']
        lines.append(f"| {label} | {delta['mean_difference']:+.4f} | [{lo:+.4f}; {hi:+.4f}] |")
    lines += ['', '## По вакансиям, действующие веса', '',
              '| Вакансия | Допущено | NDCG@10 | P@10, 2–3 | Recall@20, 2–3 | Специализация / грейд / обязательные навыки: исключено |',
              '|---|---:|---:|---:|---:|---:|']
    for item in current['per_vacancy']:
        x = item['metrics']
        reasons = x['filter_reasons']
        breakdown = ' / '.join(str(reasons.get(k, 0)) for k in ('specialization', 'grade', 'required_technologies'))
        lines.append(f"| {item['vacancy_name']} | {x['eligible']}/{x['reviewed']} | "
                     f"{x['ndcg_at_10']:.4f} | {x['precision_2plus_at_10']:.4f} | "
                     f"{x['recall_2plus_at_20']:.4f} | {breakdown} |")
    runtime = report['local_runtime']
    lines += ['', '## Скорость локального поиска', '',
              f"На десяти полных запросах к текущей БД медиана {runtime['median_seconds']:.3f} с, "
              f"диапазон {runtime['minimum_seconds']:.3f}–{runtime['maximum_seconds']:.3f} с. "
              'Это топ-20 при заранее рассчитанных векторах Jina; время индексации, API и браузера '
              'не входит в замер. Один локальный прогон не является серверным SLA.', '']
    return '\n'.join(lines) + '\n'


def main():
    verify_blind_reviews()
    main_groups = groups_for_rounds(OUT / 'eval_pairs.csv', OUT / 'round2' / 'eval_pairs.csv')
    if len(main_groups) != 10 or sum(map(len, main_groups.values())) != 300:
        raise ValueError('Expected 300 reviewed pairs across 10 vacancies')
    ai_id = next(vid for vid, group in main_groups.items() if group[0]['vacancy_name'] == 'AI Engineer / AI-разработчик')
    ai_groups = groups_for_rounds(OUT / 'round2' / 'eval_pairs.csv', OUT / 'round3' / 'eval_pairs.csv')
    if len(ai_groups[ai_id]) != 130:
        raise ValueError('Expected 130 distinct AI Engineer reviews')
    with connect() as c:
        main_report = evaluate_cohort(c, main_groups, True)
        ai_report = evaluate_cohort(c, {ai_id: ai_groups[ai_id]}, False)
        runtime = benchmark_full_search(c, list(main_groups))
    current = next(x for x in main_report['settings'] if x['name'] == CURRENT)
    main_report['current_weight_bootstrap_95'] = {
        name: bootstrap_interval([v['metrics'][name] for v in current['per_vacancy']])
        for name in ('ndcg_at_10', 'precision_2plus_at_10', 'recall_2plus_at_20')
    }
    main_report['paired_weight_intervals_vs_current'] = paired_weight_intervals(
        main_report['settings'], current)
    report = {'evaluated_at_utc': datetime.now(timezone.utc).isoformat(),
              'scoring_version': SCORING_VERSION,
              'source_files': ['out/four_signal_eval/eval_pairs.csv',
                               'out/four_signal_eval/round2/eval_pairs.csv',
                               'out/four_signal_eval/round3/eval_pairs.csv'],
              'main_300': main_report, 'ai_130': ai_report,
              'local_runtime': runtime}
    (OUT / 'current_comprehensive_evaluation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'current_comprehensive_evaluation.md').write_text(render_markdown(report), encoding='utf-8')
    print(json.dumps({'main_300': current['macro'],
                      'ai_130': next(x for x in ai_report['settings'] if x['name'] == CURRENT)['macro']},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
