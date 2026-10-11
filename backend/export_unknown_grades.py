"""Export every Unknown grade with full work context for human review."""

import argparse
import csv
import itertools
import json
from pathlib import Path

from .db import ROOT, connect


OUT = ROOT / 'out' / 'grade_estimation' / 'unknown_for_review.csv'
FIELDS = (
    'user_id', 'resume_id', 'specialization', 'position_name', 'typical_position',
    'experience_years', 'work_items_count', 'project_items_count',
    'work_experience', 'projects', 'source_work_experience_text',
    'skills_text', 'hard_skills', 'education_level', 'certificates',
    'achievements', 'additional_information', 'human_grade', 'reviewer_notes',
)


def format_item(item):
    title = (item['title'] or '').strip()
    company = (item['company'] or '').strip()
    start = (item['start_date'] or '').strip()
    end = (item['end_date'] or '').strip()
    header = title or 'Без названия'
    if company:
        header += f' — {company}'
    if start or end:
        header += f' [{start or "?"} — {end or "?"}]'
    description = (item['description'] or '').strip()
    return f'{header}\n{description}' if description else header


def export(db, output):
    if output.exists():
        raise FileExistsError(f'{output} already exists; preserve reviewer edits')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.tmp')
    if temporary.exists():
        temporary.unlink()
    count = 0
    with connect(db) as c, temporary.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        source = c.execute('''SELECT r.id resume_id,r.user_id,r.specialization,r.position_name,
            r.typical_position,r.experience_years,r.skills_text,r.hard_skills,
            r.education_level,r.certificates,r.achievements,r.additional_information,r.raw_json,
            w.kind,w.ordinal,w.title,w.company,w.start_date,w.end_date,w.description
            FROM candidate_grade_estimates g
            JOIN resumes r ON r.id=g.resume_id
            LEFT JOIN work_items w ON w.resume_id=r.id
            WHERE g.grade='Unknown'
            ORDER BY r.specialization,r.id,w.ordinal,w.id''')
        for _, joined in itertools.groupby(source, key=lambda row: row['resume_id']):
            rows = list(joined)
            resume = rows[0]
            work = [format_item(row) for row in rows if row['kind'] == 'work']
            projects = [format_item(row) for row in rows if row['kind'] == 'project']
            other = [format_item(row) for row in rows if row['kind'] == 'unclassified']
            raw = json.loads(resume['raw_json'] or '{}')
            row = {
                'user_id': resume['user_id'], 'resume_id': resume['resume_id'],
                'specialization': resume['specialization'] or '',
                'position_name': resume['position_name'] or '',
                'typical_position': resume['typical_position'] or '',
                'experience_years': resume['experience_years'],
                'work_items_count': len(work), 'project_items_count': len(projects),
                'work_experience': '\n\n'.join(work + other),
                'projects': '\n\n'.join(projects),
                'source_work_experience_text': raw.get('work_experience_text') or '',
                'skills_text': resume['skills_text'] or '',
                'hard_skills': resume['hard_skills'] or '',
                'education_level': resume['education_level'] or '',
                'certificates': resume['certificates'] or '',
                'achievements': resume['achievements'] or '',
                'additional_information': resume['additional_information'] or '',
                'human_grade': '', 'reviewer_notes': '',
            }
            writer.writerow(row)
            count += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary.replace(output)
    return {'rows': count, 'output': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'fsp.sqlite3')
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    print(json.dumps(export(args.db, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
