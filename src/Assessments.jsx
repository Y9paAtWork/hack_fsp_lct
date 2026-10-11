import React,{useEffect,useState} from 'react';
import {api} from './api';
import {useProfile} from './ProfileContext';

function Question({question,value,onChange}){
 const opts=question.options||[];
 return <section className="panel assessment-question"><span className="eyebrow">{question.type==='write_code'?'ЗАДАНИЕ НА КОД':question.type==='multi'?'ВЫБЕРИТЕ ВСЕ ВЕРНЫЕ ОТВЕТЫ':'ВОПРОС'}</span><h3>{question.text}</h3>{question.code_snippet&&<pre>{question.code_snippet}</pre>}
 {['single','multi'].includes(question.type)?<div className="assessment-answers">{opts.map(option=>{
 const chosen=question.type==='multi'?(Array.isArray(value)&&value.includes(option.value)):value===option.value;
 return <label key={option.value} className={chosen?'selected':''}><input type={question.type==='multi'?'checkbox':'radio'} name={String(question.id)} checked={!!chosen} onChange={()=>onChange(question.type==='multi'?(chosen?value.filter(x=>x!==option.value):[...(value||[]),option.value]):option.value)}/>{option.label}</label>;})}</div>:
 <textarea className="assessment-text" spellCheck={question.type==='write_code'?false:undefined} value={value||''} onChange={e=>onChange(e.target.value)} placeholder={question.type==='write_code'?'Введите решение':'Введите ответ'}/>}
 </section>;
}

export function TechnologyTests(){
 const profile=useProfile()?.profile;
 const specializations=profile?.selected||[];
 const [specialization,setSpecialization]=useState(profile?.active||specializations[0]||'');
 const [items,setItems]=useState([]),[grade,setGrade]=useState(''),[attempt,setAttempt]=useState(null),[answers,setAnswers]=useState({}),[result,setResult]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const reload=()=>api(`/tests/technologies${specialization?`?specialization=${encodeURIComponent(specialization)}`:''}`).then(r=>{setGrade(r.grade||'');setItems((r.items||[]).filter(item=>!r.grade||item.grade===r.grade));setError('')}).catch(e=>setError(e.message));
 useEffect(()=>{reload()},[specialization]);
 const start=async item=>{setBusy(true);setError('');try{const data=await api('/tests/technologies/start',{method:'POST',body:{technology:item.technology,grade:item.grade,specialization}});setAttempt(data);setAnswers({});setResult(null);}catch(e){setError(e.message)}finally{setBusy(false)}};
 const submit=async()=>{setBusy(true);setError('');try{const data=await api(`/tests/technologies/${attempt.attempt_id}/submit`,{method:'POST',body:{answers}});setResult(data);setAttempt(null);reload();}catch(e){setError(e.message)}finally{setBusy(false)}};
 return <><div className="eyebrow">ЛИЧНЫЙ КАБИНЕТ / ТЕСТИРОВАНИЕ</div><h1>ТЕСТЫ ПО ТЕХНОЛОГИЯМ</h1><p className="page-sub">Вопросы по вашему стеку{grade?` и текущему грейду ${grade}`:''}. Результаты сохраняются в аккаунте.</p>{specializations.length>0&&<label className="field assessment-specialization"><span>Специализация</span><select value={specialization} onChange={e=>{setSpecialization(e.target.value);setAttempt(null);setResult(null);setItems([])}}>{specializations.map(spec=><option key={spec}>{spec}</option>)}</select></label>}{error&&<p role="alert">{error}</p>}
 {result&&<section className="panel assessment-result"><h2>{result.technology} · {result.grade}</h2><strong>{result.score} / 100</strong><p>{result.passed?'Уровень пройден':'Порог уровня пока не достигнут'}{result.next_level_available?' · Высокий результат поможет при повышении грейда':''}</p><button className="btn" onClick={()=>setResult(null)}>К списку</button></section>}
 {attempt?<><div className="assessment-head"><button className="btn" onClick={()=>setAttempt(null)}>К списку тестов</button><span>{attempt.technology} · {attempt.grade} · {attempt.questions.length} заданий</span></div>{attempt.questions.map(q=><Question key={q.id} question={q} value={answers[q.id]} onChange={value=>setAnswers(old=>({...old,[q.id]:value}))}/>)}<button className="btn btn-primary" disabled={busy||Object.keys(answers).length!==attempt.questions.length} onClick={submit}>{busy?'Проверяем…':'Завершить и проверить'}</button></>:
 <div className="assessment-grid">{items.length?items.map(item=><section className="panel assessment-card" key={`${item.technology}-${item.grade}`}><h3>{item.technology}</h3><p>{item.grade} · {item.questions} заданий</p><p>{item.last_score===null?'Пока не проходили':`Последний результат: ${item.last_score} / 100`}</p>{item.retry_at&&!item.available&&<small>Пересдача с {new Date(item.retry_at).toLocaleString('ru-RU')}</small>}<button className="btn btn-primary" disabled={!item.available||busy} onClick={()=>start(item)}>{item.last_score===null?'Начать':'Открыть тест'}</button></section>):<section className="panel assessment-card">Для вашего стека и текущего грейда пока нет доступных тестов.</section>}</div>}</>;
}

export function DailyPractice(){
 const [data,setData]=useState(null),[answers,setAnswers]=useState({}),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let live=true;let timer;
  const load=()=>api('/tests/daily').then(r=>{if(live){setData(r);setAnswers({})}}).catch(e=>{if(live)setError(e.message)});
  const schedule=()=>{const moscowMillis=Date.now()+3*60*60*1000;const remaining=86400000-(moscowMillis%86400000);
   timer=setTimeout(()=>{load();schedule()},remaining+50)};
  load();schedule();return()=>{live=false;clearTimeout(timer)};
 },[]);
 const submit=async()=>{setBusy(true);setError('');try{const result=await api('/tests/daily/submit',{method:'POST',body:{answers}});setData(old=>({...old,completed:true,score:result.score}));}catch(e){setError(e.message)}finally{setBusy(false)}};
 return <><div className="eyebrow">ЛИЧНЫЙ КАБИНЕТ / ЕЖЕДНЕВНЫЙ ТЕСТ</div><h1>ЕЖЕДНЕВНАЯ ПРАКТИКА</h1>{error&&<p role="alert">{error}</p>}{data&&<><p className="page-sub">{data.date_msk} · {data.specialization} · {data.grade} · 3 вопроса. Новый набор появится в 00:00 МСК.</p>{data.completed?<section className="panel assessment-result"><h2>Сегодняшний тест завершён</h2><strong>{data.score} / 100</strong><p>Результат сохранён в статистике активности.</p></section>:<>{data.questions.map(q=><Question key={q.id} question={q} value={answers[q.id]} onChange={value=>setAnswers(old=>({...old,[q.id]:value}))}/>)}<button className="btn btn-primary" disabled={busy||Object.keys(answers).length!==3} onClick={submit}>{busy?'Проверяем…':'Завершить тест'}</button></>}</>}</>;
}

export function GradeProgress(){
 const [data,setData]=useState(null),[error,setError]=useState('');
 const reload=()=>api('/grades/progress').then(progress=>setData(progress.specializations)).catch(e=>setError(e.message));
 useEffect(()=>{reload()},[]);
 const advance=async spec=>{try{await api(`/grades/advance?specialization=${encodeURIComponent(spec)}`,{method:'POST'});await reload()}catch(e){setError(e.message)}};
 return <div className="grade-progress"><h2>ПОДТВЕРЖДЕНИЕ ГРЕЙДА</h2>{error&&<p role="alert">{error}</p>}{data&&Object.entries(data).map(([spec,p])=>{const next=p.levels[p.next_grade];return <section className="panel" key={spec}><h3>{spec}</h3><p>Текущий уровень тестов: <strong>{p.current_grade}</strong> · {p.confirmed_grade?`подтверждён ${p.confirmed_grade}`:'грейд не подтверждён'}</p>{p.can_advance&&<button className="btn" onClick={()=>advance(spec)}>Перейти на {p.next_test_grade}</button>}<p>Следующий уровень: {p.next_grade}. Групп: {next.groups_done} / {next.groups_required}, на нужном уровне: {next.at_target_done} / {next.at_target_required}.</p><div className="grade-groups">{next.groups.map((g,i)=><div key={g.name} className={g.done?'done':''}><strong>{g.done?'✓':'○'} {g.name}{i<next.mandatory_first?' · обязательно':''}</strong><small>{g.done?g.matched.join(', '):`Нужно ${g.needed}: ${g.alternatives.join(', ')}`}</small></div>)}</div></section>})}{data&&!Object.keys(data).length&&<p>Выберите специализацию в анкете.</p>}</div>;
}

export function RealActivityCalendar(){
 const [days,setDays]=useState([]);
 useEffect(()=>{api('/activity/calendar').then(r=>setDays(r.days)).catch(()=>{})},[]);
 return <section className="panel activity-panel"><div className="activity-heading"><div><div className="eyebrow">ВАША АКТИВНОСТЬ</div><h2>КАЛЕНДАРЬ АКТИВНОСТИ</h2><p>Посещения и ежедневные тесты по московским датам.</p></div><div className="activity-stats"><span><b>{days.filter(d=>d.visited).length}</b> посещений</span><span><b>{days.filter(d=>d.test_score!==null).length}</b> тестов</span></div></div><div className="calendar-scroll"><div className="activity-calendar" role="img" aria-label="Календарь активности за 52 недели">{Array.from({length:52},(_,week)=><div className="calendar-week" key={week}>{days.slice(week*7,week*7+7).map(day=><span key={day.date} className={`calendar-day level-${day.test_score!==null?3:day.visited?1:0} ${day.test_score!==null?'test-day':''}`} title={`${day.date}: ${day.test_score!==null?`тест ${day.test_score}/100`:day.visited?'посещение':'нет активности'}`}/>)}</div>)}</div></div></section>;
}
