import React,{useEffect,useState} from 'react';
import {api} from './api';
import {useProfile} from './ProfileContext';

const go=path=>{location.hash=path;window.scrollTo({top:0,behavior:'instant'})};
const technologies=value=>{try{const parsed=JSON.parse(value||'[]');return Array.isArray(parsed)?parsed:[]}catch{return []}};
const salaryLabel=item=>{
 const min=Number(item.salary_min),max=Number(item.salary_max);
 const hasMin=item.salary_min!=null&&min>0,hasMax=item.salary_max!=null&&max>0;
 const amount=value=>Number(value).toLocaleString('ru-RU');
 if(hasMin&&hasMax&&max>=min)return `${amount(min)}–${amount(max)} ₽/мес`;
 if(hasMin)return `от ${amount(min)} ₽/мес`;
 if(hasMax)return `до ${amount(max)} ₽/мес`;
 return 'Зарплата не указана';
};
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
   {data?.items.map((item,index)=>{
    const expanded=opened===item.id;
    const stack=technologies(item.all_technologies_json);
    const salary=salaryLabel(item);
    return <section className={`panel candidate-vacancy ${item.salary_mismatch?'salary-mismatch':''}`} key={item.id}>
     <button type="button" className="candidate-vacancy-toggle" aria-expanded={expanded} aria-controls={`vacancy-details-${item.id}`} onClick={()=>setOpened(expanded?'':item.id)}>
      <span className="candidate-vacancy-summary"><strong>{item.vacancy_name}</strong><span className="candidate-vacancy-salary">{salary}</span>{item.salary_mismatch&&<span className="salary-warning">Максимальная зарплата ниже ваших ожиданий</span>}</span>
      <span className="candidate-vacancy-toggle-meta">{ranked&&<span className="match-placeholder">Сходство {item.score.toFixed(3)}</span>}<span className="candidate-vacancy-more">{expanded?'Скрыть':'Подробнее'}</span></span>
     </button>
     {expanded&&<div id={`vacancy-details-${item.id}`} className="candidate-vacancy-expanded">
      <p className="eyebrow">{ranked?`#${(page-1)*20+index+1} · `:''}{item.specialization} · {item.grade} · {item.region||'Регион не указан'}</p>
      <p className="candidate-vacancy-company">{item.company_name||'Вакансия из каталога'}</p>
      <h3>Описание вакансии</h3><p>{item.description||'Описание пока не указано.'}</p>
      <h3>Требуемый стек</h3>{stack.length?<div className="chips">{stack.map(tech=><span className="chip" key={tech}>{tech}</span>)}</div>:<p className="muted">Стек не указан.</p>}
      {item.required_technologies?.length>0&&<><h3>Обязательные технологии</h3><div className="chips">{item.required_technologies.map(tech=><span className="chip" key={tech}>{tech}</span>)}</div></>}
      {item.responsibilities&&<><h3>Задачи</h3><p>{item.responsibilities}</p></>}
      {item.position_requirements&&<><h3>Требования</h3><p>{item.position_requirements}</p></>}
      {ranked&&<div className="vacancy-score-detail"><span>Опыт {item.components.experience.toFixed(3)}</span><span>Технологии {item.components.skills.toFixed(3)}</span><span>Совпало технологий {item.matched_technologies}/{item.requested_technologies}</span>{item.best_experience&&<span>Лучший блок: {item.best_experience.title||'Проект или работа'}</span>}</div>}
      {!item.employer_id&&<p className="muted">Это вакансия из каталога; работодатель ещё не подключён к платформе.</p>}
      <div className="row-actions">{applied.has(item.id)?<button className="btn" onClick={()=>go(`/candidate/chat/${applied.get(item.id).id}`)}>Отклик: {statusLabel[applied.get(item.id).status]}</button>:<button className="btn btn-primary" disabled={busy===item.id} onClick={()=>apply(item.id)}>{busy===item.id?'Отправляем…':'Откликнуться'}</button>}</div>
     </div>}
    </section>;
   })}
  </div>
  {data&&!data.items.length&&<section className="panel empty-box">{specs.length?'По выбранной специализации подходящих открытых вакансий нет.':'Добавьте специализацию в профиле, чтобы запустить подбор.'}</section>}
  {data&&data.total>20&&<div className="row-actions vacancy-pagination"><button className="btn" disabled={page===1||loading} onClick={()=>setPage(p=>p-1)}>Назад</button><span>Страница {page}</span><button className="btn" disabled={loading||page*20>=data.total} onClick={()=>setPage(p=>p+1)}>Далее</button></div>}
 </>;
}
