"""Create a blinded human-review file for the fixed ranking evaluation sample."""

import argparse
import csv
import json
import secrets
from pathlib import Path

from .db import ROOT, connect


DEFAULT_DIR = ROOT / 'out' / 'four_signal_eval'
FIELDS = [
    'review_id', 'vacancy_name', 'vacancy_experience_text',
    'candidate_position', 'candidate_experience_blocks',
    'human_rank_1_best', 'relevance_0_3', 'reviewer_notes',
]


def experience_text(c, resume_id):
    blocks = list(c.execute('''SELECT kind,title,company,description FROM work_items
        WHERE resume_id=? ORDER BY ordinal,id''', (resume_id,)))
    if not blocks:
        return 'Выделенных блоков работы или проектов нет.'
    parts = []
    for index, block in enumerate(blocks, 1):
        kind = 'Проект' if block['kind'] == 'project' else 'Работа'
        title = (block['title'] or '').strip()
        company = (block['company'] or '').strip()
        heading = f'{index}. {kind}'
        if title:
            heading += f' — {title}'
        if company:
            heading += f' ({company})'
        parts.append(f'{heading}\n{(block["description"] or "").strip()}')
    return '\n\n'.join(parts)


def export(source, db, output, key_path):
    if output.exists() or key_path.exists():
        raise FileExistsError('Review export already exists; preserve reviewer edits and the matching key')
    with source.open(encoding='utf-8-sig', newline='') as file:
        pairs = list(csv.DictReader(file))
    if not pairs:
        raise ValueError('Evaluation sample is empty')
    if any(row.get('relevance_0_3', '').strip() or row.get('reviewer_notes', '').strip() for row in pairs):
        raise ValueError('Source sample already contains review data; do not create a new blind key')

    grouped = {}
    with connect(db) as c:
        for row in pairs:
            review_id = 'R-' + secrets.token_hex(5).upper()
            grouped.setdefault(row['vacancy_id'], []).append({
                'review_id': review_id,
                'vacancy_name': row['vacancy_name'],
                'vacancy_experience_text': row['vacancy_experience_text'],
                'candidate_position': row['position_name'],
                'candidate_experience_blocks': experience_text(c, row['resume_id']),
                'human_rank_1_best': '', 'relevance_0_3': '', 'reviewer_notes': '',
                '_vacancy_id': row['vacancy_id'], '_resume_id': row['resume_id'],
            })
    shuffled = []
    for group in grouped.values():
        secrets.SystemRandom().shuffle(group)
        shuffled.extend(group)
    ids = [row['review_id'] for row in shuffled]
    if len(set(ids)) != len(ids):
        raise RuntimeError('Duplicate review ID')
    key = {row['review_id']: {'vacancy_id': row['_vacancy_id'], 'resume_id': row['_resume_id']}
           for row in shuffled}

    output.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({field: row[field] for field in FIELDS} for row in shuffled)
    key_path.write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'rows': len(shuffled), 'vacancies': len(grouped), 'output': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_DIR / 'eval_pairs.csv')
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'fsp.sqlite3')
    parser.add_argument('--output', type=Path, default=DEFAULT_DIR / 'blind_review.csv')
    parser.add_argument('--key', type=Path, default=DEFAULT_DIR / '.blind_review_key.json')
    args = parser.parse_args()
    print(json.dumps(export(args.source, args.db, args.output, args.key), ensure_ascii=False))


if __name__ == '__main__':
    main()
