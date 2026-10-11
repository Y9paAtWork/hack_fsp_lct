"""Dictionary heuristics for technologies mentioned in vacancy descriptions."""

import csv
import re
from functools import lru_cache
from pathlib import Path


ALIASES = {
    'Python': ('питон',),
    'PostgreSQL': ('postgres', 'постгрес'),
    'Kubernetes': ('k8s', 'кубернетес'),
    'Go': ('golang',),
    'JavaScript': ('javascript', 'JS'),
    'TypeScript': ('typescript', 'TS'),
    'Node.js': ('nodejs', 'node js'),
    'Vue.js': ('vuejs', 'vue'),
    'Next.js': ('nextjs',),
    'React': ('reactjs', 'react.js'),
    'scikit-learn': ('sklearn', 'scikit learn'),
    'MS SQL Server': ('mssql', 'microsoft sql server'),
    '1C': ('1С', '1С8'),
    'CryptoPro': ('КриптоПро',),
    'VipNet': ('ВипНет',),
    'Kaspersky Security Center': ('KSC',),
    'Generative AI / LLM': ('LLM', 'large language model', 'большая языковая модель',
                            'большие языковые модели', 'генеративный ИИ', 'prompt engineering',
                            'prompt-engineering', 'промпт-инжиниринг'),
    'Search / Retrieval': ('RAG', 'FAISS', 'Pinecone', 'векторный поиск', 'vector search'),
    'Machine Learning': ('машинное обучение', 'ML'),
    'Deep Learning': ('глубокое обучение', 'DL'),
    'Computer Vision': ('компьютерное зрение',),
    'Time Series': ('временные ряды',),
    'NLP': ('обработка естественного языка', 'natural language processing'),
    'Audio / Speech': ('распознавание речи', 'ASR'),
    'REST API': ('RESTful API',),
}

AMBIGUOUS = {'Go', 'R', 'C'}
EXPLICIT_LANGUAGE = re.compile(r'(?:язык|на|стек|знани[ея]|опыт\s+с|работ[аы]\s+с|разработк[а-я]*\s+на)\s*$', re.I)
DEVELOPER_AFTER = re.compile(r'^\s*(?:developer|разработчик|программист)', re.I)
NEGATED_AFTER = re.compile(r'^\s+не\s+(?:требуется|обязателен|нужен|нужна)', re.I)


@lru_cache(maxsize=1)
def patterns():
    with Path(__file__).with_name('technology_stack_FINAL.csv').open(encoding='utf-8-sig', newline='') as file:
        names = [row['technology'].strip() for row in csv.DictReader(file)]
    result = []
    for order, name in enumerate(names):
        for alias in dict.fromkeys((name, *ALIASES.get(name, ()))):
            body = re.escape(alias).replace(r'\ ', r'[\s\u00a0]+')
            pattern = re.compile(r'(?<![\w+#])' + body + r'(?![\w+#])', re.I)
            result.append((order, name, alias, pattern))
    return result


def extract_evidence(description):
    if not isinstance(description, str) or not description.strip():
        return []
    matches = []
    for order, name, alias, pattern in patterns():
        for hit in pattern.finditer(description):
            start, end = hit.span()
            if NEGATED_AFTER.match(description[end:end + 35]):
                continue
            if name in AMBIGUOUS and alias.casefold() == name.casefold():
                # A bare one-letter language or English word "Go" needs a tech context.
                if hit.group() != name or description[end:end + 1] == '-':
                    continue
                before = description[max(0, start - 45):start]
                after = description[end:end + 25]
                if not (EXPLICIT_LANGUAGE.search(before) or DEVELOPER_AFTER.match(after)):
                    continue
            matches.append((start, end, order, name, hit.group()))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2]))
    accepted = []
    last_end = -1
    seen = set()
    for start, end, _, name, matched in matches:
        if start < last_end:
            continue
        last_end = end
        if name not in seen:
            accepted.append({'technology': name, 'matched_text': matched, 'start': start})
            seen.add(name)
    return accepted


def extract_technologies(description):
    return [item['technology'] for item in extract_evidence(description)]
