"""Estimate candidate seniority from IT work history, titles and years.

These are heuristic labels, not verified grades. They do not change ranking.
"""

import argparse
import csv
import itertools
import json
import random
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from .db import ROOT, connect, init_db, now


OUT = ROOT / 'out' / 'grade_estimation'
VERSION = 'it-grade-heuristic-v3-three-grades'
TODAY = date.today()

IT_TITLE = re.compile(
    r'программист|разработчик|developer|software|frontend|front.?end|backend|back.?end|fullstack|full.?stack|'
    r'devops|\bsre\b|\bqa\b|тестировщик|тестировщиц|инженер по тестированию|'
    r'data.?scientist|data.?engineer|data.?analyst|\bml[ -]?(?:инженер|engineer)|'
    r'аналитик данных|системн\w* аналитик|информационн\w* безопасн|кибербезопасн|'
    r'системн\w* администратор|сетев\w* инженер|администратор баз\w* данн|database|'
    r'\bit[ -]?специалист|\bит[ -]?специалист|веб.?разработ|web.?разработ|'
    r'техническ\w* поддержк|администратор\s*бд|инженер\s*по\s*автоматизац', re.I)
IT_CONTEXT = re.compile(
    r'\bpython\b|\bjava\b|javascript|typescript|\bsql\b|\bapi\b|\bdocker\b|kubernetes|'
    r'\blinux\b|\bgit\b|\breact\b|\bnode\b|postgres|\b1[сc]\b|\bml\b|нейросет|'
    r'машинн\w* обучен|баз\w* данн|программн\w* обеспечен|разработк\w* (?:сайт|приложен|сервис|систем|по\b)|'
    r'написан\w* код|код.?ревью|code review|администрирован\w* (?:сервер|сет|систем)|'
    r'автоматизац\w* тестирован|тестирован\w* (?:по|программ|систем|приложен)|'
    r'информационн\w* систем|веб.?приложен', re.I)
NON_IT_TITLE = re.compile(
    r'бухгалтер|кассир|продавец|кладовщик|уборщик|разнорабоч|слесарь|оператор лини|'
    r'сборщик|водитель|повар|официант|курьер|охранник|грузчик|менеджер по продаж', re.I)
TITLE_MARKERS = (
    ('Lead', re.compile(r'\bteam.?lead\b|\btech.?lead\b|тимлид|техлид|руководител\w* (?:групп|команд|отдел)|head of', re.I)),
    ('Senior', re.compile(r'\bsenior\b|\bsr\.?\b|\bсеньор\b|ведущ\w*|главн\w*|старш\w*', re.I)),
    ('Middle', re.compile(r'\bmiddle\b|\bмидл\w*', re.I)),
    ('Junior', re.compile(r'\bjunior\b|\bjr\.?\b|младш\w*|начинающ\w*', re.I)),
    ('Intern', re.compile(r'\bintern\b|\btrainee\b|стаж[её]р\w*|практикант\w*', re.I)),
)
LEAD_DUTIES = re.compile(
    r'руковод\w* команд\w* (?:разработ|программист|инженер|аналитик)|'
    r'управлен\w* команд\w* (?:разработ|программист|инженер|аналитик)|'
    r'лидировал\w* команд\w*|управлен\w* отдел\w* разработ', re.I)
SENIOR_DUTIES = re.compile(
    r'проектирован\w* архитектур|архитектур\w* (?:приложен|систем|сервис)|'
    r'ментор\w*|наставничеств|\bcode review\b|ревью код\w*', re.I)


def marker(text):
    for grade, pattern in TITLE_MARKERS:
        if pattern.search(text or ''):
            return grade
    return None


def parse_date(value):
    if not value or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def dated_years(items):
    spans = []
    for item in items:
        start = parse_date(item['start_date'])
        end = parse_date(item['end_date']) or (TODAY if (item['end_date'] or '').casefold() in
                                                       ('present', 'по настоящее время', 'н.в.') else None)
        if start and end and start <= TODAY and end >= start:
            spans.append((start.toordinal(), min(end, TODAY).toordinal() + 1))
    spans.sort()
    days = 0
    previous_end = 0
    for start, end in spans:
        days += max(0, end - max(start, previous_end))
        previous_end = max(previous_end, end)
    return round(days / 365.25, 2)


def is_it_item(item):
    title = item['title'] or ''
    description = item['description'] or ''
    if NON_IT_TITLE.search(title) and not IT_TITLE.search(title):
        return False
    return bool(IT_TITLE.search(title) or IT_CONTEXT.search(description))


def classify(resume, items):
    declared = resume['experience_years']
    declared = float(declared) if declared is not None and 0 <= float(declared) <= 60 else None
    relevant = [item for item in items if is_it_item(item)]
    work = [item for item in relevant if item['kind'] == 'work']
    projects = [item for item in relevant if item['kind'] == 'project']
    years_dated = dated_years(work)
    # Total declared employment may contain non-IT jobs. Bound it when dated IT history exists.
    effective = max(years_dated, min(declared, years_dated + 2)) if declared and years_dated else (declared or years_dated)
    effective = round(effective, 2)
    reasons = []
    work.sort(key=lambda item: (
        parse_date(item['end_date']) or (TODAY if (item['end_date'] or '').casefold() == 'present' else date.min),
        parse_date(item['start_date']) or date.min,
        -(item['ordinal'] if item['ordinal'] is not None else 999999)), reverse=True)
    explicit_work = marker(work[0]['title']) if work else None
    explicit_position = marker(resume['position_name']) or marker(resume['typical_position'])
    if explicit_position in ('Intern', 'Junior') and effective >= 3:
        explicit_position = None
    has_lead_duties = any(LEAD_DUTIES.search(item['description'] or '') for item in work)
    has_senior_duties = any(SENIOR_DUTIES.search(item['description'] or '') for item in work)
    if explicit_work:
        grade = explicit_work
        reasons.append('explicit_it_work_title')
        confidence = 'high' if years_dated >= .5 else 'medium'
    elif has_lead_duties and effective >= 4:
        grade, confidence = 'Lead', 'medium'
        reasons.append('it_team_lead_responsibilities')
    elif explicit_position and (work or projects):
        grade, confidence = explicit_position, 'medium'
        reasons.append('explicit_resume_position')
    elif work and effective >= 6:
        grade, confidence = 'Senior', 'medium' if years_dated >= 2 else 'low'
        reasons.append('it_work_and_6_plus_years')
    elif work and effective >= 2:
        grade, confidence = 'Middle', 'medium' if years_dated >= 1 else 'low'
        reasons.append('it_work_and_2_plus_years')
    elif work or projects:
        grade, confidence = 'Junior', 'medium' if years_dated >= .25 else 'low'
        reasons.append('limited_it_work_or_projects')
    else:
        grade, confidence = 'Unknown', 'low'
        reasons.append('no_reliable_it_experience')
    if grade == 'Middle' and has_senior_duties and effective >= 4 and not explicit_work:
        grade = 'Senior'
        reasons.append('senior_it_responsibilities')
    if explicit_work and declared is not None:
        if (grade == 'Lead' and declared < 3) or (grade == 'Senior' and declared < 2) or (grade in ('Intern', 'Junior') and declared >= 4):
            confidence = 'low'
            reasons.append('title_years_conflict')
    if not years_dated and declared and work:
        confidence = 'low'
        reasons.append('declared_years_without_dated_it_work')
    if grade == 'Intern':
        grade = 'Junior'
        reasons.append('intern_merged_into_junior')
    elif grade == 'Lead':
        grade = 'Senior'
        reasons.append('lead_merged_into_senior')
    return {
        'user_id': resume['user_id'], 'resume_id': resume['id'],
        'specialization': resume['specialization'] or '',
        'position_name': resume['position_name'] or '',
        'grade': grade, 'confidence': confidence,
        'declared_years': declared, 'dated_it_years': years_dated,
        'effective_it_years': effective, 'it_work_blocks': len(work),
        'it_project_blocks': len(projects), 'evidence': reasons,
    }


def calculate(db, output):
    init_db(db)
    output.mkdir(parents=True, exist_ok=True)
    fields = ('user_id', 'resume_id', 'specialization', 'position_name', 'grade', 'confidence',
              'declared_years', 'dated_it_years', 'effective_it_years',
              'it_work_blocks', 'it_project_blocks', 'evidence')
    counts = Counter()
    confidence_counts = Counter()
    by_spec = defaultdict(Counter)
    source_counts = Counter()
    sample = defaultdict(list)
    rng = random.Random(20261010)
    with connect(db) as c, (output / 'heuristic_candidate_grades.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        source = c.execute('''SELECT r.id,r.user_id,r.specialization,r.position_name,
            r.typical_position,r.experience_years,w.kind,w.ordinal,w.title,w.start_date,w.end_date,w.description
            FROM resumes r LEFT JOIN work_items w ON w.resume_id=r.id
            ORDER BY r.id,w.ordinal,w.id''')
        batch = []
        for _, joined in itertools.groupby(source, key=lambda row: row['id']):
            rows = list(joined)
            resume = rows[0]
            items = [row for row in rows if row['kind'] is not None]
            estimate = classify(resume, items)
            csv_row = {field: (json.dumps(estimate[field], ensure_ascii=False)
                               if field == 'evidence' else estimate[field]) for field in fields}
            writer.writerow(csv_row)
            batch.append((estimate['user_id'], estimate['resume_id'], estimate['grade'],
                          estimate['confidence'], estimate['declared_years'],
                          estimate['dated_it_years'], estimate['effective_it_years'],
                          estimate['it_work_blocks'], estimate['it_project_blocks'],
                          csv_row['evidence'], VERSION, now()))
            counts[estimate['grade']] += 1
            confidence_counts[estimate['confidence']] += 1
            by_spec[estimate['specialization']][estimate['grade']] += 1
            source_counts.update(estimate['evidence'])
            if len(sample[estimate['grade']]) < 10:
                sample[estimate['grade']].append({k: estimate[k] for k in
                                                  ('resume_id', 'position_name', 'declared_years',
                                                   'dated_it_years', 'grade', 'confidence', 'evidence')})
            elif rng.random() < 10 / counts[estimate['grade']]:
                sample[estimate['grade']][rng.randrange(10)] = {k: estimate[k] for k in
                    ('resume_id', 'position_name', 'declared_years', 'dated_it_years',
                     'grade', 'confidence', 'evidence')}
            if len(batch) >= 1000:
                c.executemany('''INSERT INTO candidate_grade_estimates VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(user_id) DO UPDATE SET resume_id=excluded.resume_id,
                    grade=excluded.grade,confidence=excluded.confidence,
                    declared_years=excluded.declared_years,dated_it_years=excluded.dated_it_years,
                    effective_it_years=excluded.effective_it_years,it_work_blocks=excluded.it_work_blocks,
                    it_project_blocks=excluded.it_project_blocks,evidence_json=excluded.evidence_json,
                    heuristic_version=excluded.heuristic_version,updated_at=excluded.updated_at''', batch)
                batch.clear()
        if batch:
            c.executemany('''INSERT INTO candidate_grade_estimates VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET resume_id=excluded.resume_id,
                grade=excluded.grade,confidence=excluded.confidence,
                declared_years=excluded.declared_years,dated_it_years=excluded.dated_it_years,
                effective_it_years=excluded.effective_it_years,it_work_blocks=excluded.it_work_blocks,
                it_project_blocks=excluded.it_project_blocks,evidence_json=excluded.evidence_json,
                heuristic_version=excluded.heuristic_version,updated_at=excluded.updated_at''', batch)
        c.execute('DELETE FROM candidate_grade_estimates WHERE user_id NOT IN (SELECT user_id FROM resumes)')
    total = sum(counts.values())
    report = {'version': VERSION, 'as_of': TODAY.isoformat(), 'total': total, 'grades': dict(counts),
              'confidence': dict(confidence_counts),
              'grade_by_specialization': {spec: dict(values) for spec, values in sorted(by_spec.items())},
              'evidence_counts': dict(source_counts), 'audit_sample': dict(sample),
              'method': 'explicit IT work title > IT leadership duties > explicit resume title > bounded years of IT work; projects only support Junior; uncertain cases Unknown'}
    (output / 'grade_statistics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Эвристическая оценка грейда', '',
             f'Оценено {total:,} кандидатов. Значения сохранены в БД в `candidate_grade_estimates` '
             f'и выгружены в `heuristic_candidate_grades.csv`. Дата расчёта стажа: {TODAY.isoformat()}. '
             'Это гипотезы, не проверенные человеком.', '',
             '| Грейд | Число | Доля |', '|---|---:|---:|']
    for grade in ('Junior', 'Middle', 'Senior', 'Unknown'):
        n = counts[grade]
        lines.append(f'| {grade} | {n:,} | {n / total:.1%} |')
    lines += ['', '## Уверенность', '', '| Уровень | Число | Доля |', '|---|---:|---:|']
    for level in ('high', 'medium', 'low'):
        n = confidence_counts[level]
        lines.append(f'| {level} | {n:,} | {n / total:.1%} |')
    lines += ['', '## Основания для оценки', '', '| Сигнал | Число |', '|---|---:|']
    for signal, n in source_counts.most_common():
        lines.append(f'| {signal} | {n:,} |')
    lines += ['', '## По специализациям', '',
              '| Специализация | Junior | Middle | Senior | Unknown |',
              '|---|---:|---:|---:|---:|']
    for spec, values in sorted(by_spec.items(), key=lambda item: -sum(item[1].values())):
        lines.append('| ' + spec + ' | ' + ' | '.join(str(values[grade]) for grade in
                     ('Junior', 'Middle', 'Senior', 'Unknown')) + ' |')
    lines += ['', 'Правила используют только релевантную IT-работу. Пересекающиеся даты не суммируются; '
              'заявленный общий стаж ограничивается датированной IT-историей плюс два года. '
              'Проекты не превращаются в годы профессионального опыта. Должность в резюме слабее '
              'должности в истории работы. `Unknown` означает недостаток надёжных сведений, '
              'а не отсутствие навыков. Оценка грейда пока не влияет на ранжирование.', '']
    (output / 'grade_statistics.md').write_text('\n'.join(lines), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'fsp.sqlite3')
    parser.add_argument('--out', type=Path, default=OUT)
    args = parser.parse_args()
    result = calculate(args.db, args.out)
    print(json.dumps({key: result[key] for key in ('total', 'grades', 'confidence', 'evidence_counts')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
