"""Live Docker acceptance checks. Run after starting Docker and pulling worker images."""
import subprocess
import time

from .sandbox_worker import grade


def main():
    status=subprocess.run(['docker','info','--format','{{.ServerVersion}}'],capture_output=True,text=True)
    if status.returncode:
        raise SystemExit('Docker daemon is unavailable; start Docker Desktop first')
    cases=[
        {'technology':'Python','code':'def most_common(nums):\n    return min(set(nums),key=lambda n:(-nums.count(n),n)) if nums else None',
         'tests':[{'input':[[3,1,3,2,1]],'expected':1},{'input':[[]],'expected':None}]},
        {'technology':'JavaScript','code':'function uniqueNumbers(arr) { return [...new Set(arr)]; }',
         'tests':[{'input':[[2,1,2,3,1]],'expected':[2,1,3]},{'input':[[]],'expected':[]}]},
        {'technology':'SQL','code':'SELECT dept_id, AVG(salary) AS avg_salary FROM employees GROUP BY dept_id HAVING COUNT(*)>=2 ORDER BY dept_id',
         'tests':[{'data':{'employees':[{'id':1,'dept_id':10,'salary':100},{'id':2,'dept_id':10,'salary':200}]},
                   'expected':[{'dept_id':10,'avg_salary':150}]},
                  {'data':{'employees':[{'id':1,'dept_id':10,'salary':100}]},'expected':[]}]},
    ]
    for case in cases:
        result=grade(case)
        if result['score']!=1:raise AssertionError((case['technology'],result))
        print(case['technology'],'passed')
    timeout_case={'technology':'Python','code':'def most_common(nums):\n    while True: pass',
                  'tests':[{'input':[[1]],'expected':1}]}
    started=time.monotonic()
    try:grade(timeout_case)
    except RuntimeError:pass
    else:raise AssertionError('Infinite loop unexpectedly received a score')
    if time.monotonic()-started>12:raise AssertionError('Timeout limit was exceeded')
    print('timeout passed')
    memory_case={'technology':'Python','code':'def most_common(nums):\n    return bytearray(256*1024*1024)',
                 'tests':[{'input':[[1]],'expected':1}]}
    try:
        memory_result=grade(memory_case)
        if memory_result['score']!=0:raise AssertionError('Memory limit unexpectedly received a score')
    except RuntimeError:pass
    print('memory limit passed')

if __name__=='__main__':main()
