import React,{useState,useEffect} from 'react';
import {api} from './api';
export default function DatabaseEmployer({page}){
 const [q,setQ]=useState(''),[number,setNumber]=useState(1),[result,setResult]=useState(null),[detail,setDetail]=useState(null),[error,setError]=useState(''),[company,setCompany]=useState(null),[stats,setStats]=useState(null),[loading,setLoading]=useState(false);
 const candidates=page==='search'||page==='candidates';
 useEffect(()=>{setNumber(1);setDetail(null);setResult(null);},[page]);
 useEffect(()=>{let live=true;setError('');setLoading(true);
 const path=page==='home'?'/database/stats':page==='company'?'/employer/profile':page==='vacancies'||candidates?`/${candidates?'candidates':'vacancies'}?q=${encodeURIComponent(q)}&page=${number}&limit=20`:null;
 if(!path){setLoading(false);return;}
 const timer=setTimeout(()=>{api(path).then(r=>{if(!live)return;if(page==='home')setStats(r);else if(page==='company')setCompany(r);else setResult(r);}).catch(e=>{if(live)setError(e.message);}).finally(()=>{if(live)setLoading(false);});},200);
 return()=>{live=false;clearTimeout(timer);};
 },[page,q,number]);
 const show=async id=>{try{setDetail(await api('/candidates/'+id));}catch(e){setError(e.message);}};
 return <><h1>{page==='home'?'КАБИНЕТ РАБОТОДАТЕЛЯ':page==='company'?'ПРОФИЛЬ КОМПАНИИ':candidates?'КАНДИДАТЫ ИЗ БД':page==='vacancies'?'ВАКАНСИИ ИЗ БД':'РАЗДЕЛ'}</h1>
 {error&&<p role="alert">{error}</p>}{loading&&<p role="status">Загрузка…</p>}
 {page==='home'&&stats&&<section className="panel" style={{padding:24}}><p>Резюме: {stats.resumes.toLocaleString()}</p><p>Вакансии: {stats.vacancies.toLocaleString()}</p><p>Опыт и проекты: {stats.work_items.toLocaleString()}</p><p>Готовых векторов опыта: {stats.item_vectors.toLocaleString()}</p><p>Частей текста в очереди: {stats.embedding_status.pending||0}</p><p>Ошибок векторизации: {stats.embedding_status.failed||0}</p></section>}
 {page==='company'&&company&&<form className="panel" style={{padding:24}} onSubmit={async e=>{e.preventDefault();try{await api('/employer/profile',{method:'PUT',body:company});setError('');}catch(e){setError(e.message);}}}>{[['company_name','Компания'],['about','О компании'],['website','Сайт']].map(([key,label])=><label className="field" key={key}><span>{label}</span><input required={key==='company_name'} value={company[key]} onChange={e=>setCompany({...company,[key]:e.target.value})}/></label>)}<button className="btn btn-primary">Сохранить</button></form>}
 {(candidates||page==='vacancies')&&<><label className="field"><span>Поиск {candidates?'по должности или технологии':'по названию вакансии'}</span><input value={q} onChange={e=>{setQ(e.target.value);setNumber(1);setDetail(null);}}/></label><p>Найдено: {result?.total??'…'}</p>
 {result?.items.map(item=><section className="panel" key={item.id} style={{padding:24,marginBottom:16}}><h3>{item.position_name||item.vacancy_name||'Без названия'}</h3><p>{item.specialization} · {item.location||item.region}</p><p>{JSON.parse(item.all_technologies_json||'[]').join(' · ')}</p>{candidates?<><p>Опыт/проекты: {item.item_count}; готовые векторы: {item.vector_count}</p><button className="btn" onClick={()=>show(item.id)}>Посмотреть опыт</button></>:<><p>{item.description}</p><p>Зарплата: {item.salary_min??'—'} — {item.salary_max??'—'}</p></>}</section>)}
 <div className="row-actions"><button className="btn" disabled={number<=1} onClick={()=>setNumber(n=>n-1)}>Назад</button><span>Страница {number}</span><button className="btn" disabled={!result||number*20>=result.total} onClick={()=>setNumber(n=>n+1)}>Далее</button></div>
 {detail&&<section className="panel" style={{padding:24,marginTop:24}}><h2>ОПЫТ И ПРОЕКТЫ</h2><button className="btn" onClick={()=>setDetail(null)}>Закрыть</button>{detail.items.map(item=><article key={item.id} style={{marginTop:24}}><h3>{item.kind==='project'?'Проект':item.kind==='unclassified'?'Неразобранный фрагмент':'Работа'}: {item.title||'без должности'}</h3><p>{item.company}</p><p>{item.start_date} — {item.end_date}</p><p style={{whiteSpace:'pre-wrap'}}>{item.description}</p>{!!item.needs_review&&<p className="muted">Разбиение требует проверки.</p>}</article>)}</section>}</>}
 {!['home','company','vacancies','search','candidates'].includes(page)&&<p>Этот раздел пока не подключён к серверу.</p>}
 </>;
}
