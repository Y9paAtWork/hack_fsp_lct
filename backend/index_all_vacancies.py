"""Build resumable Jina query vectors for every vacancy in the existing SQLite DB.

Run from the site directory: python3 -m backend.index_all_vacancies
"""

import argparse
import hashlib
import time
from pathlib import Path

from .db import ROOT, connect, db_path, init_db
from .embedding_worker import DIMENSIONS, MODEL, REVISION, LocalJina, normalize_vectors
from .ranking import store_vacancy_query_vector, vacancy_experience_text

TASK = 'retrieval.query'
SCAN_BATCH = 256


def _load_encoder(model_path):
    source = Path(model_path).expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f'Локальная модель не найдена: {source}')
    metadata = source / '.cache' / 'huggingface' / 'download' / 'model.safetensors.metadata'
    if not metadata.is_file() or metadata.read_text().splitlines()[0] != REVISION:
        raise ValueError('Ревизия локальной Jina не совпадает с векторами опыта в БД')
    return LocalJina(model_path=str(source), task=TASK)


def index_all(db=None, model_path=None, batch_size=8, limit=None, dry_run=False,
              encoder=None, progress=print):
    """Store each batch atomically; a repeated run skips vectors with matching text hash."""
    if not 1 <= batch_size <= 128:
        raise ValueError('batch_size должен быть от 1 до 128')
    if limit is not None and limit < 1:
        raise ValueError('limit должен быть положительным')
    path = Path(db or db_path()).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f'База данных не найдена: {path}')
    if not dry_run:
        init_db(path)

    stats = {'scanned': 0, 'current': 0, 'pending': 0, 'written': 0,
             'changed_during_run': 0, 'title_only': 0, 'empty': 0}
    pending = []
    last_id = ''
    batches = 0
    started = time.monotonic()

    def write_batch():
        nonlocal encoder, batches
        if not pending or dry_run:
            pending.clear()
            return
        if encoder is None:
            encoder = _load_encoder(model_path or ROOT.parent / 'models' / 'jina-embeddings-v3')
        vectors = normalize_vectors(encoder.encode([text for _, text, _ in pending]), len(pending))
        with connect(path) as c:
            for (vacancy_id, text, digest), vector in zip(pending, vectors):
                latest = c.execute(
                    'SELECT vacancy_name,responsibilities,description FROM vacancies WHERE id=?',
                    (vacancy_id,)).fetchone()
                if latest is None or hashlib.sha256(
                        vacancy_experience_text(latest).encode()).hexdigest() != digest:
                    stats['changed_during_run'] += 1
                    continue
                store_vacancy_query_vector(c, vacancy_id, vector)
                stats['written'] += 1
        pending.clear()
        batches += 1
        if batches % 25 == 0:
            progress(f"Сохранено {stats['written']} новых векторов; "
                     f"просмотрено {stats['scanned']} вакансий; {time.monotonic()-started:.0f} с")

    stop = False
    while not stop:
        with connect(path) as c:
            rows = c.execute('''SELECT v.id,v.vacancy_name,v.responsibilities,v.description,
                 q.model,q.revision,q.task,q.dimensions,q.text_sha256
                 FROM vacancies v LEFT JOIN vacancy_query_vectors q ON q.vacancy_id=v.id
                 WHERE v.id>? ORDER BY v.id LIMIT ?''', (last_id, SCAN_BATCH)).fetchall()
        if not rows:
            break
        for row in rows:
            last_id = row['id']
            stats['scanned'] += 1
            text = vacancy_experience_text(row)
            if not text:
                stats['empty'] += 1
                continue
            if text == row['vacancy_name'].strip()[:2200]:
                stats['title_only'] += 1
            digest = hashlib.sha256(text.encode()).hexdigest()
            if (row['model'] == MODEL and row['revision'] == REVISION and
                    row['task'] == TASK and row['dimensions'] == DIMENSIONS and
                    row['text_sha256'] == digest):
                stats['current'] += 1
                continue
            stats['pending'] += 1
            if not dry_run:
                pending.append((row['id'], text, digest))
                if len(pending) >= batch_size:
                    write_batch()
            if limit is not None and stats['pending'] >= limit:
                stop = True
                break
    write_batch()
    stats['elapsed_seconds'] = round(time.monotonic() - started, 1)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', help='SQLite файл; по умолчанию site/data/fsp.sqlite3')
    parser.add_argument('--model-path', help='Каталог локальной Jina v3')
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--limit', type=int, help='Обработать не более N новых/устаревших вакансий')
    parser.add_argument('--dry-run', action='store_true', help='Подсчитать работу без загрузки модели и записи')
    args = parser.parse_args()
    result = index_all(args.db, args.model_path, args.batch_size, args.limit, args.dry_run)
    print('Итог:', result)


if __name__ == '__main__':
    main()
