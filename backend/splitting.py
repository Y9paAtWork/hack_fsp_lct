import re
import hashlib

HEADER = re.compile(r'(?P<title>[^\n|]{1,180}?)\s+@\s+(?P<company>[^\[\n]{1,250})\s*\[(?P<start>[^\]\n]{4,60}?)\s+[—–-]\s+(?P<end>[^\]\n]{2,60})\]\s*')
PROJECT = re.compile(r'(?im)(?:^|\n)\s*(?:(?:личные|учебные|собственные|pet)[ -]*)?(?:проекты?|projects?|pet[ -]projects?)\s*[:—-]\s*')

def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def chunks(text, limit=2200):
    text = text.strip()
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            space = max(text.rfind('\n', start + limit // 2, end), text.rfind(' ', start + limit // 2, end))
            if space > start:
                end = space
        yield text[start:end].strip()
        start = end
        while start < len(text) and text[start].isspace():
            start += 1

def split_projects(text, source_field):
    matches = list(PROJECT.finditer(text))
    result = []
    for i, m in enumerate(matches):
        block = text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
        if block:
            title, _, description = block.partition('\n')
            result.append({'kind': 'project', 'title': title[:180], 'company': '', 'start_date': '', 'end_date': '',
                           'description': description or block, 'source_text': text[m.start():m.end()] + block,
                           'source_field': source_field, 'split_method': 'explicit_project_heading', 'needs_review': 1})
    return result

def split_resume(row):
    text = row.get('work_experience_text', '').strip()
    result = []
    if text:
        blocks = re.split(r'\s*\|\|\s*', text)
        for block in blocks:
            matches = list(HEADER.finditer(block))
            if matches:
                prefix = block[:matches[0].start()].strip()
                if prefix:
                    result.append({'kind':'unclassified','title':'','company':'','start_date':'','end_date':'',
                                   'description':prefix,'source_text':prefix,'source_field':'work_experience_text',
                                   'split_method':'unparsed_prefix','needs_review':1})
                for i, m in enumerate(matches):
                    raw = block[m.start():matches[i + 1].start() if i + 1 < len(matches) else len(block)].strip()
                    desc = block[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(block)].strip()
                    result.append({'kind':'work', 'title':m['title'].strip(), 'company':m['company'].strip(),
                                   'start_date':m['start'].strip(),'end_date':m['end'].strip(),
                                   'description':desc,'source_text':raw,'source_field':'work_experience_text',
                                   'split_method':'structured_header','needs_review':int(len(matches)>1)})
                    result.extend(split_projects(desc, 'work_experience_text'))
            elif block.strip():
                result.append({'kind':'work','title':'','company':'','start_date':'','end_date':'',
                               'description':block.strip(),'source_text':block.strip(),'source_field':'work_experience_text',
                               'split_method':'delimiter_only','needs_review':1})
    else:
        desc = row.get('work_experience_descriptions', '').strip()
        if desc:
            for block in re.split(r'\s*\|\|\s*', desc):
                if block.strip():
                    result.append({'kind':'work','title':'','company':'','start_date':'','end_date':'',
                                   'description':block.strip(),'source_text':block.strip(),'source_field':'work_experience_descriptions',
                                   'split_method':'description_fallback','needs_review':1})
        elif row.get('work_experience_titles', '').strip() or row.get('work_experience_companies','').strip():
            raw = '\n'.join(row.get(k,'') for k in ('work_experience_titles','work_experience_companies'))
            result.append({'kind':'unclassified','title':'','company':'','start_date':'','end_date':'',
                           'description':raw,'source_text':raw,'source_field':'work_experience_titles+companies',
                           'split_method':'unaligned_lists','needs_review':1})
    result.extend(split_projects(row.get('additional_information',''), 'additional_information'))
    return result

def embedding_text(item):
    return '\n'.join(str(item.get(k) or '').strip() for k in ('title','company','start_date','end_date','description') if item.get(k)).strip()

def insert_item(c, item_id, resume_id, user_id, ordinal, item, timestamp, specialization=''):
    c.execute('''INSERT OR IGNORE INTO work_items
    (id,resume_id,user_id,kind,ordinal,specialization,title,company,start_date,end_date,description,source_field,source_text,split_method,needs_review,created_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
    (item_id,resume_id,user_id,item['kind'],ordinal,specialization,item.get('title',''),item.get('company',''),
     item.get('start_date',''),item.get('end_date',''),item.get('description',''),item['source_field'],
     item['source_text'],item['split_method'],int(item['needs_review']),timestamp))
    for i, text in enumerate(chunks(embedding_text(item))):
        c.execute('INSERT OR IGNORE INTO embedding_chunks(id,item_id,ordinal,content,content_sha256,updated_at) VALUES(?,?,?,?,?,?)',
                  (f'{item_id}:chunk:{i}',item_id,i,text,digest(text),timestamp))
