"""Privacy boundary for employer access to ranked candidates."""

import hashlib
import os
import secrets
import uuid

from fastapi import HTTPException, Request

from .db import now

MAX_RESULTS = 20
DAILY_NEW_MATCHES = int(os.environ.get('FSP_DAILY_CANDIDATE_LIMIT', '50'))


def _fingerprint(value):
    pepper = os.environ.get('FSP_AUDIT_PEPPER', 'fsp-local-audit').encode()
    return hashlib.sha256(pepper + (value or '').encode()).hexdigest()


def audit(c, employer_id, action, vacancy_id=None, match_id=None, count=0, request=None):
    ip = request.client.host if request and request.client else ''
    agent = request.headers.get('user-agent', '') if request else ''
    c.execute('''INSERT INTO candidate_access_audit
      (id,employer_id,vacancy_id,match_id,action,result_count,ip_hash,user_agent_hash,created_at)
      VALUES(?,?,?,?,?,?,?,?,?)''',
      (uuid.uuid4().hex, employer_id, vacancy_id, match_id, action, count,
       _fingerprint(ip), _fingerprint(agent), now()))


def require_approved_employer(c, employer_id):
    row = c.execute('SELECT access_status FROM employer_profiles WHERE user_id=?',
                    (employer_id,)).fetchone()
    if not row or row['access_status'] != 'approved':
        raise HTTPException(403, 'Доступ к базе кандидатов откроется после проверки компании')


def owned_search_vacancy(c, vacancy_id, employer_id):
    row = c.execute('SELECT employer_id,status FROM vacancies WHERE id=?', (vacancy_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Вакансия не найдена')
    if row['employer_id'] != employer_id:
        raise HTTPException(403, 'Подбор доступен только для собственной вакансии')
    if row['status'] != 'open':
        raise HTTPException(409, 'Вакансия закрыта и находится в архиве')
    return row


def anonymize_matches(c, employer_id, vacancy_id, ranked, request=None):
    """Return a bounded, pseudonymous view and persist its access trail."""
    require_approved_employer(c, employer_id)
    ranked = ranked[:MAX_RESULTS]
    today = now()[:10]
    used = c.execute('''SELECT count(*) FROM candidate_match_access
      WHERE employer_id=? AND substr(created_at,1,10)=?''', (employer_id, today)).fetchone()[0]
    result = []
    for item in ranked:
        existing = c.execute('''SELECT match_id FROM candidate_match_access
          WHERE employer_id=? AND vacancy_id=? AND resume_id=?''',
          (employer_id, vacancy_id, item['resume_id'])).fetchone()
        if existing:
            match_id = existing['match_id']
            c.execute('UPDATE candidate_match_access SET last_seen_at=? WHERE match_id=?',
                      (now(), match_id))
        else:
            if used >= DAILY_NEW_MATCHES:
                break
            match_id = secrets.token_urlsafe(24)
            c.execute('''INSERT INTO candidate_match_access
              (match_id,employer_id,vacancy_id,candidate_id,resume_id,created_at,last_seen_at)
              VALUES(?,?,?,?,?,?,?)''',
              (match_id, employer_id, vacancy_id, item['user_id'], item['resume_id'], now(), now()))
            used += 1
        result.append({
            'match_id': match_id,
            'position_name': item['position_name'],
            'specialization': item['specialization'],
            'candidate_stack': item['candidate_stack'],
            'candidate_grade': item['candidate_grade'],
            'verified_grade': item['verified_grade'],
            'contactable': item['contactable'],
            'salary_mismatch': item['salary_mismatch'],
            'score': item['score'],
            'components': item['components'],
            'experience_blocks_found': item['experience_blocks_found'],
            'requested_technologies': item['requested_technologies'],
            'vacancy_technologies': item['vacancy_technologies'],
        })
    audit(c, employer_id, 'search', vacancy_id=vacancy_id, count=len(result), request=request)
    return result


def resolve_match(c, employer_id, vacancy_id, match_id):
    row = c.execute('''SELECT candidate_id,resume_id FROM candidate_match_access
      WHERE match_id=? AND employer_id=? AND vacancy_id=?''',
      (match_id, employer_id, vacancy_id)).fetchone()
    if not row:
        raise HTTPException(404, 'Совпадение не найдено')
    return row


def authorized_resume(c, employer_id, resume_id, interaction_id):
    row = c.execute('''SELECT h.id,h.kind,h.status,h.vacancy_id FROM hiring_interactions h
      JOIN resumes r ON r.user_id=h.candidate_id
      WHERE h.id=? AND h.employer_id=? AND r.id=?''',
      (interaction_id, employer_id, resume_id)).fetchone()
    if row and (row['kind'] != 'invitation' or row['status'] == 'approved'):
        return row
    row = c.execute('''SELECT i.id,'invitation' kind,i.status,i.vacancy_id
      FROM hiring_invitations i JOIN resumes r ON r.user_id=i.candidate_id
      WHERE i.id=? AND i.employer_id=? AND r.id=?''',
      (interaction_id, employer_id, resume_id)).fetchone()
    if not row or row['status'] != 'approved':
        raise HTTPException(403, 'Кандидат ещё не разрешил открыть резюме')
    return row


