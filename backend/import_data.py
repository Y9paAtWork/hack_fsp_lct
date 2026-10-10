import argparse
import csv
import hashlib
import json
import uuid
from pathlib import Path
from .db import connect, init_db, now, ROOT
from .splitting import split_resume, insert_item

csv.field_size_limit(20_000_000)

def number(value):
    try:
        x = float(value)
        return x if __import__('math').isfinite(x) else None
    except (ValueError, TypeError):
        return None

def array(value):
    try:
        result = json.loads(value or '[]')
        return result if isinstance(result,list) else []
    except (ValueError,TypeError):
        return []

def dump(value):
    return json.dumps(value,ensure_ascii=False)

def technologies(c, table, field, entity_id, values):
    for name in set(v for v in values if isinstance(v,str) and v.strip()):
        c.execute('INSERT OR IGNORE INTO technologies(name) VALUES(?)',(name,))
        c.execute(f'INSERT OR IGNORE INTO {table}({field},technology) VALUES(?,?)',(entity_id,name))

def seed_catalog(c):
    with open(ROOT/'technology_stack_FINAL.csv',encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            c.execute('INSERT INTO technologies(name,categories_json,test_required) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET categories_json=excluded.categories_json,test_required=excluded.test_required',
                      (r['technology'],dump(r['category'].split('; ')),int(r['test_required'])))

RESUME_FIELDS=['specialization','position_name','typical_position','age','location','experience','salary_min','salary_max','schedule_type','business_trip','relocation','retraining_capability','skills','hard_skills','soft_skills','education_level','education_institutions','education_details','additional_education','languages','certificates','achievements','additional_information','ner_technologies','all_technologies','technology_count']
VACANCY_FIELDS=['specialization','vacancy_name','typical_position','professional_sphere','region','experience_requirements','schedule_type','salary_min','salary_max','skills','hard_skills','soft_skills','position_requirements','responsibilities','additional_requirements','description','ner_technologies','all_technologies','technology_count']
NUMERIC={'age','experience','salary_min','salary_max','experience_requirements','technology_count'}

def values(row,fields):
    return [number(row.get(k)) if k in NUMERIC else dump(array(row.get(k))) if k in ('ner_technologies','all_technologies') else row.get(k,'') for k in fields]

def column(k):
    return {'experience':'experience_years','skills':'skills_text','ner_technologies':'ner_technologies_json','all_technologies':'all_technologies_json'}.get(k,k)

def import_file(path,kind,db=None):
    path=Path(path)
    with path.open('rb') as f:
        sha=hashlib.file_digest(f,'sha256').hexdigest()
    run_id=f'{kind}:{sha}'
    init_db(db)
    with connect(db) as c:
        seed_catalog(c)
        old=c.execute('SELECT status,row_count FROM import_runs WHERE id=?',(run_id,)).fetchone()
        if old and old['status']=='complete':return {'kind':kind,'rows':old['row_count'],'already_imported':True}
        c.execute('INSERT INTO import_runs(id,filename,sha256,kind,status,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status',(run_id,path.name,sha,kind,'running',now()))
    count=0
    try:
        with path.open(encoding='utf-8-sig',newline='') as f, connect(db) as c:
            for index,row in enumerate(csv.DictReader(f),1):
                timestamp=now()
                identity=uuid.uuid5(uuid.NAMESPACE_URL,f'fsp:{kind}:{sha}:{index}').hex
                specs=dump([v.strip() for v in row.get('it_specializations','').split(';') if v.strip()])
                if kind=='resumes':
                    user_id='dataset:'+identity
                    c.execute('INSERT OR IGNORE INTO users(id,role,name,origin,created_at) VALUES(?,?,?,?,?)',(user_id,'candidate',f'Кандидат {index}','dataset',timestamp))
                    cols=['id','user_id','import_id','source_row','specializations_json']+[column(k) for k in RESUME_FIELDS]+['raw_json','created_at','updated_at']
                    vals=[identity,user_id,run_id,index,specs]+values(row,RESUME_FIELDS)+[dump(row),timestamp,timestamp]
                    c.execute(f"INSERT OR IGNORE INTO resumes({','.join(cols)}) VALUES({','.join('?' for _ in vals)})",vals)
                    for n,item in enumerate(split_resume(row)):
                        insert_item(c,f'{identity}:item:{n}',identity,user_id,n,item,timestamp,row.get('specialization',''))
                    technologies(c,'resume_technologies','resume_id',identity,array(row.get('all_technologies')))
                else:
                    cols=['id','import_id','source_row','specializations_json']+[column(k) for k in VACANCY_FIELDS]+['raw_json','created_at']
                    vals=[identity,run_id,index,specs]+values(row,VACANCY_FIELDS)+[dump(row),timestamp]
                    c.execute(f"INSERT OR IGNORE INTO vacancies({','.join(cols)}) VALUES({','.join('?' for _ in vals)})",vals)
                    technologies(c,'vacancy_technologies','vacancy_id',identity,array(row.get('all_technologies')))
                count=index
                if index%1000==0:
                    c.execute('UPDATE import_runs SET row_count=? WHERE id=?',(index,run_id));c.commit()
            c.execute("UPDATE import_runs SET status='complete',row_count=? WHERE id=?",(count,run_id))
    except Exception:
        with connect(db) as c:c.execute("UPDATE import_runs SET status='failed',row_count=? WHERE id=?",(count,run_id))
        raise
    return {'kind':kind,'rows':count,'already_imported':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--resumes',required=True);p.add_argument('--vacancies',required=True);p.add_argument('--db')
    a=p.parse_args()
    for path,kind in [(a.resumes,'resumes'),(a.vacancies,'vacancies')]:print(dump(import_file(path,kind,a.db)),flush=True)
