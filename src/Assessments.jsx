import React,{useEffect,useRef,useState} from 'react';
import {api} from './api';

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
 const [sections,setSections]=useState([]),[attempt,setAttempt]=useState(null),[attemptSpec,setAttemptSpec]=useState(''),[answers,setAnswers]=useState({}),[result,setResult]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const requestId=useRef(0);
 const reload=()=>{const current=++requestId.current;return api('/tests/technologies').then(r=>{if(current!==requestId.current)return;setSections(r.specializations||[r]);setError('')}).catch(e=>{if(current===requestId.current)setError(e.message)})};
 useEffect(()=>{reload();return()=>{requestId.current++}},[]);
 const start=async(item,specialization)=>{setBusy(true);setError('');try{const data=await api('/tests/technologies/start',{method:'POST',body:{technology:item.technology,grade:item.grade,specialization}});setAttempt(data);setAttemptSpec(specialization);setAnswers({});setResult(null);}catch(e){setError(e.message)}finally{setBusy(false)}};
 const submit=async()=>{setBusy(true);setError('');try{const data=await api(`/tests/technologies/${attempt.attempt_id}/submit`,{method:'POST',body:{answers}});setResult(data);setAttempt(null);reload();}catch(e){setError(e.message)}finally{setBusy(false)}};
 const cards=(items,specialization)=><div className="assessment-grid">{items.map(item=><section className={`panel assessment-card${item.credited?' assessment-card-credited':''}`} key={`${item.technology}-${item.grade}`}><h3>{item.technology}</h3><p>{item.grade} · {item.questions} заданий</p>{item.credited?<p className="assessment-credit">{item.credited_by_grade===item.grade?`Уровень пройден: ${item.credited_score} / 100`:`Зачтено по результату ${item.credited_by_grade}: 100 / 100`}</p>:<p>{item.last_score===null?'Пока не проходили':`Последний результат: ${item.last_score} / 100`}</p>}{item.retry_at&&!item.available&&<small>Доступно с {new Date(item.retry_at).toLocaleString('ru-RU')}</small>}<button className="btn btn-primary" disabled={!item.available||busy} onClick={()=>start(item,specialization)}>{item.credited&&!item.available?'Зачтено':item.credited?'Пересдать':item.last_score===null?'Начать':'Открыть тест'}</button></section>)}</div>;
 return <><div className="eyebrow">ЛИЧНЫЙ КАБИНЕТ / ТЕСТИРОВАНИЕ</div><h1>ТЕСТЫ ПО ТЕХНОЛОГИЯМ</h1><p className="page-sub">Для каждой специализации показаны тесты её грейда. Зачёт — от 50 баллов; тест более высокого уровня даёт 100 баллов на всех уровнях ниже. При результате от 90 баллов можно сразу попробовать следующий уровень.</p>{error&&<p role="alert">{error}</p>}
 {result&&<section className="panel assessment-result"><div className="eyebrow">{attemptSpec}</div><h2>{result.technology} · {result.grade}</h2><strong>{result.score} / 100</strong><p>{result.passed?'Уровень пройден':'Порог 50 баллов не достигнут'}{result.suggested_grade?` · Попробуйте уровень ${result.suggested_grade}`:result.next_level_available?` · Доступен уровень ${result.next_grade}`:''}</p><div className="assessment-result-actions">{result.suggested_grade&&<button className="btn btn-primary" disabled={busy} onClick={()=>start({technology:result.technology,grade:result.suggested_grade},attemptSpec)}>Пройти {result.suggested_grade}</button>}{result.next_level_available&&<button className="btn btn-primary" disabled={busy} onClick={()=>start({technology:result.technology,grade:result.next_grade},attemptSpec)}>Пройти {result.next_grade}</button>}<button className="btn" onClick={()=>setResult(null)}>К списку</button></div></section>}
 {attempt?<><div className="assessment-head"><button className="btn" onClick={()=>setAttempt(null)}>К списку тестов</button><span>{attemptSpec} · {attempt.technology} · {attempt.grade} · {attempt.questions.length} заданий</span></div>{attempt.questions.map(q=><Question key={q.id} question={q} value={answers[q.id]} onChange={value=>setAnswers(old=>({...old,[q.id]:value}))}/>)}<button className="btn btn-primary" disabled={busy||Object.keys(answers).length!==attempt.questions.length} onClick={submit}>{busy?'Проверяем…':'Завершить и проверить'}</button></>:
 sections.length?sections.map(section=>{const required=(section.items||[]).filter(item=>item.grade===section.grade),additional=(section.items||[]).filter(item=>item.grade!==section.grade);const credited=required.filter(item=>item.credited).length;return <section className="assessment-section" key={section.specialization} aria-label={`Тесты: ${section.specialization}`}><div className="assessment-section-head"><div><span className="eyebrow">СПЕЦИАЛИЗАЦИЯ</span><h2>{section.specialization}</h2><p>Требуемый уровень: <strong>{section.grade}</strong></p></div><span className="assessment-count">Зачтено {credited} / {required.length}</span></div>{required.length?cards(required,section.specialization):<p className="panel assessment-empty">Для вашего стека пока нет тестов уровня {section.grade}.</p>}{additional.length>0&&<details className="assessment-other"><summary>Другие доступные уровни ({additional.length})</summary><p>Они открываются после результата теста на основном уровне.</p>{cards(additional,section.specialization)}</details>}</section>}):<p className="panel assessment-empty">Выберите специализацию и добавьте технологии в стек, чтобы увидеть тесты.</p>}</>;
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
 return <div className="grade-progress"><h2>ПОДТВЕРЖДЕНИЕ ГРЕЙДА</h2>{error&&<p role="alert">{error}</p>}{data&&Object.entries(data).map(([spec,p])=>{
  const target=p.next_grade?p.levels[p.next_grade]:null;
  const confirmingCurrent=p.next_grade===p.current_grade;
  const status=p.confirmed_grade===p.current_grade
   ?`${p.current_grade} подтверждён`
   :`${p.current_grade} не подтверждён${p.confirmed_grade?` · ранее подтверждён ${p.confirmed_grade}`:''}`;
  return <section className="panel" key={spec}><h3>{spec}</h3><p>Текущий уровень тестов: <strong>{p.current_grade}</strong> · {status}</p>{p.can_advance&&<button className="btn" onClick={()=>advance(spec)}>Перейти на {p.next_test_grade}</button>}{target?<><p>{confirmingCurrent?'Подтверждение уровня':'Следующий уровень'}: <strong>{p.next_grade}</strong>. Групп: {target.groups_done} / {target.groups_required}, на нужном уровне: {target.at_target_done} / {target.at_target_required}.</p><div className="grade-groups">{target.groups.map((g,i)=><div key={g.name} className={g.done?'done':''}><strong>{g.done?'✓':'○'} {g.name}{i<target.mandatory_first?' · обязательно':''}</strong><small>{g.done?g.matched.join(', '):`Нужно ${g.needed}: ${g.alternatives.join(', ')}`}</small></div>)}</div></>:<p>Senior подтверждён — максимальный уровень.</p>}</section>})}{data&&!Object.keys(data).length&&<p>Выберите специализацию в анкете.</p>}</div>;
}

export function RealActivityCalendar(){
 const [days,setDays]=useState([]);
 useEffect(()=>{api('/activity/calendar').then(r=>setDays(r.days)).catch(()=>{})},[]);
 return <section className="panel activity-panel"><div className="activity-heading"><div><div className="eyebrow">ВАША АКТИВНОСТЬ</div><h2>КАЛЕНДАРЬ АКТИВНОСТИ</h2><p>Посещения и ежедневные тесты по московским датам.</p></div><div className="activity-stats"><span><b>{days.filter(d=>d.visited).length}</b> посещений</span><span><b>{days.filter(d=>d.test_score!==null).length}</b> тестов</span></div></div><div className="calendar-scroll"><div className="activity-calendar" role="img" aria-label="Календарь активности за 52 недели">{Array.from({length:52},(_,week)=><div className="calendar-week" key={week}>{days.slice(week*7,week*7+7).map(day=><span key={day.date} className={`calendar-day level-${day.test_score!==null?3:day.visited?1:0} ${day.test_score!==null?'test-day':''}`} title={`${day.date}: ${day.test_score!==null?`тест ${day.test_score}/100`:day.visited?'посещение':'нет активности'}`}/>)}</div>)}</div></div></section>;
}
