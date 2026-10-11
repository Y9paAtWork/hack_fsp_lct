"""Validate reviewed Unknown grades and build the three-grade candidate catalog."""

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from .db import ROOT, connect, init_db, now


OUT = ROOT / 'out' / 'grade_estimation'
GRADES = ('Junior', 'Middle', 'Senior')
EDITABLE = {'human_grade', 'reviewer_notes'}
FIELDS = (
    'user_id', 'resume_id', 'specialization', 'position_name', 'grade', 'grade_source',
    'confidence', 'declared_years', 'effective_it_years', 'it_work_blocks',
    'it_project_blocks', 'reviewer_notes',
)


def read(path):
    with path.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        return reader.fieldnames, list(reader)


def validate_review(original_path, reviewed_path):
    original_fields, original_rows = read(original_path)
    reviewed_fields, reviewed_rows = read(reviewed_path)
    if original_fields != reviewed_fields or len(original_rows) != len(reviewed_rows):
        raise ValueError('Reviewed file columns or row count changed')
    original = {row['resume_id']: row for row in original_rows}
    reviewed = {row['resume_id']: row for row in reviewed_rows}
    if len(original) != len(original_rows) or len(reviewed) != len(reviewed_rows) or original.keys() != reviewed.keys():
        raise ValueError('Resume IDs are duplicated or differ from the original export')
    fixed_fields = [field for field in original_fields if field not in EDITABLE]
    for resume_id, row in reviewed.items():
        if any(row[field] != original[resume_id][field] for field in fixed_fields):
            raise ValueError(f'Original data changed for resume {resume_id}')
        if row['human_grade'].strip() not in GRADES:
            raise ValueError(f'Missing or invalid grade for resume {resume_id}')
    return reviewed


def apply(db, original_path, reviewed_path, out):
    reviewed = validate_review(original_path, reviewed_path)
    digest = hashlib.sha256(reviewed_path.read_bytes()).hexdigest()
    init_db(db)
    counts = Counter()
    source_counts = Counter()
    source_grade = defaultdict(Counter)
    by_specialization = defaultdict(Counter)
    timestamp = now()
    rows = []
    db_rows = []
    with connect(db) as c:
        estimates = c.execute('''SELECT e.user_id,e.resume_id,e.grade,e.confidence,e.declared_years,
            e.effective_it_years,e.it_work_blocks,e.it_project_blocks,e.heuristic_version,
            r.specialization,r.position_name
            FROM candidate_grade_estimates e JOIN resumes r ON r.id=e.resume_id
            ORDER BY r.specialization,e.resume_id''')
        unknown_ids = set()
        for estimate in estimates:
            resume_id = estimate['resume_id']
            if estimate['grade'] == 'Unknown':
                unknown_ids.add(resume_id)
                label = reviewed.get(resume_id)
                if label is None or label['user_id'] != estimate['user_id']:
                    raise ValueError(f'Missing or mismatched review for {resume_id}')
                grade = label['human_grade'].strip()
                source = 'review_file'
                confidence = None
                notes = label['reviewer_notes'].strip()
                source_version = f'sha256:{digest}'
            else:
                if estimate['grade'] not in GRADES:
                    raise ValueError(f'Invalid heuristic grade for {resume_id}')
                grade = estimate['grade']
                source = 'heuristic'
                confidence = estimate['confidence']
                notes = ''
                source_version = estimate['heuristic_version']
            export = {
                'user_id': estimate['user_id'], 'resume_id': resume_id,
                'specialization': estimate['specialization'] or '',
                'position_name': estimate['position_name'] or '',
                'grade': grade, 'grade_source': source, 'confidence': confidence or '',
                'declared_years': estimate['declared_years'],
                'effective_it_years': estimate['effective_it_years'],
                'it_work_blocks': estimate['it_work_blocks'],
                'it_project_blocks': estimate['it_project_blocks'],
                'reviewer_notes': notes,
            }
            rows.append(export)
            db_rows.append((estimate['user_id'], resume_id, grade, source, confidence,
                            notes, source_version, timestamp))
            counts[grade] += 1
            source_counts[source] += 1
            source_grade[source][grade] += 1
            by_specialization[estimate['specialization'] or ''][grade] += 1
        if unknown_ids != reviewed.keys():
            raise ValueError('Reviewed IDs differ from current Unknown estimates')
        if len(rows) != c.execute('SELECT count(*) FROM resumes').fetchone()[0]:
            raise ValueError('Final grade catalog does not cover all resumes')
        c.executemany('''INSERT INTO candidate_grades VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET resume_id=excluded.resume_id,
            grade=excluded.grade,grade_source=excluded.grade_source,
            confidence=excluded.confidence,reviewer_notes=excluded.reviewer_notes,
            source_version=excluded.source_version,updated_at=excluded.updated_at''', db_rows)
        c.execute('DELETE FROM candidate_grades WHERE user_id NOT IN (SELECT user_id FROM resumes)')
    out.mkdir(parents=True, exist_ok=True)
    target = out / 'candidate_grades.csv'
    temp = target.with_suffix(target.suffix + '.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(target)
    report = {
        'total': len(rows), 'review_file_sha256': digest,
        'grades': dict(counts), 'source_counts': dict(source_counts),
        'grades_by_source': {source: dict(values) for source, values in source_grade.items()},
        'grades_by_specialization': {spec: dict(values) for spec, values in sorted(by_specialization.items())},
        'meaning': 'three-category grade from reviewed Unknown rows plus normalized heuristic estimates; not independently validated',
    }
    (out / 'final_grade_statistics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Итоговые три грейда', '',
             f"Всего {len(rows):,} кандидатов. Грейды `Intern` включены в `Junior`, `Lead` — в `Senior`. "
             'Оценки из присланного файла сохранены с источником `review_file`; '
             'остальные — с источником `heuristic`. Данные доступны в таблице `candidate_grades` '
             'и файле `candidate_grades.csv`.', '',
             '| Грейд | Кандидатов | Доля | Из файла разметки | По эвристике |',
             '|---|---:|---:|---:|---:|']
    for grade in GRADES:
        n = counts[grade]
        lines.append(f"| {grade} | {n:,} | {n / len(rows):.1%} | "
                     f"{source_grade['review_file'][grade]:,} | {source_grade['heuristic'][grade]:,} |")
    lines += ['', '## По специализациям', '',
              '| Специализация | Junior | Middle | Senior |', '|---|---:|---:|---:|']
    for spec, values in sorted(by_specialization.items(), key=lambda item: -sum(item[1].values())):
        lines.append(f"| {spec} | {values['Junior']:,} | {values['Middle']:,} | {values['Senior']:,} |")
    lines += ['', 'Разметка из файла принята как предоставленная оценка. '
              'Преобладание Junior среди прежде не определённых случаев может отражать '
              'правило разметки при недостатке данных; независимая проверка качества пока не проводилась. '
              'Грейд не участвует в ранжировании вакансий.', '']
    (out / 'final_grade_statistics.md').write_text('\n'.join(lines), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'fsp.sqlite3')
    parser.add_argument('--original', type=Path, default=OUT / 'unknown_for_review.csv')
    parser.add_argument('--reviewed', type=Path, default=OUT / 'unknown_for_review_graded.csv')
    parser.add_argument('--out', type=Path, default=OUT)
    args = parser.parse_args()
    result = apply(args.db, args.original, args.reviewed, args.out)
    print(json.dumps({key: result[key] for key in ('total', 'grades', 'source_counts', 'grades_by_source')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
