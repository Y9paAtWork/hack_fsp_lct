import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response, Depends, Query
from pydantic import BaseModel, Field
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError, VerificationError
from .db import connect, init_db, now
from .profile import empty_profile, sanitize, sync_resume
from .import_data import dump, seed_catalog
from .ranking import WEIGHTS, SCORING_VERSION, catalog, rank_vacancy, rebuild_skill_vector, record_visit
from .assessments import seed_daily, seed_technology_questions, catalog_tests, start_technology, submit_technology, grade_progress, update_grades, daily_public, submit_daily, activity_calendar, user_test_grade, current_grade_progress, advance_test_grade, selected_specializations, technology_test_grades, matrix
from .marketplace import employer_overview, my_vacancies, public_vacancies, owned_vacancy, list_interactions, get_interaction, create_interaction, change_interaction, add_message
from .candidate_vacancy_search import ensure_experience_vectors, rank_for_candidate

hasher=PasswordHasher(time_cost=2,memory_cost=19456,parallelism=1)
COOKIE='fsp_session'
SESSION_TTL=7*24*3600
attempts=defaultdict(deque)

@asynccontextmanager
async def lifespan(app):
    init_db()
    with connect() as c:
        seed_catalog(c)
        seed_technology_questions(c)
        seed_daily(c)
    yield
app=FastAPI(title='FSP Career API',lifespan=lifespan)

@app.middleware('http')
async def protect(request,call_next):
    if request.method in ('POST','PUT','PATCH','DELETE'):
        allowed=set(os.environ.get('FSP_ALLOWED_ORIGINS','http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000').split(','))
        origin=request.headers.get('origin')
        if origin and origin not in allowed:return Response('Origin not allowed',status_code=403)
        try:
            if int(request.headers.get('content-length','0'))>2_000_000:return Response('Request too large',status_code=413)
        except ValueError:return Response('Invalid Content-Length',status_code=400)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response

class Register(BaseModel):
    email:str=Field(min_length=3,max_length=254)
    password:str=Field(min_length=8,max_length=128)
    name:str=Field(min_length=1,max_length=200)
    role:str
    company_name:str=Field(default='',max_length=200)
    profile:dict|None=None
    specialization_grades:dict[str,str]|None=None
class Login(BaseModel):
    email:str=Field(max_length=254)
    password:str=Field(max_length=128)
class ProfilePayload(BaseModel):
    profile:dict
    revision:int
class EmployerPayload(BaseModel):
    company_name:str=Field(min_length=1,max_length=200)
    about:str=Field(default='',max_length=20000)
    website:str=Field(default='',max_length=2000)
class VacancyPayload(BaseModel):
    vacancy_name:str=Field(min_length=3,max_length=200)
    specialization:str=Field(min_length=1,max_length=100)
    grade:str
    salary_min:int=Field(ge=1000,le=10000000)
    salary_max:int=Field(ge=1000,le=10000000)
    required_technologies:list[str]=Field(default_factory=list,max_length=202)
    region:str=Field(default='',max_length=200)
    description:str=Field(min_length=20,max_length=20000)
    responsibilities:str=Field(min_length=20,max_length=20000)
    position_requirements:str=Field(default='',max_length=20000)
    skills_text:str=Field(default='',max_length=10000)
    visibility:str=Field(default='public')
class VacancyState(BaseModel):
    visibility:str
    status:str
class InvitationPayload(BaseModel):
    candidate_id:str
class DecisionPayload(BaseModel):
    status:str
class MessagePayload(BaseModel):
    body:str=Field(min_length=1,max_length=5000)
class TechnologyStart(BaseModel):
    technology:str
    grade:str
class GradeDeclarations(BaseModel):
    grades:dict[str,str]
class AnswersPayload(BaseModel):
    answers:dict
class CandidateVacancySearch(BaseModel):
    specialization:str=''
    page:int=Field(default=1,ge=1)
    limit:int=Field(default=20,ge=1,le=100)

def user_public(row):return {k:row[k] for k in ['id','email','name','role','origin']}
def issue_session(c,user_id,response):
    token=secrets.token_urlsafe(32)
    c.execute('DELETE FROM sessions WHERE expires_at<?',(int(time.time()),))
    c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user_id,int(time.time())+SESSION_TTL,now()))
    response.set_cookie(COOKIE,token,httponly=True,secure=os.environ.get('FSP_COOKIE_SECURE')=='1',samesite='strict',max_age=SESSION_TTL,path='/')
def current_user(request:Request):
    token=request.cookies.get(COOKIE,'')
    if not token:raise HTTPException(401,'Войдите в аккаунт')
    with connect() as c:
        user=c.execute('''SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id
        WHERE s.token_hash=? AND s.expires_at>? AND u.origin='registration' ''',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
        if user and user['role']=='candidate':record_visit(c,user['id'])
    if not user:raise HTTPException(401,'Сессия истекла. Войдите снова')
    return dict(user)
def candidate(user=Depends(current_user)):
    if user['role']!='candidate':raise HTTPException(403,'Нужен аккаунт кандидата')
    return user
def employer(user=Depends(current_user)):
    if user['role']!='employer':raise HTTPException(403,'Нужен аккаунт работодателя')
    return user

@app.get('/api/health')
def health():return {'ok':True}
@app.post('/api/auth/register')
def register(payload:Register,response:Response):
    email=payload.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise HTTPException(422,'Введите корректную почту')
    if payload.role not in ('candidate','employer'):raise HTTPException(422,'Неизвестная роль')
    if not payload.name.strip():raise HTTPException(422,'Укажите имя')
    if payload.role=='employer' and not payload.company_name.strip():raise HTTPException(422,'Укажите компанию')
    profile=None;grades=None
    if payload.role=='candidate':
        if payload.profile is None or payload.specialization_grades is None:
            raise HTTPException(422,'Завершите все шаги анкеты перед созданием аккаунта')
        profile=sanitize(payload.profile,payload.name.strip(),email)
        profile['name']=payload.name.strip()
        grades=payload.specialization_grades
        if (profile['step']!=4 or not profile['terms'] or not profile['selected'] or
            profile['active'] not in profile['selected'] or set(grades)!=set(profile['selected']) or
            any(spec not in matrix() for spec in profile['selected']) or
            any(grade not in ('Junior','Middle','Senior') for grade in grades.values())):
            raise HTTPException(422,'Заполните специализации и грейд каждого направления')
        if profile['salaryMin'] is None:
            raise HTTPException(422,'Укажите минимальную желаемую зарплату в рублях за месяц')
    uid=uuid.uuid4().hex;timestamp=now()
    try:
        with connect() as c:
            c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',(uid,email,hasher.hash(payload.password),payload.role,payload.name.strip(),'registration',timestamp))
            if payload.role=='candidate':
                c.execute('INSERT INTO candidate_profiles(user_id,fsp_id,profile_json,updated_at) VALUES(?,?,?,?)',
                          (uid,profile['fsp'],dump(profile),timestamp))
                sync_resume(c,{'id':uid},profile)
                initial=grades[profile['active']]
                c.execute('INSERT INTO candidate_initial_grades VALUES(?,?,?)',(uid,initial,timestamp))
                c.execute('INSERT INTO candidate_test_grades VALUES(?,?,?)',(uid,initial,timestamp))
                c.executemany('INSERT INTO candidate_specialization_grades VALUES(?,?,?,?,?,?)',
                              [(uid,spec,grade,grade,timestamp,timestamp) for spec,grade in grades.items()])
                names,catalog_hash=catalog();rebuild_skill_vector(c,uid,names,catalog_hash)
                record_visit(c,uid)
            else:c.execute('INSERT INTO employer_profiles VALUES(?,?,?,?,?)',(uid,payload.company_name.strip(),'','',timestamp))
            issue_session(c,uid,response)
            user=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
    except sqlite3.IntegrityError:raise HTTPException(409,'Эта почта уже зарегистрирована')
    return {'user':user_public(user)}
@app.post('/api/auth/login')
def login(payload:Login,response:Response,request:Request):
    ip=request.client.host if request.client else 'unknown';t=time.time()
    if len(attempts)>10000:attempts.clear()
    q=attempts[ip]
    while q and q[0]<t-300:q.popleft()
    if len(q)>=15:raise HTTPException(429,'Слишком много попыток. Повторите позже')
    q.append(t)
    with connect() as c:
        user=c.execute("SELECT * FROM users WHERE email=? AND origin='registration'",(payload.email.strip().lower(),)).fetchone()
        try:valid=bool(user) and hasher.verify(user['password_hash'],payload.password)
        except (VerifyMismatchError,InvalidHashError,VerificationError):valid=False
        if not valid:raise HTTPException(401,'Неверная почта или пароль')
        issue_session(c,user['id'],response)
        if user['role']=='candidate':record_visit(c,user['id'])
    return {'user':user_public(user)}
@app.get('/api/auth/me')
def me(user=Depends(current_user)):return {'user':user_public(user)}
@app.post('/api/auth/logout')
def logout(request:Request,response:Response):
    token=request.cookies.get(COOKIE,'')
    if token:
        with connect() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE,path='/')
    return {'ok':True}
@app.get('/api/profile')
def get_profile(user=Depends(candidate)):
    with connect() as c:row=c.execute('SELECT profile_json,revision FROM candidate_profiles WHERE user_id=?',(user['id'],)).fetchone()
    return {'profile':sanitize(json.loads(row[0]),user['name'],user['email']),'revision':row[1]}
@app.put('/api/profile')
def put_profile(payload:ProfilePayload,user=Depends(candidate)):
    if len(dump(payload.profile))>1_000_000:raise HTTPException(413,'Профиль слишком большой')
    profile=sanitize(payload.profile,user['name'],user['email'])
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT revision FROM candidate_profiles WHERE user_id=?',(user['id'],)).fetchone()
        if row[0]!=payload.revision:raise HTTPException(409,'Профиль изменён в другой вкладке. Обновите страницу')
        c.execute('UPDATE candidate_profiles SET profile_json=?,fsp_id=?,revision=revision+1,updated_at=? WHERE user_id=?',(dump(profile),profile['fsp'],now(),user['id']))
        c.execute('UPDATE users SET name=? WHERE id=?',(profile['name'],user['id']))
        sync_resume(c,user,profile)
        initial=c.execute('SELECT grade FROM candidate_initial_grades WHERE user_id=?',(user['id'],)).fetchone()
        default_grade=initial[0] if initial else 'Junior'
        stamp=now()
        c.executemany('''INSERT OR IGNORE INTO candidate_specialization_grades
          (user_id,specialization,declared_grade,current_grade,declared_at,updated_at)
          VALUES(?,?,?,?,?,?)''',[(user['id'],spec,default_grade,default_grade,stamp,stamp)
                            for spec in profile['selected'] if spec in matrix()])
        names,catalog_hash=catalog();rebuild_skill_vector(c,user['id'],names,catalog_hash)
        update_grades(c,user['id'])
    return {'profile':profile,'revision':payload.revision+1}
@app.post('/api/profile/reset')
def reset_profile(payload:dict,user=Depends(candidate)):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT revision FROM candidate_profiles WHERE user_id=?',(user['id'],)).fetchone()
        if payload.get('revision')!=row[0]:raise HTTPException(409,'Профиль изменён. Обновите страницу')
        profile=empty_profile(user['name'],user['email'])
        c.execute('UPDATE candidate_profiles SET profile_json=?,fsp_id=?,revision=revision+1,updated_at=? WHERE user_id=?',(dump(profile),'',now(),user['id']))
        sync_resume(c,user,profile)
        names,catalog_hash=catalog();rebuild_skill_vector(c,user['id'],names,catalog_hash)
    return {'profile':profile,'revision':row[0]+1}
@app.get('/api/profile/items')
def my_items(user=Depends(candidate)):
    with connect() as c:
        rows=c.execute('''SELECT w.*, (SELECT COUNT(*) FROM embedding_chunks ch WHERE ch.item_id=w.id) chunks,
        (SELECT COUNT(*) FROM embedding_chunks ch WHERE ch.item_id=w.id AND ch.status='ready') ready_chunks,
        EXISTS(SELECT 1 FROM item_vectors iv WHERE iv.item_id=w.id) vector_ready
        FROM work_items w WHERE user_id=? ORDER BY ordinal''',(user['id'],)).fetchall()
    return {'items':[dict(r) for r in rows]}
@app.get('/api/employer/profile')
def employer_profile(user=Depends(employer)):
    with connect() as c:r=c.execute('SELECT company_name,about,website FROM employer_profiles WHERE user_id=?',(user['id'],)).fetchone()
    return dict(r)
@app.put('/api/employer/profile')
def save_employer_profile(payload:EmployerPayload,user=Depends(employer)):
    with connect() as c:c.execute('UPDATE employer_profiles SET company_name=?,about=?,website=?,updated_at=? WHERE user_id=?',(payload.company_name,payload.about,payload.website,now(),user['id']))
    return payload.model_dump()
@app.get('/api/database/stats')
def database_stats(user=Depends(employer)):
    with connect() as c:
        tables=['users','resumes','vacancies','work_items','embedding_chunks','embeddings','item_vectors']
        counts={t:c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}
        counts['embedding_status']={r[0]:r[1] for r in c.execute('SELECT status,COUNT(*) FROM embedding_chunks GROUP BY status')}
    return counts
@app.get('/api/vacancies')
def list_vacancies(q:str=Query(default='',max_length=200),page:int=Query(default=1,ge=1),limit:int=Query(default=20,ge=1,le=100),mine:bool=False,ready_only:bool=False,user=Depends(employer)):
    where='vacancy_name LIKE ? AND (employer_id IS NULL OR employer_id=?)';params=['%'+q+'%',user['id']]
    if mine:where+=' AND employer_id=?';params.append(user['id'])
    if ready_only:where+=' AND EXISTS(SELECT 1 FROM vacancy_query_vectors vq WHERE vq.vacancy_id=vacancies.id)'
    with connect() as c:
        total=c.execute('SELECT COUNT(*) FROM vacancies WHERE '+where,params).fetchone()[0]
        rows=c.execute('SELECT id,vacancy_name,specialization,grade,grade_source,region,salary_min,salary_max,required_technologies_json,experience_requirements,description,responsibilities,all_technologies_json,employer_id,visibility,status FROM vacancies WHERE '+where+' ORDER BY (employer_id IS NOT NULL) DESC,id LIMIT ? OFFSET ?',params+[limit,(page-1)*limit]).fetchall()
    return {'items':[dict(r) for r in rows],'total':total,'page':page}

@app.post('/api/vacancies',status_code=201)
def create_vacancy(payload:VacancyPayload,user=Depends(employer)):
    from .assessments import matrix
    from .technology_heuristics import extract_technologies
    if payload.specialization not in matrix():
        raise HTTPException(422,'Выберите специализацию из списка')
    if payload.visibility not in ('public','private'):
        raise HTTPException(422,'Неизвестная видимость вакансии')
    if payload.grade not in ('Junior','Middle','Senior'):
        raise HTTPException(422,'Выберите грейд вакансии')
    if payload.salary_max < payload.salary_min:
        raise HTTPException(422,'Верхняя граница зарплаты должна быть не ниже нижней')
    allowed=set(catalog()[0])
    if len(payload.required_technologies)!=len(set(payload.required_technologies)) or any(name not in allowed for name in payload.required_technologies):
        raise HTTPException(422,'Обязательные навыки должны быть из справочника технологий')
    values=payload.model_dump()
    values={key:value.strip() if isinstance(value,str) else value for key,value in values.items()}
    if not values['vacancy_name'] or not values['description'] or not values['responsibilities']:
        raise HTTPException(422,'Заполните название, описание и задачи')
    tech=list(dict.fromkeys([*extract_technologies(' '.join((values['description'],values['responsibilities'],values['position_requirements'],values['skills_text']))),*values['required_technologies']]))
    vacancy_id=uuid.uuid4().hex
    with connect() as c:
        c.execute('''INSERT INTO vacancies(id,employer_id,specialization,specializations_json,vacancy_name,
          region,description,responsibilities,position_requirements,skills_text,all_technologies_json,
          technology_count,raw_json,created_at,visibility,grade,grade_source,salary_min,salary_max,required_technologies_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (vacancy_id,user['id'],values['specialization'],dump([values['specialization']]),
           values['vacancy_name'],values['region'],values['description'],values['responsibilities'],
           values['position_requirements'],values['skills_text'],dump(tech),len(tech),dump(values),now(),values['visibility'],
           values['grade'],'employer',values['salary_min'],values['salary_max'],dump(values['required_technologies'])))
        c.executemany('INSERT INTO vacancy_technologies(vacancy_id,technology) VALUES(?,?)',
                      [(vacancy_id,name) for name in tech])
    return {'id':vacancy_id,'technologies':tech,'required_technologies':values['required_technologies'],'indexed':False}

@app.post('/api/vacancies/{vacancy_id}/index')
def index_my_vacancy(vacancy_id:str,user=Depends(employer)):
    with connect() as c:
        row=c.execute('SELECT employer_id FROM vacancies WHERE id=?',(vacancy_id,)).fetchone()
        if not row:raise HTTPException(404,'Вакансия не найдена')
        if row['employer_id']!=user['id']:raise HTTPException(403,'Можно индексировать только свою вакансию')
    try:
        from .ranking_cli import index_vacancies
        index_vacancies(None,[vacancy_id])
    except Exception as exc:
        raise HTTPException(503,'Вакансия сохранена, но модель Jina пока не смогла подготовить поиск. Повторите индексацию позже.') from exc
    return {'id':vacancy_id,'indexed':True}

@app.get('/api/employer/overview')
def company_overview(user=Depends(employer)):
    with connect() as c:return employer_overview(c,user['id'])

@app.get('/api/employer/vacancies')
def company_vacancies(user=Depends(employer)):
    with connect() as c:return {'items':my_vacancies(c,user['id'])}

@app.patch('/api/employer/vacancies/{vacancy_id}')
def change_vacancy(vacancy_id:str,payload:VacancyState,user=Depends(employer)):
    if payload.visibility not in ('public','private') or payload.status not in ('open','closed'):
        raise HTTPException(422,'Неизвестный статус вакансии')
    with connect() as c:
        owned_vacancy(c,vacancy_id,user['id'])
        c.execute('''UPDATE vacancies SET visibility=?,status=?,closed_at=? WHERE id=?''',
                  (payload.visibility,payload.status,now() if payload.status=='closed' else None,vacancy_id))
    return {'id':vacancy_id,'visibility':payload.visibility,'status':payload.status}

@app.get('/api/public/vacancies')
def discover_vacancies(specialization:str='',page:int=Query(default=1,ge=1),
                      limit:int=Query(default=20,ge=1,le=100),user=Depends(candidate)):
    with connect() as c:return public_vacancies(c,specialization,page,limit)

@app.post('/api/candidate/vacancies/search')
def search_vacancies(payload:CandidateVacancySearch,user=Depends(candidate)):
    try:
        ensure_experience_vectors(None,user['id'])
    except (FileNotFoundError,ImportError,ValueError) as exc:
        raise HTTPException(503,f'Не удалось подготовить векторы опыта: {exc}') from exc
    with connect() as c:
        try:
            return rank_for_candidate(c,user['id'],payload.specialization,payload.page,payload.limit)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc

@app.post('/api/vacancies/{vacancy_id}/apply',status_code=201)
def apply_vacancy(vacancy_id:str,user=Depends(candidate)):
    with connect() as c:
        interaction_id=create_interaction(c,vacancy_id,user['id'],'application')
    return {'id':interaction_id,'status':'pending'}

@app.post('/api/vacancies/{vacancy_id}/invite',status_code=201)
def invite_candidate(vacancy_id:str,payload:InvitationPayload,user=Depends(employer)):
    with connect() as c:
        interaction_id=create_interaction(c,vacancy_id,payload.candidate_id,'invitation',user['id'])
    return {'id':interaction_id,'status':'pending'}

@app.get('/api/interactions')
def interactions(vacancy_id:str='',user=Depends(current_user)):
    with connect() as c:return {'items':list_interactions(c,user,vacancy_id or None)}

@app.get('/api/interactions/{interaction_id}')
def interaction_detail(interaction_id:str,user=Depends(current_user)):
    with connect() as c:
        row=get_interaction(c,interaction_id,user)
        result=dict(row)
        result['messages']=[dict(m) for m in c.execute('''SELECT m.id,m.sender_id,u.name sender_name,m.body,m.created_at
            FROM hiring_messages m JOIN users u ON u.id=m.sender_id WHERE m.interaction_id=?
            ORDER BY m.created_at,m.id''',(interaction_id,))]
        result['chat_enabled']=row['status']=='approved' and row['employer_id'] is not None
    return result

@app.post('/api/interactions/{interaction_id}/decision')
def decide_interaction(interaction_id:str,payload:DecisionPayload,user=Depends(current_user)):
    with connect() as c:status=change_interaction(c,interaction_id,user,payload.status)
    return {'id':interaction_id,'status':status}

@app.post('/api/interactions/{interaction_id}/messages',status_code=201)
def post_message(interaction_id:str,payload:MessagePayload,user=Depends(current_user)):
    with connect() as c:return add_message(c,interaction_id,user,payload.body)
@app.get('/api/candidates')
def list_candidates(q:str=Query(default='',max_length=200),page:int=Query(default=1,ge=1),limit:int=Query(default=20,ge=1,le=100),user=Depends(employer)):
    with connect() as c:
        where='(r.position_name LIKE ? OR r.all_technologies_json LIKE ?)';params=['%'+q+'%']*2
        total=c.execute('SELECT COUNT(*) FROM resumes r WHERE '+where,params).fetchone()[0]
        rows=c.execute('''SELECT r.id,r.user_id,r.position_name,r.specialization,r.location,r.experience_years,r.all_technologies_json,
        (SELECT COUNT(*) FROM item_vectors iv WHERE iv.user_id=r.user_id) vector_count,
        (SELECT COUNT(*) FROM work_items wi WHERE wi.user_id=r.user_id) item_count
        FROM resumes r WHERE '''+where+' ORDER BY r.id LIMIT ? OFFSET ?',params+[limit,(page-1)*limit]).fetchall()
    return {'items':[dict(r) for r in rows],'total':total,'page':page}
@app.get('/api/candidates/{resume_id}')
def candidate_details(resume_id:str,user=Depends(employer)):
    with connect() as c:
        r=c.execute('SELECT id,user_id,position_name,specialization,location,experience_years,expected_salary_min,all_technologies_json FROM resumes WHERE id=?',(resume_id,)).fetchone()
        if not r:raise HTTPException(404,'Резюме не найдено')
        items=c.execute('SELECT id,kind,title,company,start_date,end_date,description,split_method,needs_review FROM work_items WHERE resume_id=? ORDER BY ordinal',(resume_id,)).fetchall()
    return {'resume':dict(r),'items':[dict(i) for i in items]}

@app.get('/api/tests/technologies')
def technology_catalog(user=Depends(candidate)):
    with connect() as c:
        items=catalog_tests(c,user['id'])
        grades=sorted(technology_test_grades(c,user['id']),key=('Junior','Middle','Senior').index)
        return {'grade':grades[0] if len(grades)==1 else None,'grades':grades,'items':items}

@app.post('/api/tests/technologies/start')
def technology_start(payload:TechnologyStart,user=Depends(candidate)):
    with connect() as c:
        return start_technology(c,user['id'],payload.technology,payload.grade)

@app.get('/api/grades/declarations')
def grade_declarations(user=Depends(candidate)):
    with connect() as c:
        selected=selected_specializations(c,user['id'])
        rows=c.execute('''SELECT sg.specialization,sg.declared_grade,sg.current_grade,
          EXISTS(SELECT 1 FROM candidate_verified_grades vg WHERE vg.user_id=sg.user_id
            AND vg.specialization=sg.specialization) verified
          FROM candidate_specialization_grades sg WHERE sg.user_id=?''',(user['id'],)).fetchall()
    return {'grades':{r['specialization']:{'declared_grade':r['declared_grade'],'current_grade':r['current_grade'],
                                         'editable':r['current_grade']==r['declared_grade'] and not r['verified']}
                      for r in rows if r['specialization'] in selected}}

@app.put('/api/grades/declarations')
def save_grade_declarations(payload:GradeDeclarations,user=Depends(candidate)):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        selected=selected_specializations(c,user['id'])
        if not selected or set(payload.grades)!=set(selected) or any(
            grade not in ('Junior','Middle','Senior') for grade in payload.grades.values()):
            raise HTTPException(422,'Укажите грейд для каждой выбранной специализации')
        stamp=now()
        for spec,grade in payload.grades.items():
            if spec not in matrix():raise HTTPException(422,'Неизвестная специализация')
            existing=c.execute('''SELECT declared_grade,current_grade FROM candidate_specialization_grades
              WHERE user_id=? AND specialization=?''',(user['id'],spec)).fetchone()
            if existing and existing[0]!=grade:
                verified=c.execute('''SELECT 1 FROM candidate_verified_grades WHERE user_id=? AND specialization=?''',
                                   (user['id'],spec)).fetchone()
                if existing['current_grade']!=existing['declared_grade'] or verified:
                    raise HTTPException(409,'После подтверждения или повышения грейд нельзя изменить вручную')
                c.execute('''UPDATE candidate_specialization_grades SET declared_grade=?,current_grade=?,updated_at=?
                  WHERE user_id=? AND specialization=?''',(grade,grade,stamp,user['id'],spec))
            elif not existing:
                c.execute('''INSERT INTO candidate_specialization_grades VALUES(?,?,?,?,?,?)''',
                          (user['id'],spec,grade,grade,stamp,stamp))
    return grade_declarations(user)

@app.post('/api/tests/technologies/{attempt_id}/submit')
def technology_submit(attempt_id:str,payload:AnswersPayload,user=Depends(candidate)):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        return submit_technology(c,user['id'],attempt_id,payload.answers)

@app.get('/api/grades/progress')
def grades_progress(user=Depends(candidate)):
    with connect() as c:return {'specializations':grade_progress(c,user['id'])}

@app.get('/api/grades/current')
def grade_current(specialization:str='',user=Depends(candidate)):
    with connect() as c:
        if specialization and specialization not in selected_specializations(c,user['id']):
            raise HTTPException(422,'Специализация не выбрана')
        return current_grade_progress(c,user['id'],specialization or None)

@app.post('/api/grades/advance')
def grade_advance(specialization:str='',user=Depends(candidate)):
    with connect() as c:
        if specialization and specialization not in selected_specializations(c,user['id']):
            raise HTTPException(422,'Специализация не выбрана')
        c.execute('BEGIN IMMEDIATE')
        return advance_test_grade(c,user['id'],specialization or None)

@app.get('/api/tests/daily')
def get_daily(user=Depends(candidate)):
    with connect() as c:return daily_public(c,user['id'])

@app.post('/api/tests/daily/submit')
def complete_daily(payload:AnswersPayload,user=Depends(candidate)):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        return submit_daily(c,user['id'],payload.answers)

@app.get('/api/activity/calendar')
def my_calendar(user=Depends(candidate)):
    with connect() as c:return {'days':activity_calendar(c,user['id'])}

@app.get('/api/matches/{vacancy_id}')
def vacancy_matches(vacancy_id:str,limit:int=Query(default=20,ge=1,le=100),verified_only:bool=False,user=Depends(employer)):
    with connect() as c:
        row=c.execute('SELECT employer_id,status FROM vacancies WHERE id=?',(vacancy_id,)).fetchone()
        if not row:raise HTTPException(404,'Вакансия не найдена')
        if row['employer_id'] and row['employer_id']!=user['id']:
            raise HTTPException(403,'Вакансия другого работодателя')
        if row['status']!='open':raise HTTPException(409,'Вакансия закрыта и находится в архиве')
        try:items=rank_vacancy(c,vacancy_id,limit,verified_only=verified_only)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return {'vacancy_id':vacancy_id,'items':items,'verified_only':verified_only,'weights':WEIGHTS,
            'scoring_version':SCORING_VERSION,
            'warning':'У импортированных кандидатов отдельные показатели могут быть синтетическими; результаты зарегистрированных пользователей хранятся на сервере'}

from fastapi.staticfiles import StaticFiles
from .db import ROOT
if (ROOT / 'dist').is_dir():
    app.mount('/',StaticFiles(directory=ROOT / 'dist',html=True),name='site')
