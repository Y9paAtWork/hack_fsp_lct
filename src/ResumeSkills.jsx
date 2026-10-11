import React, {useState, useRef, useEffect} from 'react';
import {readResume} from './resume';
export default function ResumeSkills({onSkills,onExperience}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState(null);
  const version=useRef(0);
  useEffect(()=>()=>{version.current++;},[]);
  async function upload(file) {
    const id=++version.current;
    setError('');setResult(null);
    if (!file) {setBusy(false);return;}
    setBusy(true);
    try {
      const data=await readResume(file);
      if(id!==version.current)return;
      setResult(data);onSkills(data.skills);onExperience?.(data.experience);
    } catch(e) {if(id===version.current)setError(e.message || 'Не удалось прочитать резюме');}
    finally {if(id===version.current)setBusy(false);}
  }
  return <section className="panel" style={{padding:24}}>
    <h3>ДАННЫЕ ИЗ РЕЗЮМЕ</h3>
    <p>Загрузите PDF с текстом или TXT, до 5 МБ. Обработка выполняется в браузере.</p>
    <input aria-label="Резюме для извлечения навыков" type="file" accept=".pdf,.txt" onChange={e=>upload(e.target.files?.[0])}/>
    {busy&&<p role="status">Читаем резюме…</p>}
    {error&&<p role="alert">{error}</p>}
    {result&&<><p role="status">{result.skills.length ? `Найдено: ${result.skills.join(', ')}` : 'Известные технологии не найдены. Добавьте стек вручную.'}</p>{onExperience&&<p>Найдено мест работы: {result.experience?.length||0}. Проверьте и дополните их на шаге «Опыт».</p>}{onExperience&&result.experience?.length>0&&<details><summary>Распознанный опыт работы</summary>{result.experience.map((job,i)=><div key={i}><strong>{job.company||'Компания не определена'}</strong><p>{job.role}</p><p>{job.start} — {job.end}</p><p>{job.description}</p></div>)}</details>}{result.warnings?.map(w=><p key={w} role="status">{w}</p>)}{result.excluded?.length>0&&<p>Исключены упоминания с отрицанием: {result.excluded.map(e=>e.skill).join(', ')}</p>}<details><summary>Где найдены навыки</summary>{result.entities.map(e=><p key={`${e.start}-${e.skill}`}>{e.text} → {e.skill} ({e.evidence==='skills-section'?'раздел навыков':'упоминание'})</p>)}</details><p className="muted">Извлечение по справочнику и эвристикам. Проверьте список: упоминание навыка не подтверждает владение им.</p></>}
  </section>;
}
