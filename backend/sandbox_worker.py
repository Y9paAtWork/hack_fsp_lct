"""Run separately with Docker available: python -m backend.sandbox_worker.

The worker receives code over localhost, then starts a container with no mounts,
network or privileges. It never evaluates submitted code on the host.
"""
import hmac
import json
import os
import select
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PYTHON_RUNNER = r'''
import json,sys
p=json.load(sys.stdin); scope={}; results=[]
try:
 exec(compile(p['code'],'submission.py','exec'),scope)
 fn=scope['most_common']
 for case in p['cases']:
  try: results.append({'ok':True,'value':fn(*case['input'])})
  except BaseException: results.append({'ok':False})
except BaseException: results=[{'ok':False} for case in p['cases']]
print(json.dumps({'results':results}))
'''
JAVASCRIPT_RUNNER = r'''
const fs=require('fs');const p=JSON.parse(fs.readFileSync(0,'utf8'));
try {
 const vm=require('vm'),context={};vm.createContext(context);
 vm.runInContext(p.code,context,{timeout:1000});
 const fn=context.uniqueNumbers;
 const results=p.cases.map(t=>{try{return {ok:true,value:JSON.parse(JSON.stringify(fn(...t.input)))}}catch(e){return {ok:false}}});
 process.stdout.write(JSON.stringify({results}));
} catch(e) {process.stdout.write(JSON.stringify({results:p.cases.map(()=>({ok:false}))}));}
'''
SQL_RUNNER = r'''
import json,sqlite3,sys
p=json.load(sys.stdin);results=[]
for test in p['cases']:
 try:
  c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
  c.execute('CREATE TABLE employees(id INTEGER, dept_id INTEGER, salary REAL)')
  c.executemany('INSERT INTO employees(id,dept_id,salary) VALUES(:id,:dept_id,:salary)',test['data']['employees'])
  c.set_authorizer(lambda action,*args: sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION) else sqlite3.SQLITE_DENY)
  c.set_progress_handler(lambda: 1/0,100000)
  got=[dict(r) for r in c.execute(p['code']).fetchmany(1000)]
  results.append({'ok':True,'value':got})
 except BaseException: results.append({'ok':False})
print(json.dumps({'results':results}))
'''
RUNNERS = {'Python': ('python:3.11-alpine', ['python','-I','-S','-c',PYTHON_RUNNER]),
           'JavaScript': ('node:20-alpine', ['node','-e',JAVASCRIPT_RUNNER]),
           'SQL': ('python:3.11-alpine', ['python','-I','-S','-c',SQL_RUNNER])}
SLOTS = threading.BoundedSemaphore(2)


def docker_call(command, payload):
    """Bound stdout and wall time even if submitted code prints without stopping."""
    with tempfile.TemporaryFile() as source:
        source.write(payload);source.seek(0)
        try:process=subprocess.Popen(command,stdin=source,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        except OSError as exc:raise RuntimeError('Docker sandbox unavailable') from exc
        output=bytearray();deadline=time.monotonic()+8
        try:
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise RuntimeError('Docker sandbox timed out')
                ready,_,_=select.select([process.stdout],[],[],remaining)
                if not ready:raise RuntimeError('Docker sandbox timed out')
                chunk=os.read(process.stdout.fileno(),4097-len(output))
                if not chunk:break
                output.extend(chunk)
                if len(output)>4096:raise RuntimeError('Docker sandbox output limit exceeded')
            process.wait(timeout=max(.01,deadline-time.monotonic()))
            if process.returncode:raise RuntimeError('Docker sandbox failed')
            return bytes(output)
        except (OSError,ValueError,subprocess.TimeoutExpired):
            raise RuntimeError('Docker sandbox unavailable or timed out')
        finally:
            if process.poll() is None:
                process.kill();process.wait()
            process.stdout.close()


def grade(payload):
    technology=payload.get('technology')
    if technology not in RUNNERS: raise ValueError('Unsupported technology')
    code=payload.get('code');tests=payload.get('tests')
    if not isinstance(code,str) or len(code)>12000 or not isinstance(tests,list) or not 1<=len(tests)<=20:
        raise ValueError('Invalid submission')
    image, command=RUNNERS[technology]
    cases=[{'data':test['data']} if technology=='SQL' else {'input':test['input']} for test in tests]
    input_data=json.dumps({'code':code,'cases':cases}).encode()
    if len(input_data)>100000: raise ValueError('Submission too large')
    docker=['docker','run','--rm','-i','--pull=never','--network=none','--read-only',
            '--tmpfs','/tmp:rw,noexec,nosuid,size=8m','--user','65534:65534',
            '--cap-drop=ALL','--security-opt','no-new-privileges','--pids-limit=32',
            '--memory=128m','--memory-swap=128m','--cpus=0.5','--ulimit','nofile=64:64',
            image,*command]
    if not SLOTS.acquire(timeout=1): raise RuntimeError('Docker sandbox is busy')
    try:
        output=docker_call(docker,input_data)
    finally:SLOTS.release()
    response=json.loads(output)
    outputs=response.get('results')
    if not isinstance(outputs,list) or len(outputs)!=len(tests): raise RuntimeError('Invalid sandbox result')
    passed=sum(1 for output,test in zip(outputs,tests) if isinstance(output,dict)
               and output.get('ok') is True and output.get('value')==test['expected'])
    return {'score':passed/len(tests)}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        secret=os.environ.get('FSP_SANDBOX_SECRET','')
        expected='Bearer '+secret
        if not secret or not hmac.compare_digest(self.headers.get('Authorization',''),expected):
            self.send_error(401);return
        if self.path!='/grade': self.send_error(404);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=120000: raise ValueError('Invalid payload size')
            response=grade(json.loads(self.rfile.read(size)))
            body=json.dumps(response).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except (ValueError,RuntimeError,json.JSONDecodeError): self.send_error(503)
    def log_message(self,*args): pass

if __name__=='__main__':
    if not os.environ.get('FSP_SANDBOX_SECRET'): raise SystemExit('Set FSP_SANDBOX_SECRET')
    ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
