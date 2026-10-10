import React,{useState,useId} from 'react';
import {suggestTechnologies,resolveTechnology} from './ner';
export default function TechnologyInput({selected,category,onAdd}){
 const [value,setValue]=useState(''),[open,setOpen]=useState(false),[index,setIndex]=useState(-1);
 const id=useId(),items=suggestTechnologies(value,selected,category);
 const add=v=>{const name=resolveTechnology(v);if(name)onAdd(name);setValue('');setOpen(false);setIndex(-1);};
 return <div className="technology-picker"><div className="tech-entry"><input role="combobox" aria-label="Название технологии" aria-autocomplete="list" aria-expanded={open&&items.length>0} aria-controls={id} aria-activedescendant={open&&index>=0?`${id}-${index}`:undefined} placeholder="Начните вводить: Python, React…" value={value} onFocus={()=>setOpen(true)} onBlur={()=>setOpen(false)} onChange={e=>{setValue(e.target.value);setOpen(true);setIndex(-1)}} onKeyDown={e=>{
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setOpen(true);setIndex(i=>items.length?(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length:-1);}
  if(e.key==='Escape'){setOpen(false);setIndex(-1);}
  if(e.key==='Enter'){e.preventDefault();add(open&&index>=0&&items[index]?items[index].technology:value);}
 }}/><button type="button" className="btn btn-primary" disabled={!value.trim()} onClick={()=>add(value)}>Добавить технологию</button></div>
 {open&&items.length>0&&<ul id={id} role="listbox" className="technology-options">{items.map((item,i)=><li key={item.technology} id={`${id}-${i}`} role="option" aria-selected={i===index} className={i===index?'active':''} onMouseDown={e=>{e.preventDefault();add(item.technology);}}><strong>{item.technology}</strong><small>{item.categories.join(' · ')}</small></li>)}</ul>}
 {open&&value.trim()&&!items.length&&<p className="muted">Новых совпадений в справочнике нет. Можно добавить свой навык кнопкой.</p>}
 </div>;
}
