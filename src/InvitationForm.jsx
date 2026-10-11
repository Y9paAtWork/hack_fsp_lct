import React,{useEffect,useState} from 'react';
import {api} from './api';

export default function InvitationForm({matchId,vacancyId,title='',description='',salaryMin='',salaryMax='',deliverable=true,onSent,onCancel}){
 const [form,setForm]=useState({company_name:'',title,description,salary_min:salaryMin,salary_max:salaryMax,contact_method:'Чат на платформе'});
 const [error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let live=true;api('/employer/profile').then(r=>{if(live)setForm(old=>({...old,company_name:r.company_name||''}))}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[]);
 const change=(key,value)=>setForm(old=>({...old,[key]:value}));
 const submit=async e=>{e.preventDefault();setBusy(true);setError('');try{
  const payload={...form,match_id:matchId,salary_min:Number(form.salary_min),salary_max:Number(form.salary_max)};
  const result=await api(`/vacancies/${encodeURIComponent(vacancyId)}/invite`,{method:'POST',body:payload});
  onSent?.(result);
 }catch(e){setError(e.message)}finally{setBusy(false)}};
 return <form className="panel invitation-form" onSubmit={submit}><h3>УСЛОВИЯ ПРЕДЛОЖЕНИЯ</h3><p className="muted">{deliverable?'Кандидат увидит условия и зарплату до открытия чата.':'Это демонстрационное резюме без аккаунта. Предложение сохранится в вашем кабинете, но доставить его кандидату пока нельзя.'}</p>
  <label className="field"><span>Компания</span><input required maxLength={200} value={form.company_name} onChange={e=>change('company_name',e.target.value)}/></label>
  <label className="field"><span>Название предложения</span><input required minLength={3} maxLength={200} value={form.title} onChange={e=>change('title',e.target.value)}/></label>
  <label className="field"><span>Описание и условия</span><textarea required minLength={20} maxLength={20000} value={form.description} onChange={e=>change('description',e.target.value)}/></label>
  <div className="form-grid"><label className="field"><span>Зарплата от, ₽/мес</span><input type="number" required min="1000" max="10000000" value={form.salary_min} onChange={e=>change('salary_min',e.target.value)}/></label><label className="field"><span>Зарплата до, ₽/мес</span><input type="number" required min={form.salary_min||'1000'} max="10000000" value={form.salary_max} onChange={e=>change('salary_max',e.target.value)}/></label></div>
  <label className="field"><span>Способ связи</span><input required minLength={3} maxLength={300} value={form.contact_method} onChange={e=>change('contact_method',e.target.value)}/></label>
  {error&&<p role="alert">{error}</p>}<div className="row-actions"><button className="btn btn-primary" disabled={busy||!form.company_name}>{busy?'Отправляем…':'Отправить предложение'}</button><button type="button" className="btn" onClick={onCancel}>Отмена</button></div>
 </form>;
}
