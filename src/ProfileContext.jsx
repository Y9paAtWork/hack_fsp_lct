import React,{createContext,useContext,useState,useEffect,useRef,useCallback} from 'react';
import {emptyProfile,sanitizeProfile,profileSkills} from './profileStore';
import {useAuth} from './AuthContext';
import {api} from './api';
const Context=createContext(null);
export function ProfileProvider({children}){
 const {user}=useAuth();
 const [profile,setProfile]=useState(emptyProfile),[error,setError]=useState(''),[loading,setLoading]=useState(false),[saving,setSaving]=useState(false),[resetVersion,setResetVersion]=useState(0),[loadedUserId,setLoadedUserId]=useState(null),[loadFailed,setLoadFailed]=useState(false);
 const latest=useRef(profile),revision=useRef(0),dirty=useRef(false),generation=useRef(0),epoch=useRef(0),chain=useRef(Promise.resolve());
 useEffect(()=>{
  const id=++epoch.current;dirty.current=false;setError('');setLoadFailed(false);setLoadedUserId(null);setProfile(emptyProfile());latest.current=emptyProfile();
  if(user?.role!=='candidate'){setLoading(false);return;}
  setLoading(true);
  api('/profile').then(r=>{if(id!==epoch.current)return;latest.current=sanitizeProfile(r.profile);revision.current=r.revision;setProfile(latest.current);}).catch(e=>{if(id===epoch.current){setError(e.message);setLoadFailed(true);}}).finally(()=>{if(id===epoch.current){setLoadedUserId(user.id);setLoading(false);}});
 },[user?.id]);
 const updateProfile=useCallback(patch=>{const next=sanitizeProfile({...latest.current,...patch});latest.current=next;dirty.current=true;generation.current++;setProfile(next);},[]);
 const flush=useCallback(()=>{
  const id=epoch.current;
  const work=async()=>{
   if(!dirty.current||user?.role!=='candidate'||id!==epoch.current)return;
   const gen=generation.current;setSaving(true);
   try{const r=await api('/profile',{method:'PUT',body:{profile:latest.current,revision:revision.current}});if(id!==epoch.current)return;revision.current=r.revision;if(gen===generation.current){latest.current=sanitizeProfile(r.profile);setProfile(latest.current);dirty.current=false;}setError('');}
   catch(e){if(id===epoch.current)setError(e.message);throw e;}
   finally{if(id===epoch.current)setSaving(false);}
  };
  const next=chain.current.catch(()=>{}).then(work);chain.current=next;return next;
 },[user?.id]);
 useEffect(()=>{if(!loading&&dirty.current&&user?.role==='candidate'){const t=setTimeout(()=>flush().catch(()=>{}),500);return()=>clearTimeout(t);}},[profile,loading,flush]);
 const clearProfile=async()=>{await flush();const r=await api('/profile/reset',{method:'POST',body:{revision:revision.current}});generation.current++;dirty.current=false;revision.current=r.revision;latest.current=sanitizeProfile(r.profile);setProfile(latest.current);setResetVersion(v=>v+1);setError('');};
 return <Context.Provider value={{profile,updateProfile,clearProfile,flush,error,loading:loading||(user?.role==='candidate'&&loadedUserId!==user.id),loadFailed,saving:saving||dirty.current,resetVersion}}>{children}</Context.Provider>;
}
export const useProfile=()=>useContext(Context);
export function useCandidate(){
 const {profile}=useProfile();
 return {name:profile.name||'Кандидат',firstName:profile.name.split(' ')[0]||'Кандидат',role:profile.selected.join(' / ')||'Специализация не выбрана',stack:profileSkills(profile),completion:Math.round(([!!profile.name,!!profile.email,!!profile.selected.length,!!profileSkills(profile).length,!!Object.values(profile.jobs).flat().length].filter(Boolean).length/5)*100),experience:profile.selected.flatMap(s=>(profile.jobs[s]||[]).map(j=>({...j,period:`${j.start}–${j.end}`,role:j.role||s}))),achievements:[]};
}
