import React,{useState} from 'react';
import {useAuth} from './AuthContext';
const route=path=>{location.hash=path;};
export default function AuthForm({mode='login',role='candidate'}){
 const auth=useAuth();
 const [email,setEmail]=useState(''),[password,setPassword]=useState(''),[name,setName]=useState(''),[company,setCompany]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const submit=async e=>{e.preventDefault();setBusy(true);setError('');try{
  const user=mode==='login'?await auth.login({email,password}):await auth.register({email,password,name,role,company_name:company});
  route(user.role==='candidate'?(mode==='login'?'/candidate/home':'/candidate/register'):'/employer/home');
 }catch(e){setError(e.message);}finally{setBusy(false);}};
 return <main className="center-page"><button className="text-link" onClick={()=>route('/')}>← На главную</button><div className="panel" style={{padding:32,maxWidth:600,width:'100%'}}><h1>{mode==='login'?'ВХОД':role==='candidate'?'РЕГИСТРАЦИЯ КАНДИДАТА':'РЕГИСТРАЦИЯ РАБОТОДАТЕЛЯ'}</h1><form onSubmit={submit}>
 {mode!=='login'&&<label className="field"><span>Имя</span><input required maxLength={200} autoComplete="name" value={name} onChange={e=>setName(e.target.value)}/></label>}
 {mode!=='login'&&role==='employer'&&<label className="field"><span>Компания</span><input required value={company} maxLength={200} onChange={e=>setCompany(e.target.value)}/></label>}
 <label className="field"><span>Email</span><input required type="email" autoComplete="email" value={email} onChange={e=>setEmail(e.target.value)}/></label>
 <label className="field"><span>Пароль {mode!=='login'&&'(от 8 символов)'}</span><input required type="password" minLength={mode==='login'?1:8} maxLength={128} autoComplete={mode==='login'?'current-password':'new-password'} value={password} onChange={e=>setPassword(e.target.value)}/></label>
 {error&&<p role="alert">{error}</p>}<button className="btn btn-primary" disabled={busy}>{busy?'Подождите…':mode==='login'?'Войти':'Создать аккаунт'}</button></form>
 {mode==='login'?<><p>Нет аккаунта?</p><div className="row-actions"><button className="btn" onClick={()=>route('/candidate/register')}>Я кандидат</button><button className="btn" onClick={()=>route('/employer/register')}>Я работодатель</button></div></>:<p><button className="text-link" onClick={()=>route('/login')}>Уже есть аккаунт — войти</button></p>}
 </div></main>;
}
