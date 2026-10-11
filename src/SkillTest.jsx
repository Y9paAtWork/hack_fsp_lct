import React, {useState} from 'react';
import ResumeSkills from './ResumeSkills';
import {useProfile} from './ProfileContext';
import {profileSkills} from './profileStore';
import {selectQuestions,gradeQuiz} from './skillQuiz';
export default function SkillTest(){
  const {profile,updateProfile}=useProfile();
  const skills=profileSkills(profile);
  const [answers,setAnswers]=useState({}),[result,setResult]=useState(null);
  const questions=selectQuestions(skills);
  const update=(next)=>{const spec=profile.active||profile.selected[0]||'Other IT';updateProfile({selected:profile.selected.length?profile.selected:[spec],active:spec,stacks:{...profile.stacks,[spec]:next},quiz:null});setAnswers({});setResult(null);};
  return <section className="panel" style={{padding:24,marginBottom:24}}><h2>ТЕСТ ПО РЕЗЮМЕ</h2><ResumeSkills onSkills={update}/>
    <p>Ваш стек: {skills.join(', ')||'загрузите резюме'}</p>
    {!!skills.length&&!questions.length&&<p>Для найденных технологий вопросов пока нет.</p>}
    {skills.some(s=>!questions.some(q=>q.skill===s))&&<p className="muted">Не все технологии пока покрыты вопросами.</p>}
    {questions.map((q,i)=><fieldset key={q.skill} disabled={!!result} style={{margin:'20px 0'}}><legend>{q.skill}: {q.question}</legend>{q.options.map((o,j)=><label key={o} style={{display:'block',padding:8}}><input type="radio" name={`resume-q-${i}`} checked={answers[i]===j} onChange={()=>setAnswers({...answers,[i]:j})}/> {o}</label>)}</fieldset>)}
    {!!questions.length&&<button className="btn btn-primary" disabled={!!result||Object.keys(answers).length!==questions.length} onClick={()=>{const value=gradeQuiz(questions,answers);setResult(value);updateProfile({quiz:{...value,date:new Date().toISOString()}});}}>Проверить ответы</button>}
    {result&&<p role="status">Правильно: {result.correct} из {result.total} ({result.score}%). Это короткая проверка знаний, а не оценка грейда. <button className="btn" onClick={()=>{setAnswers({});setResult(null)}}>Пройти заново</button></p>}
  </section>;
}
