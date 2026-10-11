"""Persistent vacancy applications, invitations and participant-only chats."""
import json
import uuid
from fastapi import HTTPException
from .db import now


def owned_vacancy(c,vacancy_id,employer_id,require_open=False):
    vacancy=c.execute('SELECT * FROM vacancies WHERE id=? AND employer_id=?',(vacancy_id,employer_id)).fetchone()
    if not vacancy:raise HTTPException(404,'Вакансия не найдена в вашем аккаунте')
    if require_open and vacancy['status']!='open':raise HTTPException(409,'Вакансия закрыта')
    return vacancy


def employer_overview(c,employer_id):
    company=c.execute('SELECT company_name,about,website FROM employer_profiles WHERE user_id=?',(employer_id,)).fetchone()
    counts=c.execute('''SELECT count(*),sum(status='open'),sum(status='closed'),sum(visibility='public')
      FROM vacancies WHERE employer_id=?''',(employer_id,)).fetchone()
    application_count=c.execute("SELECT count(*) FROM hiring_interactions WHERE employer_id=? AND kind='application'",(employer_id,)).fetchone()[0]
    pending_count=c.execute("SELECT count(*) FROM hiring_interactions WHERE employer_id=? AND kind='application' AND status='pending'",(employer_id,)).fetchone()[0]
    invites=c.execute("SELECT count(*) FROM hiring_interactions WHERE employer_id=? AND kind='invitation'",(employer_id,)).fetchone()[0]
    chats=c.execute("SELECT count(*) FROM hiring_interactions WHERE employer_id=? AND status='approved'",(employer_id,)).fetchone()[0]
    return {'company':dict(company) if company else None,'vacancies':counts[0],
            'open_vacancies':counts[1] or 0,'archived_vacancies':counts[2] or 0,
            'public_vacancies':counts[3] or 0,'applications':application_count,
            'pending_applications':pending_count,'invitations':invites,'chats':chats}


def my_vacancies(c,employer_id):
    rows=c.execute('''SELECT v.id,v.vacancy_name,v.specialization,v.region,v.description,v.responsibilities,
      v.visibility,v.status,v.created_at,v.closed_at,
      (SELECT count(*) FROM hiring_interactions h WHERE h.vacancy_id=v.id AND h.kind='application') application_count,
      (SELECT count(*) FROM hiring_interactions h WHERE h.vacancy_id=v.id AND h.kind='application' AND h.status='pending') pending_count
      FROM vacancies v WHERE v.employer_id=? ORDER BY v.created_at DESC,v.id DESC''',(employer_id,)).fetchall()
    return [dict(row) for row in rows]


def public_vacancies(c,specialization='',page=1,limit=20):
    where="v.visibility='public' AND v.status='open'"
    params=[]
    if specialization:
        where+=' AND v.specialization=?';params.append(specialization)
    total=c.execute('SELECT count(*) FROM vacancies v WHERE '+where,params).fetchone()[0]
    rows=c.execute('''SELECT v.id,v.vacancy_name,v.specialization,v.region,v.description,v.responsibilities,
      v.position_requirements,v.skills_text,v.all_technologies_json,v.employer_id,
      ep.company_name, v.visibility,v.status,v.created_at
      FROM vacancies v LEFT JOIN employer_profiles ep ON ep.user_id=v.employer_id
      WHERE '''+where+' ORDER BY (v.employer_id IS NULL),v.created_at DESC,v.id DESC LIMIT ? OFFSET ?',
      params+[limit,(page-1)*limit]).fetchall()
    return {'items':[dict(row) for row in rows],'total':total,'page':page,'ranking_status':'placeholder'}


def interaction_public(c,row):
    result=dict(row)
    result['messages']=[dict(message) for message in c.execute('''SELECT m.id,m.sender_id,u.name sender_name,m.body,m.created_at
       FROM hiring_messages m JOIN users u ON u.id=m.sender_id WHERE m.interaction_id=?
       ORDER BY m.created_at,m.id''',(row['id'],))]
    result['chat_enabled']=row['status']=='approved' and row['employer_id'] is not None
    return result


def get_interaction(c,interaction_id,user):
    row=c.execute('''SELECT h.*,v.vacancy_name,v.specialization,v.description,v.visibility,v.status vacancy_status,
      u.name candidate_name,ep.company_name,r.id resume_id,r.position_name,r.all_technologies_json
      FROM hiring_interactions h JOIN vacancies v ON v.id=h.vacancy_id
      JOIN users u ON u.id=h.candidate_id LEFT JOIN employer_profiles ep ON ep.user_id=h.employer_id
      LEFT JOIN resumes r ON r.user_id=h.candidate_id WHERE h.id=?''',(interaction_id,)).fetchone()
    if not row:raise HTTPException(404,'Диалог не найден')
    if user['id'] not in (row['candidate_id'],row['employer_id']):raise HTTPException(403,'Нет доступа к диалогу')
    return row


def list_interactions(c,user,vacancy_id=None):
    field='candidate_id' if user['role']=='candidate' else 'employer_id'
    where=f'h.{field}=?';params=[user['id']]
    if vacancy_id:
        where+=' AND h.vacancy_id=?';params.append(vacancy_id)
    rows=c.execute('''SELECT h.id,h.vacancy_id,h.candidate_id,h.employer_id,h.kind,h.status,h.created_at,h.updated_at,
      v.vacancy_name,v.specialization,v.status vacancy_status,u.name candidate_name,ep.company_name,
      (SELECT body FROM hiring_messages m WHERE m.interaction_id=h.id ORDER BY m.created_at DESC,m.id DESC LIMIT 1) last_message
      FROM hiring_interactions h JOIN vacancies v ON v.id=h.vacancy_id
      JOIN users u ON u.id=h.candidate_id LEFT JOIN employer_profiles ep ON ep.user_id=h.employer_id
      WHERE '''+where+' ORDER BY h.updated_at DESC,h.id DESC',params).fetchall()
    return [dict(row) for row in rows]


def create_interaction(c,vacancy_id,candidate_id,kind,employer_id=None):
    vacancy=c.execute('SELECT id,employer_id,visibility,status FROM vacancies WHERE id=?',(vacancy_id,)).fetchone()
    if not vacancy:raise HTTPException(404,'Вакансия не найдена')
    if vacancy['status']!='open':raise HTTPException(409,'Вакансия закрыта')
    if kind=='application':
        if vacancy['visibility']!='public':raise HTTPException(403,'Вакансия закрыта для откликов')
        employer_id=vacancy['employer_id']
    else:
        if vacancy['employer_id']!=employer_id:raise HTTPException(403,'Приглашать можно только на свою вакансию')
        target=c.execute("SELECT 1 FROM users WHERE id=? AND role='candidate' AND origin='registration'",(candidate_id,)).fetchone()
        if not target:raise HTTPException(422,'Приглашение можно отправить только зарегистрированному кандидату')
    existing=c.execute('SELECT id FROM hiring_interactions WHERE vacancy_id=? AND candidate_id=? AND kind=?',
                       (vacancy_id,candidate_id,kind)).fetchone()
    if existing:raise HTTPException(409,'Отклик или приглашение уже существует')
    interaction_id=uuid.uuid4().hex;stamp=now()
    c.execute('INSERT INTO hiring_interactions VALUES(?,?,?,?,?,?,?,?)',
              (interaction_id,vacancy_id,candidate_id,employer_id,kind,'pending',stamp,stamp))
    return interaction_id


def change_interaction(c,interaction_id,user,status):
    if status not in ('approved','rejected'):raise HTTPException(422,'Неизвестный статус')
    row=get_interaction(c,interaction_id,user)
    owner=(row['kind']=='application' and user['id']==row['employer_id']) or (row['kind']=='invitation' and user['id']==row['candidate_id'])
    if not owner:raise HTTPException(403,'Вы не можете изменить статус')
    if row['status']!='pending':raise HTTPException(409,'Решение уже принято')
    c.execute('UPDATE hiring_interactions SET status=?,updated_at=? WHERE id=?',(status,now(),interaction_id))
    return status


def add_message(c,interaction_id,user,body):
    row=get_interaction(c,interaction_id,user)
    clean=body.strip()
    if not clean or len(clean)>5000:raise HTTPException(422,'Введите сообщение до 5000 символов')
    if row['status']!='approved' or row['employer_id'] is None:
        raise HTTPException(409,'Чат открывается после одобрения')
    message_id=uuid.uuid4().hex;stamp=now()
    c.execute('INSERT INTO hiring_messages VALUES(?,?,?,?,?)',(message_id,interaction_id,user['id'],clean,stamp))
    c.execute('UPDATE hiring_interactions SET updated_at=? WHERE id=?',(stamp,interaction_id))
    return {'id':message_id,'body':clean,'sender_id':user['id'],'created_at':stamp}
