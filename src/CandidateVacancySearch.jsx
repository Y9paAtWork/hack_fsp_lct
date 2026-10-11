import React,{useEffect,useState} from 'react';
import {api} from './api';
import {useProfile} from './ProfileContext';

const go=path=>{location.hash=path;window.scrollTo({top:0,behavior:'instant'})};
const technologies=value=>{try{return JSON.parse(value||'[]')}catch{return []}};
const statusLabel={pending:'Ожидает решения',approved:'Одобрено',rejected:'Отклонено'};

export default function CandidateVacancies(){
 const {profile}=useProfile();
 const specs=profile.selected||[];
 const [specialization,setSpecialization]=useState(profile.active||'');
 const [ranked,setRanked]=useState(true),[page,setPage]=useState(1),[refresh,setRefresh]=useState(0);
 const [data,setData]=useState(null),[applications,setApplications]=useState([]);
 const [opened,setOpened]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false),[busy,setBusy]=useState('');

 useEffect(()=>{
  let live=true;
  setData(null);setError('');setLoading(true);
  const vacancies=ranked
   ?api('/candidate/vacancies/search',{method:'POST',body:{specialization,page,limit:20}})
   :api(`/public/vacancies?specialization=${encodeURIComponent(specialization)}&page=${page}&limit=20`);
  Promise.all([vacancies,api('/interactions')])
   .then(([result,interactions])=>{if(live){setData(result);setApplications(interactions.items.filter(item=>item.kind==='application'))}})
   .catch(e=>{if(live)setError(e.message)})
   .finally(()=>{if(live)setLoading(false)});
  return()=>{live=false};
 },[specialization,page,ranked,refresh]);

 const applied=new Map(applications.map(item=>[item.vacancy_id,item]));
 const apply=async id=>{
  setBusy(id);setError('');
  try{
   await api(`/vacancies/${id}/apply`,{method:'POST'});
   const interactions=await api('/interactions');
   setApplications(interactions.items.filter(item=>item.kind==='application'));
  }catch(e){setError(e.message)}finally{setBusy('')}
 };
 const selectMode=value=>{setRanked(value);setPage(1);setOpened('')};
 const changeSpecialization=value=>{setSpecialization(value);setPage(1);setOpened('')};

 return <>
  <div className="eyebrow">ЛИЧНЫЙ КАБИНЕТ / ВАКАНСИИ</div>
  <h1>ПОИСК ВАКАНСИИ</h1>
  <p className="page-sub">Подбор сравнивает ваши отдельные работы и проекты с задачами открытых вакансий. Учитываются также технологии из вашего стека.</p>
  <div className="vacancy-search-controls panel">
   <div className="match-source-tabs">
    <button className={ranked?'active':''} onClick={()=>selectMode(true)}>По моему резюме</button>
    <button className={!ranked?'active':''} onClick={()=>selectMode(false)}>Каталог вакансий</button>
   </div>
   <label className="field"><span>Специализация</span><select value={specialization} onChange={e=>changeSpecialization(e.target.value)}><option value="">Все мои направления</option>{specs.map(spec=><option key={spec} value={spec}>{spec}</option>)}</select></label>
   <div className="vacancy-search-count"><span>{loading?'Подбираем вакансии…':`${data?.total??0} вакансий`}</span>{ranked&&<button className="btn" disabled={loading} onClick={()=>setRefresh(x=>x+1)}>Обновить подбор</button>}</div>
  </div>
  {error&&<p role="alert">{error} {ranked&&'Можно открыть каталог вакансий.'}</p>}
  {ranked&&data&&<p className="muted vacancy-search-note">Скор от 0 до 1 · опыт 70% · технологии 25% · активность 2,5% · достижения 2,5%.{data.experience_blocks===0?' Векторизованного опыта пока нет; опыт получает 0.':''}{data.experience_pending>0?` Ожидают обработки: ${data.experience_pending} блоков опыта.`:''}{data.stale_vacancies>0?` Вакансий с устаревшим вектором: ${data.stale_vacancies}.`:''}</p>}
  <div className="candidate-vacancies">
   {data?.items.map((item,index)=><section className="panel candidate-vacancy" key={item.id}>
    <div className="employer-vacancy-head"><div><span className="eyebrow">{ranked?`#${(page-1)*20+index+1} · `:''}{item.specialization} · {item.region||'Регион не указан'}</span><h2>{item.vacancy_name}</h2><p>{item.company_name||'Вакансия из каталога'}</p></div>{ranked&&<span className="match-placeholder">Сходство {item.score.toFixed(3)}</span>}</div>
    <p>{item.description}</p>
    {ranked&&<div className="vacancy-score-detail"><span>Опыт {item.components.experience.toFixed(3)}</span><span>Технологии {item.components.skills.toFixed(3)}</span><span>Совпало технологий {item.matched_technologies}/{item.requested_technologies}</span>{item.best_experience&&<span>Лучший блок: {item.best_experience.title||'Проект или работа'}</span>}</div>}
    {opened===item.id&&<div className="vacancy-details"><h3>Задачи</h3><p>{item.responsibilities||'Задачи приведены в общем описании вакансии.'}</p><h3>Требования</h3><p>{item.position_requirements||'Не указаны'}</p><h3>Стек</h3><div className="chips">{technologies(item.all_technologies_json).map(tech=><span className="chip" key={tech}>{tech}</span>)}</div>{!item.employer_id&&<p className="muted">Это вакансия из каталога; работодатель ещё не подключён к платформе.</p>}</div>}
    <div className="row-actions"><button className="btn" onClick={()=>setOpened(opened===item.id?'':item.id)}>{opened===item.id?'Скрыть описание':'Подробнее'}</button>{applied.has(item.id)?<button className="btn" onClick={()=>go(`/candidate/chat/${applied.get(item.id).id}`)}>Отклик: {statusLabel[applied.get(item.id).status]}</button>:<button className="btn btn-primary" disabled={busy===item.id} onClick={()=>apply(item.id)}>{busy===item.id?'Отправляем…':'Откликнуться'}</button>}</div>
   </section>)}
  </div>
  {data&&!data.items.length&&<section className="panel empty-box">{specs.length?'По выбранной специализации подходящих открытых вакансий нет.':'Добавьте специализацию в профиле, чтобы запустить подбор.'}</section>}
  {data&&data.total>20&&<div className="row-actions vacancy-pagination"><button className="btn" disabled={page===1||loading} onClick={()=>setPage(p=>p-1)}>Назад</button><span>Страница {page}</span><button className="btn" disabled={loading||page*20>=data.total} onClick={()=>setPage(p=>p+1)}>Далее</button></div>}
 </>;
}
