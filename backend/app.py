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
from .import_data import dump

hasher=PasswordHasher(time_cost=2,memory_cost=19456,parallelism=1)
COOKIE='fsp_session'
SESSION_TTL=7*24*3600
attempts=defaultdict(deque)

@asynccontextmanager
async def lifespan(app):
    init_db()
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
    uid=uuid.uuid4().hex;timestamp=now()
    try:
        with connect() as c:
            c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',(uid,email,hasher.hash(payload.password),payload.role,payload.name.strip(),'registration',timestamp))
            if payload.role=='candidate':
                profile=empty_profile(payload.name.strip(),email)
                c.execute('INSERT INTO candidate_profiles(user_id,profile_json,updated_at) VALUES(?,?,?)',(uid,dump(profile),timestamp))
                sync_resume(c,{'id':uid},profile)
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
    return {'profile':json.loads(row[0]),'revision':row[1]}
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
def list_vacancies(q:str=Query(default='',max_length=200),page:int=Query(default=1,ge=1),limit:int=Query(default=20,ge=1,le=100),mine:bool=False,user=Depends(employer)):
    where='vacancy_name LIKE ?';params=['%'+q+'%']
    if mine:where+=' AND employer_id=?';params.append(user['id'])
    with connect() as c:
        total=c.execute('SELECT COUNT(*) FROM vacancies WHERE '+where,params).fetchone()[0]
        rows=c.execute('SELECT id,vacancy_name,specialization,region,salary_min,salary_max,experience_requirements,description,all_technologies_json,employer_id FROM vacancies WHERE '+where+' ORDER BY id LIMIT ? OFFSET ?',params+[limit,(page-1)*limit]).fetchall()
    return {'items':[dict(r) for r in rows],'total':total,'page':page}
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
        r=c.execute('SELECT id,user_id,position_name,specialization,location,experience_years,all_technologies_json FROM resumes WHERE id=?',(resume_id,)).fetchone()
        if not r:raise HTTPException(404,'Резюме не найдено')
        items=c.execute('SELECT id,kind,title,company,start_date,end_date,description,split_method,needs_review FROM work_items WHERE resume_id=? ORDER BY ordinal',(resume_id,)).fetchall()
    return {'resume':dict(r),'items':[dict(i) for i in items]}

from fastapi.staticfiles import StaticFiles
from .db import ROOT
if (ROOT / 'dist').is_dir():
    app.mount('/',StaticFiles(directory=ROOT / 'dist',html=True),name='site')
