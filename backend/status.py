import json
from .db import connect
with connect() as c:
    counts={t:c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ['users','resumes','vacancies','work_items','embedding_chunks','embeddings','item_vectors']}
    counts['chunks_by_status']={r[0]:r[1] for r in c.execute('SELECT status,COUNT(*) FROM embedding_chunks GROUP BY status')}
    counts['items_by_kind']={r[0]:r[1] for r in c.execute('SELECT kind,COUNT(*) FROM work_items GROUP BY kind')}
    counts['needs_review']=c.execute('SELECT COUNT(*) FROM work_items WHERE needs_review=1').fetchone()[0]
print(json.dumps(counts,ensure_ascii=False,indent=2))
