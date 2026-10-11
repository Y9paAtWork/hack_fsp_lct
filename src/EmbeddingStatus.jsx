import React,{useEffect,useState} from 'react';
import {api} from './api';
export default function EmbeddingStatus(){
 const [items,setItems]=useState(null),[error,setError]=useState('');
 useEffect(()=>{let live=true;const read=()=>api('/profile/items').then(r=>{if(live){setItems(r.items);setError('');}}).catch(e=>{if(live)setError(e.message);});read();const timer=setInterval(read,5000);return()=>{live=false;clearInterval(timer);};},[]);
 return <section className="panel" style={{padding:24,marginTop:24}}><h3>ВЕКТОРИЗАЦИЯ ОПЫТА</h3>{error&&<p role="alert">{error}</p>}{items&&<><p>Фрагментов: {items.length}. Готовых векторов: {items.filter(i=>i.vector_ready).length}.</p>{items.some(i=>!i.vector_ready)&&<p className="muted">Обработка ожидает Jina worker. Данные профиля уже сохранены в БД.</p>}</>}</section>;
}
