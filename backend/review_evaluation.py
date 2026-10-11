"""Join blinded human labels and evaluate the tested ranking weights."""

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from .db import ROOT, connect
from .ranking import rank_vacancy
from .ranking_cli import WEIGHT_SETS, dcg


OUT = ROOT / 'out' / 'four_signal_eval'
IMMUTABLE_FIELDS = (
    'vacancy_name', 'vacancy_experience_text', 'candidate_position', 'candidate_experience_blocks',
)


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        return reader.fieldnames, list(reader)


def validate_reviews(blank_path, filled_path, key_path, pairs_path):
    blank_fields, blank = read_csv(blank_path)
    filled_fields, filled = read_csv(filled_path)
    pair_fields, pairs = read_csv(pairs_path)
    key = json.loads(key_path.read_text(encoding='utf-8'))
    if blank_fields != filled_fields or len(blank) != len(filled) or len(filled) != len(key):
        raise ValueError('Review file structure or row count changed')
    blank_by_id = {row['review_id']: row for row in blank}
    filled_by_id = {row['review_id']: row for row in filled}
    if len(blank_by_id) != len(blank) or len(filled_by_id) != len(filled):
        raise ValueError('Duplicate review ID')
    if set(blank_by_id) != set(filled_by_id) or set(key) != set(filled_by_id):
        raise ValueError('Review IDs do not match the original blind sample')
    pair_by_id = {(row['vacancy_id'], row['resume_id']): row for row in pairs}
    if len(pair_by_id) != len(pairs) or len(pair_by_id) != len(key):
        raise ValueError('Evaluation source no longer matches the blind sample')

    joined = {}
    groups = defaultdict(list)
    for review_id, row in filled_by_id.items():
        original = blank_by_id[review_id]
        if any(row[field] != original[field] for field in IMMUTABLE_FIELDS):
            raise ValueError(f'Source text changed in {review_id}')
        rank_text = row['human_rank_1_best'].strip()
        label_text = row['relevance_0_3'].strip()
        if not rank_text.isdigit() or label_text not in ('0', '1', '2', '3'):
            raise ValueError(f'Missing or invalid rank/relevance in {review_id}')
        pair_key = (key[review_id]['vacancy_id'], key[review_id]['resume_id'])
        if pair_key not in pair_by_id:
            raise ValueError(f'Unknown evaluation pair in {review_id}')
        if row['vacancy_name'] != pair_by_id[pair_key]['vacancy_name']:
            raise ValueError(f'Vacancy name mismatch in {review_id}')
        item = {
            'review_id': review_id,
            'vacancy_id': pair_key[0],
            'resume_id': pair_key[1],
            'human_rank': int(rank_text),
            'relevance': int(label_text),
            'reviewer_notes': row['reviewer_notes'].strip(),
        }
        joined[pair_key] = item
        groups[pair_key[0]].append(item)
    for vacancy_id, group in groups.items():
        if sorted(row['human_rank'] for row in group) != list(range(1, len(group) + 1)):
            raise ValueError(f'Human ranks must be unique 1..{len(group)} for {vacancy_id}')
    return pair_fields, pairs, joined, groups


def merge_labels(pair_fields, pairs, joined, output):
    fields = list(pair_fields)
    if 'human_rank_1_best' not in fields:
        fields.append('human_rank_1_best')
    for row in pairs:
        label = joined[(row['vacancy_id'], row['resume_id'])]
        row['relevance_0_3'] = str(label['relevance'])
        row['reviewer_notes'] = label['reviewer_notes']
        row['human_rank_1_best'] = str(label['human_rank'])
    temp = output.with_suffix(output.suffix + '.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(pairs)
    temp.replace(output)


def ndcg(ordered, all_labels, k):
    ideal = sorted(all_labels, reverse=True)
    denominator = dcg(ideal, k)
    return dcg(ordered, k) / denominator if denominator else None


def spearman(model_ids, human_ranks):
    count = len(model_ids)
    if count < 2:
        return None
    human_order = sorted(model_ids, key=lambda resume_id: human_ranks[resume_id])
    human_positions = {resume_id: i for i, resume_id in enumerate(human_order, 1)}
    distance = sum((i - human_positions[resume_id]) ** 2 for i, resume_id in enumerate(model_ids, 1))
    return 1 - 6 * distance / (count * (count * count - 1))


def pairwise_accuracy(model_ids, human_ranks):
    count = 0
    concordant = 0
    for i, first in enumerate(model_ids):
        for second in model_ids[i + 1:]:
            count += 1
            concordant += human_ranks[first] < human_ranks[second]
    return concordant / count if count else None


def evaluate(db, groups):
    report = {'method': 'blind-reviewed pairs; specialization filter retained',
              'reviewed_pairs': sum(map(len, groups.values())), 'settings': []}
    with connect(db) as c:
        for name, weights in WEIGHT_SETS.items():
            per_vacancy = []
            for vacancy_id, group in groups.items():
                by_id = {row['resume_id']: row for row in group}
                human_ranks = {rid: row['human_rank'] for rid, row in by_id.items()}
                all_labels = [row['relevance'] for row in group]
                model = rank_vacancy(c, vacancy_id, limit=1000,
                                     candidate_ids=list(by_id), weights=weights)
                model_ids = [row['resume_id'] for row in model]
                labels = [by_id[rid]['relevance'] for rid in model_ids]
                excluded = [row for row in group if row['resume_id'] not in set(model_ids)]
                eligible_ideal = sorted(labels, reverse=True)
                human_top10 = {row['resume_id'] for row in group if row['human_rank'] <= 10}
                human_best_id = next(row['resume_id'] for row in group if row['human_rank'] == 1)
                positives = sum(label > 0 for label in all_labels)
                metrics = {
                    'vacancy_id': vacancy_id,
                    'vacancy_name': c.execute('SELECT vacancy_name FROM vacancies WHERE id=?', (vacancy_id,)).fetchone()[0],
                    'judged_pairs': len(group),
                    'eligible_candidates': len(model),
                    'label_distribution': dict(sorted(Counter(all_labels).items())),
                    'ndcg_at_10': round(ndcg(labels, all_labels, 10), 4),
                    'ndcg_at_20': round(ndcg(labels, all_labels, 20), 4),
                    'eligibility_ceiling_ndcg_at_10': round(ndcg(eligible_ideal, all_labels, 10), 4),
                    'ndcg_within_eligible_at_10': round(ndcg(labels, eligible_ideal, 10), 4),
                    'recall_at_20': round(sum(value > 0 for value in labels[:20]) / positives, 4) if positives else None,
                    'recall_strong_at_20': round(
                        sum(value >= 2 for value in labels[:20]) / sum(value >= 2 for value in all_labels), 4
                    ) if any(value >= 2 for value in all_labels) else None,
                    'excluded_positive_count': sum(row['relevance'] > 0 for row in excluded),
                    'excluded_strong_count': sum(row['relevance'] >= 2 for row in excluded),
                    'human_top10_overlap': len(human_top10.intersection(model_ids[:10])),
                    'eligible_spearman': round(spearman(model_ids, human_ranks), 4),
                    'eligible_pairwise_accuracy': round(pairwise_accuracy(model_ids, human_ranks), 4),
                    'human_rank_of_model_top1': human_ranks[model_ids[0]],
                    'model_rank_of_human_top1': model_ids.index(human_best_id) + 1 if human_best_id in model_ids else None,
                    'model_top10_review_ids': [by_id[rid]['review_id'] for rid in model_ids[:10]],
                }
                per_vacancy.append(metrics)
            macro_fields = ('ndcg_at_10', 'ndcg_at_20', 'ndcg_within_eligible_at_10',
                            'recall_at_20', 'recall_strong_at_20', 'human_top10_overlap',
                            'eligible_spearman', 'eligible_pairwise_accuracy')
            macro = {field: round(statistics.mean(v[field] for v in per_vacancy if v[field] is not None), 4)
                     for field in macro_fields}
            report['settings'].append({'name': name, 'weights': weights, 'macro': macro,
                                       'per_vacancy': per_vacancy})
    return report


def markdown(report):
    vacancy_count = len(report['settings'][0]['per_vacancy'])
    vacancy_word = ('вакансия' if vacancy_count % 10 == 1 and vacancy_count % 100 != 11
                    else 'вакансии' if vacancy_count % 10 in (2, 3, 4) and vacancy_count % 100 not in (12, 13, 14)
                    else 'вакансий')
    first_setting = report['settings'][0]['per_vacancy']
    eligible_counts = sorted({item['eligible_candidates'] for item in first_setting})
    judged_counts = sorted({item['judged_pairs'] for item in first_setting})
    eligible_text = ', '.join(map(str, eligible_counts))
    judged_text = ', '.join(map(str, judged_counts))
    lines = ['# Слепая оценка весов', '',
             f"{report['reviewed_pairs']} пар ({vacancy_count} {vacancy_word}) "
             'размечены в файле без модельных мест и скоров. '
             f'В ранжировании сохраняется фильтр специализации: в выдачу допущено {eligible_text} '
             f'из {judged_text} кандидатов на вакансию.', '',
             '| Опыт / навыки / активность / ФСП | NDCG@10 | NDCG@20 | Пересечение топ-10 | Spearman, внутри специализации |',
             '|---|---:|---:|---:|---:|']
    for setting in report['settings']:
        w = setting['weights']
        weight_text = ' / '.join(f'{w[k] * 100:g}' for k in ('experience', 'skills', 'activity', 'fsp'))
        m = setting['macro']
        lines.append(f"| {weight_text} | {m['ndcg_at_10']:.4f} | {m['ndcg_at_20']:.4f} | "
                     f"{m['human_top10_overlap']:.1f}/10 | {m['eligible_spearman']:.4f} |")
    lines += ['', 'NDCG использует оценки релевантности 0–3. Spearman сравнивает порядок модели и человека '
              'только среди кандидатов нужной специализации; диапазон от −1 до 1, больше — лучше. '
              'Это маленькая диагностическая выборка, поэтому различия между близкими весами не следует считать устойчивой оценкой продакшена.', '']
    selected = next(item for item in report['settings'] if item['name'] == 'experience_70_25_025_025')
    higher_experience = next(item for item in report['settings'] if item['name'] == 'experience_80_20_00_00')
    same_top10 = all(set(a['model_top10_review_ids']) == set(b['model_top10_review_ids'])
                     for a, b in zip(selected['per_vacancy'], higher_experience['per_vacancy']))
    lines += ['## Интерпретация', '',
              f"Разница NDCG@10 (80 / 20 минус 70 / 25): "
              f"{higher_experience['macro']['ndcg_at_10'] - selected['macro']['ndcg_at_10']:.4f}; "
              f"разница Spearman (70 / 25 минус 80 / 20): "
              f"{selected['macro']['eligible_spearman'] - higher_experience['macro']['eligible_spearman']:.4f}. "
              f"Состав топ-10 {'одинаков во всех вакансиях' if same_top10 else 'различается хотя бы в одной вакансии'}.", '',
              '| Вакансия | Потолок NDCG@10 из-за фильтра | NDCG@10, 70/25 | NDCG@10 среди допущенных | Положительных исключено | Из них 2–3 |',
              '|---|---:|---:|---:|---:|---:|']
    for item in selected['per_vacancy']:
        lines.append(f"| {item['vacancy_name']} | {item['eligibility_ceiling_ndcg_at_10']:.4f} | "
                     f"{item['ndcg_at_10']:.4f} | {item['ndcg_within_eligible_at_10']:.4f} | "
                     f"{item['excluded_positive_count']} | {item['excluded_strong_count']} |")
    lines += ['', 'Фильтр специализации выполняется до скоринга. Положительные оценки кандидатов с другой специализацией '
              'не могут быть исправлены изменением весов.', '']
    for setting in report['settings']:
        w = setting['weights']
        weight_text = ' / '.join(f'{w[k] * 100:g}' for k in ('experience', 'skills', 'activity', 'fsp'))
        lines.append(f'## {weight_text}')
        lines.append('')
        lines.append('| Вакансия | NDCG@10 | NDCG@20 | Совпадение топ-10 | Spearman | Место человеческого №1 |')
        lines.append('|---|---:|---:|---:|---:|---:|')
        for item in setting['per_vacancy']:
            place = item['model_rank_of_human_top1'] or 'вне специализации'
            lines.append(f"| {item['vacancy_name']} | {item['ndcg_at_10']:.4f} | "
                         f"{item['ndcg_at_20']:.4f} | {item['human_top10_overlap']}/10 | "
                         f"{item['eligible_spearman']:.4f} | {place} |")
        lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'fsp.sqlite3')
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--review', type=Path)
    args = parser.parse_args()
    out = args.out
    review = args.review or out / 'blind_review_filled.csv'
    fields, pairs, joined, groups = validate_reviews(
        out / 'blind_review.csv', review, out / '.blind_review_key.json', out / 'eval_pairs.csv')
    report = evaluate(args.db, groups)
    merge_labels(fields, pairs, joined, out / 'eval_pairs.csv')
    (out / 'human_weight_evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'human_weight_evaluation.md').write_text(markdown(report), encoding='utf-8')
    print(json.dumps({'reviewed_pairs': report['reviewed_pairs'], 'settings': [
        {'name': item['name'], **item['macro']} for item in report['settings']]}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
