import React,{createContext,useContext,useEffect,useState} from 'react';
import {useAuth} from './AuthContext';

const DemoContext=createContext(null);
export function DemoProvider({children}){
  const {user}=useAuth();
  const storageKey='fsp-demo-threads:'+ (user?.id||'guest');
  const [threads,setThreads]=useState([]);
  useEffect(()=>{try{const value=JSON.parse(localStorage.getItem(storageKey)||'[]');setThreads(Array.isArray(value)?value:[]);}catch{setThreads([])}},[storageKey]);
  const changeStatus=(id,status)=>setThreads(list=>list.map(t=>t.id===id?{...t,status}:t));
  const sendMessage=(id,from,text)=>{const clean=text.trim();if(!clean)return;setThreads(list=>list.map(t=>t.id===id?{...t,status:t.status==='Отклонено'?t.status:'В диалоге',messages:[...t.messages,{id:Date.now(),from,text:clean,time:new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}]}:t))};
  const sendInvite=({person,vacancy,title,salary,about})=>{const id=`thread-${Date.now()}`;setThreads(list=>[{id,kind:'invitation',company:vacancy.company,candidateName:person.name,role:title||vacancy.title,salary:salary||vacancy.salary,specialty:`${vacancy.specialization} · ${vacancy.grade}`,stack:vacancy.stack.join(', '),status:'Новое',time:'только что',messages:about.trim()?[{id:Date.now()+1,from:'employer',text:about.trim(),time:new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}]:[]},...list]);return id};
  return <DemoContext.Provider value={{threads,changeStatus,sendMessage,sendInvite}}>{children}</DemoContext.Provider>
}
export const useDemo=()=>useContext(DemoContext);
