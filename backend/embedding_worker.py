import argparse
import json
import os
import time
import uuid
import urllib.request
from pathlib import Path
import numpy as np
from .db import connect, init_db, now

MODEL='jinaai/jina-embeddings-v3'
REVISION='ab036b023d30b4d1138c4c3bfa9f0c445ab455d6'
TASK='retrieval.passage'
DIMENSIONS=1024

class LocalJina:
    def __init__(self,model_path=None,task=TASK):
        # Remote-code modules need a writable cache even when weights are local.
        os.environ.setdefault('HF_HOME',str(Path(__file__).resolve().parents[1]/'.cache'/'huggingface'))
        import torch
        from transformers import AutoModel, AutoTokenizer
        from transformers.dynamic_module_utils import HF_MODULES_CACHE
        import shutil
        torch.set_num_threads(min(8,os.cpu_count() or 1))
        self.device='cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
        self.task=task
        source=model_path or MODEL
        options={'trust_remote_code':True}
        if model_path:
            options.update(local_files_only=True,use_flash_attn=False)
            module_name=Path(model_path).name.replace('-','_hyphen_')
            module_dir=Path(HF_MODULES_CACHE)/'transformers_modules'/module_name
            module_dir.mkdir(parents=True,exist_ok=True)
            for source_file in Path(model_path).glob('*.py'):
                shutil.copy2(source_file,module_dir/source_file.name)
        else:options.update(revision=REVISION,code_revision='bd55a5ec8e6c0fb1d6c26efb4b6a4a74ce8a88d3')
        self.model=AutoModel.from_pretrained(source,**options).to(self.device).eval()
        if model_path:
            self.model.roberta.tokenizer=AutoTokenizer.from_pretrained(model_path,local_files_only=True,fix_mistral_regex=True)
    def encode(self,texts):
        vectors=self.model.encode(texts,task=self.task,batch_size=min(8,len(texts)),max_length=8192,truncate_dim=DIMENSIONS,show_progress_bar=False,convert_to_numpy=False)
        return [vector.detach().float().cpu().tolist() for vector in vectors]

class ApiJina:
    def __init__(self):
        self.key=os.environ.get('JINA_API_KEY','')
        if not self.key:raise RuntimeError('Set JINA_API_KEY on the server or select local mode')
    def encode(self,texts):
        body=json.dumps({'model':'jina-embeddings-v3','task':TASK,'dimensions':DIMENSIONS,'normalized':True,'embedding_type':'float','input':texts}).encode()
        req=urllib.request.Request('https://api.jina.ai/v1/embeddings',data=body,headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=120) as response:result=json.load(response)
        entries=sorted(result['data'],key=lambda x:x['index'])
        if len(entries)!=len(texts):raise RuntimeError('Jina response count does not match request')
        return [entry['embedding'] for entry in entries]

def normalize_vectors(vectors,count):
    vectors=np.asarray(vectors,dtype=np.float32)
    if vectors.shape!=(count,DIMENSIONS) or not np.isfinite(vectors).all():raise ValueError('Invalid embedding shape or values')
    norms=np.linalg.norm(vectors,axis=1,keepdims=True)
    if (norms<=0).any():raise ValueError('Zero vector')
    return vectors/norms

def aggregate_items(c,hashes,revision):
    placeholders=','.join('?' for _ in hashes)
    ids=c.execute(f'SELECT DISTINCT item_id FROM embedding_chunks WHERE content_sha256 IN ({placeholders})',hashes).fetchall()
    for row in ids:
        item_id=row[0]
        chunks=c.execute('SELECT content_sha256 FROM embedding_chunks WHERE item_id=? ORDER BY ordinal',(item_id,)).fetchall()
        vectors=[]
        for chunk in chunks:
            e=c.execute('SELECT vector FROM embeddings WHERE content_sha256=? AND model=? AND revision=? AND task=? AND dimensions=?',(chunk[0],MODEL,revision,TASK,DIMENSIONS)).fetchone()
            if not e:break
            vectors.append(np.frombuffer(e[0],dtype='<f4'))
        if len(vectors)!=len(chunks) or not vectors:continue
        mean=np.mean(vectors,axis=0);norm=np.linalg.norm(mean)
        if not np.isfinite(norm) or norm<=0:continue
        user=c.execute('SELECT user_id FROM work_items WHERE id=?',(item_id,)).fetchone()
        if not user:continue
        c.execute('''INSERT OR REPLACE INTO item_vectors
        (item_id,user_id,model,revision,task,dimensions,vector,chunk_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                  (item_id,user[0],MODEL,revision,TASK,DIMENSIONS,(mean/norm).astype('<f4').tobytes(),len(chunks),now()))

def run_batch(encoder,db=None,batch=16,revision=REVISION,user_id=None):
    user_filter=' AND w.user_id=?' if user_id else ''
    user_params=[user_id] if user_id else []
    with connect(db) as c:
        cached=c.execute('''SELECT DISTINCT ch.content_sha256 FROM embedding_chunks ch JOIN embeddings e
        ON e.content_sha256=ch.content_sha256 AND e.model=? AND e.revision=? AND e.task=? AND e.dimensions=?
        JOIN work_items w ON w.id=ch.item_id
        WHERE ch.status!='ready' '''+user_filter+' LIMIT 500',
        [MODEL,revision,TASK,DIMENSIONS,*user_params]).fetchall()
        hashes=[r[0] for r in cached]
        if hashes:
            c.executemany("UPDATE embedding_chunks SET status='ready',error=NULL,updated_at=? WHERE content_sha256=?",[(now(),h) for h in hashes])
            aggregate_items(c,hashes,revision)
        rows=c.execute('''SELECT ch.content_sha256, MIN(content) content FROM embedding_chunks ch
        JOIN work_items w ON w.id=ch.item_id JOIN users u ON u.id=w.user_id
        WHERE status='pending' AND NOT EXISTS(SELECT 1 FROM embeddings e WHERE e.content_sha256=ch.content_sha256
        AND e.model=? AND e.revision=? AND e.task=? AND e.dimensions=?)'''+user_filter+'''
        GROUP BY ch.content_sha256 ORDER BY MIN(CASE WHEN u.origin='registration' THEN 0 ELSE 1 END), LENGTH(content),ch.content_sha256 LIMIT ?''',
        [MODEL,revision,TASK,DIMENSIONS,*user_params,batch]).fetchall()
    if not rows:return len(hashes)
    try:
        vectors=normalize_vectors(encoder.encode([r['content'] for r in rows]),len(rows))
    except Exception as exc:
        message=f'{type(exc).__name__}: embedding batch failed'
        with connect(db) as c:
            c.executemany("UPDATE embedding_chunks SET status='failed',error=?,updated_at=? WHERE content_sha256=?",[(message,now(),r['content_sha256']) for r in rows])
        raise
    with connect(db) as c:
        for row,vec in zip(rows,vectors):
            h=row['content_sha256']
            c.execute('INSERT OR IGNORE INTO embeddings VALUES(?,?,?,?,?,?,?,?)',(h,MODEL,revision,TASK,DIMENSIONS,1,vec.astype('<f4').tobytes(),now()))
            c.execute("UPDATE embedding_chunks SET status='ready',error=NULL,updated_at=? WHERE content_sha256=?",(now(),h))
        aggregate_items(c,[r['content_sha256'] for r in rows],revision)
    return len(rows)+len(hashes)

def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['local','api'],default='local');p.add_argument('--db');p.add_argument('--batch-size',type=int,default=16);p.add_argument('--watch',action='store_true');p.add_argument('--retry',action='store_true');p.add_argument('--reindex',action='store_true');p.add_argument('--max-batches',type=int)
    a=p.parse_args();init_db(a.db)
    if not 1<=a.batch_size<=128:p.error('batch-size must be 1..128')
    revision=REVISION if a.mode=='local' else 'jina-api-v3-managed'
    with connect(a.db) as c:
        if a.retry:c.execute("UPDATE embedding_chunks SET status='pending',error=NULL WHERE status='failed'")
        if a.reindex:
            c.execute("UPDATE embedding_chunks SET status='pending',error=NULL WHERE NOT EXISTS(SELECT 1 FROM embeddings e WHERE e.content_sha256=embedding_chunks.content_sha256 AND e.model=? AND e.revision=? AND e.task=? AND e.dimensions=?)",(MODEL,revision,TASK,DIMENSIONS))
        if not c.execute("SELECT 1 FROM embedding_chunks WHERE status='pending' LIMIT 1").fetchone() and not a.watch:
            print('No pending chunks');return
    run_id=uuid.uuid4().hex
    with connect(a.db) as c:c.execute('INSERT INTO embedding_runs(id,model,revision,task,dimensions,status,started_at) VALUES(?,?,?,?,?,?,?)',(run_id,MODEL,revision,TASK,DIMENSIONS,'running',now()))
    processed=0
    try:
        encoder=LocalJina() if a.mode=='local' else ApiJina()
        n=0
        while True:
            count=run_batch(encoder,a.db,a.batch_size,revision);processed+=count;n+=1
            with connect(a.db) as c:c.execute('UPDATE embedding_runs SET processed=? WHERE id=?',(processed,run_id))
            print(json.dumps({'processed_unique_chunks':processed,'batch':n}),flush=True)
            if a.max_batches and n>=a.max_batches:break
            if not count:
                if not a.watch:break
                time.sleep(3)
        with connect(a.db) as c:c.execute("UPDATE embedding_runs SET status='complete',finished_at=? WHERE id=?",(now(),run_id))
    except KeyboardInterrupt:
        with connect(a.db) as c:c.execute("UPDATE embedding_runs SET status='failed',error=?,finished_at=? WHERE id=?",('Interrupted; completed vectors retained, resume with the same command',now(),run_id))
        print('Worker stopped; completed vectors retained. Restart to continue.',flush=True)
    except Exception as exc:
        with connect(a.db) as c:c.execute("UPDATE embedding_runs SET status='failed',error=?,finished_at=? WHERE id=?",(f'{type(exc).__name__}: embedding worker failed',now(),run_id))
        raise
if __name__=='__main__':main()
