import React,{createContext,useContext,useEffect,useState} from 'react';
import {api} from './api';
const Context=createContext(null);
export function AuthProvider({children}){
 const [user,setUser]=useState(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
 useEffect(()=>{api('/auth/me').then(r=>{setUser(r.user);setError('');}).catch(e=>{if(e.status!==401)setError(e.message);}).finally(()=>setLoading(false));},[]);
 const login=async body=>{const r=await api('/auth/login',{method:'POST',body});setError('');setUser(r.user);return r.user;};
 const register=async body=>{const r=await api('/auth/register',{method:'POST',body});setError('');setUser(r.user);return r.user;};
 const logout=async()=>{await api('/auth/logout',{method:'POST'});setUser(null);};
 return <Context.Provider value={{user,loading,error,login,register,logout}}>{children}</Context.Provider>;
}
export const useAuth=()=>useContext(Context);
