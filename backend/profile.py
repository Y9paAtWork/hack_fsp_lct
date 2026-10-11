import json
import csv
import uuid
from functools import lru_cache
from pathlib import Path
from .db import now
from .splitting import insert_item
from .import_data import dump, technologies

@lru_cache(maxsize=1)
def allowed_technologies():
    with (Path(__file__).with_name('technology_stack_FINAL.csv')).open(encoding='utf-8-sig',newline='') as source:
        return {row['technology'] for row in csv.DictReader(source)}

def empty_profile(name='',email=''):
    return {'version':1,'name':name,'email':email,'fsp':'','terms':True,'step':1,'selected':[],'active':'','stacks':{},'jobs':{},'ownProjects':{},'quiz':None}

def sanitize(value,name,email):
    p=empty_profile(name,email)
    for key in ['name','fsp','active']:
        if isinstance(value.get(key),str):p[key]=value[key][:200]
    p['step']=value.get('step') if type(value.get('step')) is int and 1<=value['step']<=4 else 1
    p['selected']=list(dict.fromkeys(s[:100] for s in value.get('selected',[]) if isinstance(s,str)))[:20] if isinstance(value.get('selected'),list) else []
    p['active']=p['active'] if p['active'] in p['selected'] else (p['selected'][0] if p['selected'] else '')
    if p['step']>2 and not p['selected']:p['step']=2
    p['terms']=value.get('terms') is True
    for key,fields in [('jobs',['company','role','start','end','description','source']),('ownProjects',['name','description','tasks'])]:
        source=value.get(key,{})
        if not isinstance(source,dict):continue
        for spec,rows in source.items():
            if spec not in p['selected'] or not isinstance(rows,list):continue
            p[key][spec]=[{k:(r.get(k,'')[:20000] if isinstance(r.get(k,''),str) else '') for k in fields} for r in rows[:100] if isinstance(r,dict)]
    source=value.get('stacks',{})
    if isinstance(source,dict):
        allowed=allowed_technologies()
        p['stacks']={spec:list(dict.fromkeys(s for s in source.get(spec,[]) if isinstance(s,str) and s in allowed))[:202] for spec in p['selected'] if isinstance(source.get(spec,[]),list)}
    return p

def sync_resume(c,user,profile):
    uid=user['id'];rid=f'registered:{uid}';timestamp=now()
    c.execute('''INSERT INTO resumes(id,user_id,specialization,specializations_json,position_name,skills_text,all_technologies_json,technology_count,raw_json,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET specialization=excluded.specialization,
    specializations_json=excluded.specializations_json,position_name=excluded.position_name,skills_text=excluded.skills_text,
    all_technologies_json=excluded.all_technologies_json,technology_count=excluded.technology_count,raw_json=excluded.raw_json,updated_at=excluded.updated_at''',
    (rid,uid,profile['active'],dump(profile['selected']),profile['active'], '; '.join(s for v in profile['stacks'].values() for s in v),dump(list(dict.fromkeys(s for v in profile['stacks'].values() for s in v))),len(set(s for v in profile['stacks'].values() for s in v)),dump(profile),timestamp,timestamp))
    wanted=set();ordinal=0
    for spec in profile['selected']:
        for kind,key in [('work','jobs'),('project','ownProjects')]:
            for entry in profile[key].get(spec,[]):
                item={'kind':kind,'title':entry.get('role','') if kind=='work' else entry.get('name',''),'company':entry.get('company',''),
                      'start_date':entry.get('start',''),'end_date':entry.get('end',''),
                      'description':'\n'.join(v for v in [entry.get('description',''),entry.get('tasks','')] if v),
                      'source_text':entry.get('source') or dump(entry),'source_field':'registration','split_method':'user_reviewed','needs_review':0}
                if not any(item.get(k) for k in ['title','company','description','start_date','end_date']):continue
                item_id=uuid.uuid5(uuid.NAMESPACE_URL,uid+spec+str(ordinal)+dump(item)).hex
                wanted.add(item_id);insert_item(c,item_id,rid,uid,ordinal,item,timestamp,spec);ordinal+=1
    for row in c.execute('SELECT id FROM work_items WHERE user_id=?',(uid,)).fetchall():
        if row[0] not in wanted:c.execute('DELETE FROM work_items WHERE id=?',(row[0],))
    c.execute('DELETE FROM resume_technologies WHERE resume_id=?',(rid,))
    technologies(c,'resume_technologies','resume_id',rid,[s for v in profile['stacks'].values() for s in v])
