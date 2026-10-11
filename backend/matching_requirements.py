"""Demo salary expectations and explainable vacancy-grade heuristics.

Existing source salary fields remain untouched. The values below are demo
expectations in RUB/month, not a market estimate or a verified user claim.
"""

import json
import re


GRADES = ('Junior', 'Middle', 'Senior')
SALARY_BY_SPECIALIZATION = {
    'Backend': (70000, 120000, 200000),
    'Frontend': (65000, 115000, 190000),
    'Fullstack': (75000, 130000, 220000),
    'ML / AI': (90000, 160000, 270000),
    'Data Science': (80000, 145000, 240000),
    'Data Engineering': (85000, 150000, 250000),
    'Data Analytics': (65000, 115000, 190000),
    'System Analysis': (65000, 115000, 190000),
    'DevOps / SRE': (85000, 150000, 250000),
    'QA': (55000, 100000, 170000),
    'Mobile': (75000, 130000, 220000),
    'Cybersecurity': (75000, 135000, 225000),
    'System / Network': (55000, 100000, 175000),
    'Database': (70000, 125000, 210000),
    'Software Engineering': (75000, 130000, 215000),
    'Other IT': (50000, 90000, 150000),
}

JUNIOR_TITLE = re.compile(r'(?i)\b(?:intern|internship|trainee|junior|jr\.?|стаж[её]р\w*|младш\w*|начинающ\w*)\b')
MIDDLE_TITLE = re.compile(r'(?i)\b(?:middle|mid-level|мидл\w*)\b')
SENIOR_TITLE = re.compile(r'(?i)\b(?:senior|sr\.?|lead|principal|staff engineer|team\s*lead|tech\s*lead|архитектор\w*|ведущ\w*|старш\w*|главн\w*|руководител\w*|начальник\w*)\b')
YEARS = re.compile(r'(?i)(?:опыт(?:а|ом)?(?:\s+работы)?|стаж|experience)\s*(?:от|не\s+менее|более|свыше|of|at\s+least|minimum|:|\s)*\s*(\d{1,2})\s*(?:\+\s*)?(?:лет|года|год|years?|yrs?)')
SENIOR_REQUIREMENT = re.compile(r'(?i)(?:наставничеств\w*|менторств\w*|проектировани[ея]\s+архитектур\w*|архитектур\w*\s+(?:систем|сервис)|руководств\w*\s+команд\w*|team\s*lead|tech\s*lead)')
JUNIOR_REQUIREMENT = re.compile(r'(?i)(?:без\s+опыта|опыт\s+не\s+требуется|готовы\s+обучать|стажировк\w*)')


def demo_salary_min(specialization, grade):
    values = SALARY_BY_SPECIALIZATION.get(specialization, SALARY_BY_SPECIALIZATION['Other IT'])
    return values[GRADES.index(grade) if grade in GRADES else 0]


def usable_salary_range(minimum, maximum):
    """Legacy dataset has zero and inverted salary ranges; do not flag those."""
    return (minimum is not None and maximum is not None and
            minimum > 0 and maximum >= minimum)


def infer_vacancy_grade(vacancy):
    """Return (grade, evidence) from title, years, requirements and stack size."""
    title = vacancy['vacancy_name'] or ''
    marked = [
        grade for grade, pattern in [('Junior', JUNIOR_TITLE), ('Middle', MIDDLE_TITLE), ('Senior', SENIOR_TITLE)]
        if pattern.search(title)
    ]
    if marked:
        return max(marked, key=GRADES.index), 'title_heuristic_v1'

    years = vacancy['experience_requirements']
    if years is None:
        text = ' '.join(str(vacancy[key] or '') for key in ('position_requirements', 'additional_requirements', 'description'))[:6000]
        found = YEARS.search(text)
        years = int(found.group(1)) if found else None
    if years is not None:
        if years >= 5:
            return 'Senior', 'experience_heuristic_v1'
        if years >= 2:
            return 'Middle', 'experience_heuristic_v1'
        return 'Junior', 'experience_heuristic_v1'

    requirements = ' '.join(str(vacancy[key] or '') for key in ('position_requirements', 'additional_requirements', 'responsibilities'))[:6000]
    if SENIOR_REQUIREMENT.search(requirements):
        return 'Senior', 'requirements_heuristic_v1'
    if JUNIOR_REQUIREMENT.search(requirements):
        return 'Junior', 'requirements_heuristic_v1'
    if (vacancy['technology_count'] or 0) >= 8:
        return 'Middle', 'stack_heuristic_v1'
    return 'Middle', 'default_heuristic_v1'


def backfill_existing(c):
    """Run once after the new columns have been added, in the caller's transaction."""
    salary_rows = []
    profiles = []
    rows = c.execute('''SELECT r.id,r.user_id,r.specialization,u.origin,
      cg.grade dataset_grade,sg.current_grade specialization_grade,ig.grade initial_grade,
      cp.profile_json,cp.revision
      FROM resumes r JOIN users u ON u.id=r.user_id
      LEFT JOIN candidate_grades cg ON cg.user_id=r.user_id
      LEFT JOIN candidate_specialization_grades sg
        ON sg.user_id=r.user_id AND sg.specialization=r.specialization
      LEFT JOIN candidate_initial_grades ig ON ig.user_id=r.user_id
      LEFT JOIN candidate_profiles cp ON cp.user_id=r.user_id''')
    for row in rows:
        grade = row['specialization_grade'] or row['dataset_grade'] or row['initial_grade'] or 'Junior'
        salary = demo_salary_min(row['specialization'], grade)
        salary_rows.append((salary, row['id']))
        if row['origin'] == 'registration' and row['profile_json']:
            profile = json.loads(row['profile_json'])
            if not profile.get('salaryMin'):
                profile['salaryMin'] = salary
                encoded = json.dumps(profile, ensure_ascii=False, separators=(',', ':'))
                profiles.append((encoded, row['user_id'], row['id']))
    c.executemany('UPDATE resumes SET expected_salary_min=? WHERE id=?', salary_rows)
    for encoded, user_id, resume_id in profiles:
        c.execute('UPDATE candidate_profiles SET profile_json=?,revision=revision+1 WHERE user_id=?',
                  (encoded, user_id))
        c.execute('UPDATE resumes SET raw_json=? WHERE id=?', (encoded, resume_id))

    grade_rows = []
    for row in c.execute('''SELECT id,vacancy_name,experience_requirements,position_requirements,
      additional_requirements,responsibilities,description,technology_count FROM vacancies'''):
        grade, source = infer_vacancy_grade(row)
        grade_rows.append((grade, source, row['id']))
    c.executemany('UPDATE vacancies SET grade=?,grade_source=? WHERE id=?', grade_rows)
    return {'resumes': len(salary_rows), 'profiles': len(profiles), 'vacancies': len(grade_rows)}
