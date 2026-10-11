"""Compact transfer of current SQLite data onto the original fsp-database.zip.

The archive contains changed/new rows, new columns, vacancy query vectors,
the test bank and the grade matrix. It does not duplicate unchanged base rows.
Only the Python standard library is needed to assemble it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


BASE_MEMBER = 'data/fsp.sqlite3'
DELTA_MEMBER = 'current_delta.sqlite3'
TEST_MEMBER = 'test_bank.sqlite3'
PACKAGE_KIND = 'fsp-current-delta-v2'
LEGACY_KEYS = {
    'users': ('id',),
    'candidate_profiles': ('user_id',),
    'employer_profiles': ('user_id',),
    'resumes': ('id',),
    'resume_technologies': ('resume_id', 'technology'),
    'work_items': ('id',),
    'embedding_chunks': ('id',),
    'embeddings': ('content_sha256', 'model', 'revision', 'task', 'dimensions'),
    'item_vectors': ('item_id', 'model', 'revision', 'task', 'dimensions'),
    'vacancy_technologies': ('vacancy_id', 'technology'),
}
NEW_TABLES = (
    'candidate_grade_estimates', 'candidate_grades', 'skill_test_results',
    'candidate_skill_vectors', 'candidate_activity', 'candidate_activity_days',
    'candidate_activity_days_msk', 'candidate_daily_tests',
    'candidate_fsp_achievements', 'candidate_initial_grades',
    'candidate_test_grades', 'candidate_specialization_grades',
    'assessment_attempts', 'assessment_answers', 'technology_level_results',
    'candidate_verified_grades', 'daily_sessions', 'hiring_interactions',
    'hiring_messages', 'vacancy_query_vectors',
)
TEST_TABLES = ('technologies', 'test_questions', 'daily_questions')
VACANCY_COLUMNS = (
    ('visibility', "TEXT NOT NULL DEFAULT 'public' CHECK(visibility IN ('public','private'))"),
    ('status', "TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed'))"),
    ('closed_at', 'TEXT'),
    ('grade', "TEXT NOT NULL DEFAULT 'Junior' CHECK(grade IN ('Junior','Middle','Senior'))"),
    ('grade_source', "TEXT NOT NULL DEFAULT 'heuristic_v1'"),
    ('required_technologies_json', "TEXT NOT NULL DEFAULT '[]'"),
)
RESUME_COLUMNS = (('expected_salary_min', 'INTEGER CHECK(expected_salary_min>0)'),)
VACANCY_PATCH = tuple(name for name, _ in VACANCY_COLUMNS)


def q(name):
    return '"' + name.replace('"', '""') + '"'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def extract(archive, member, target):
    with zipfile.ZipFile(archive) as package, package.open(member) as source, Path(target).open('wb') as out:
        shutil.copyfileobj(source, out, 1024 * 1024)


def columns(c, schema, table):
    return [row[1] for row in c.execute(f'PRAGMA {schema}.table_info({q(table)})')]


def count(c, schema, table):
    return c.execute(f'SELECT count(*) FROM {schema}.{q(table)}').fetchone()[0]


def signature(c, schema):
    return {
        'counts': {table: count(c, schema, table) for table in ('users', 'resumes', 'vacancies', 'technologies', 'test_questions')},
        'import_runs': [list(row) for row in c.execute(
            f'SELECT kind,sha256,row_count FROM {schema}.import_runs ORDER BY kind,sha256')],
    }


def create_copy(c, target, select):
    c.execute(f'CREATE TABLE {q(target)} AS {select}')
    total = count(c, 'main', target)
    if not total:
        c.execute(f'DROP TABLE {q(target)}')
    return total


def export_delta(current, baseline, target):
    c = sqlite3.connect(target)
    c.execute('ATTACH DATABASE ? AS live', (str(Path(current).resolve()),))
    c.execute('ATTACH DATABASE ? AS baseline', (str(Path(baseline).resolve()),))
    counts = {}
    try:
        for table, keys in LEGACY_KEYS.items():
            name = q(table)
            join = ' AND '.join(f'b.{q(key)}=l.{q(key)}' for key in keys)
            new_name = 'new_' + table
            new_count = create_copy(c, new_name,
                f'SELECT l.* FROM live.{name} l WHERE NOT EXISTS('
                f'SELECT 1 FROM baseline.{name} b WHERE {join})')
            if new_count:
                counts[new_name] = new_count

            shared_columns = [col for col in columns(c, 'baseline', table) if col not in keys]
            if shared_columns:
                changed = ' OR '.join(f'b.{q(col)} IS NOT l.{q(col)}' for col in shared_columns)
                update_name = 'changed_' + table
                changed_count = create_copy(c, update_name,
                    f'SELECT l.* FROM live.{name} l JOIN baseline.{name} b ON {join} '
                    f'WHERE {changed}')
                if changed_count:
                    counts[update_name] = changed_count

        for table in NEW_TABLES:
            total = count(c, 'live', table)
            if total:
                create_copy(c, 'all_' + table, f'SELECT * FROM live.{q(table)}')
                counts['all_' + table] = total

        counts['resume_salary'] = create_copy(c, 'resume_salary',
            'SELECT id,expected_salary_min FROM live.resumes')
        counts['vacancy_patch'] = create_copy(c, 'vacancy_patch',
            'SELECT id,' + ','.join(q(col) for col in VACANCY_PATCH) + ' FROM live.vacancies')
        c.commit()
    finally:
        c.close()
    return counts


def export_tests(current, target):
    source = sqlite3.connect(f'file:{Path(current).resolve()}?mode=ro', uri=True)
    bank = sqlite3.connect(target)
    counts = {}
    try:
        for table in TEST_TABLES:
            definition = source.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
            bank.execute(definition)
            names = columns(source, 'main', table)
            insert = f'INSERT INTO {q(table)} VALUES({",".join("?" for _ in names)})'
            rows = source.execute(f'SELECT * FROM {q(table)}')
            while batch := rows.fetchmany(1000):
                bank.executemany(insert, batch)
            counts[table] = count(bank, 'main', table)
        bank.commit()
    finally:
        source.close()
        bank.close()
    return counts


def export(args):
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(f'Archive already exists: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fsp-current-export-', dir=output.parent) as folder:
        temp = Path(folder)
        baseline = temp / 'baseline.sqlite3'
        delta = temp / DELTA_MEMBER
        tests = temp / TEST_MEMBER
        extract(args.base_zip, BASE_MEMBER, baseline)
        with sqlite3.connect(baseline) as base, sqlite3.connect(args.current) as current:
            before, after = signature(base, 'main'), signature(current, 'main')
        if before['import_runs'] != after['import_runs'] or before['counts']['vacancies'] != after['counts']['vacancies']:
            raise ValueError('The current DB is not based on this fsp-database.zip')
        delta_counts = export_delta(args.current, baseline, delta)
        test_counts = export_tests(args.current, tests)
        manifest = {
            'kind': PACKAGE_KIND,
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'base_db_sha256': sha256(baseline),
            'baseline': before,
            'current': after,
            'delta_tables': delta_counts,
            'test_tables': test_counts,
            'delta_sha256': sha256(delta),
            'test_sha256': sha256(tests),
        }
        script = Path(__file__)
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(delta, DELTA_MEMBER)
            archive.write(tests, TEST_MEMBER)
            archive.write(script, 'transfer_current_db.py')
            archive.write(script.with_name('schema.sql'), 'schema.sql')
            archive.write(script.with_name('grade_matrix.json'), 'grade_matrix.json')
            archive.write(script.with_name('technology_stack_FINAL.csv'), 'technology_stack_FINAL.csv')
            archive.write(script.parents[1] / 'TRANSFER_CURRENT_DB.md', 'README.md')
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(f'{output} ({output.stat().st_size:,} bytes)')
    print('Delta tables:', delta_counts)


def add_columns(c, table, additions):
    existing = set(columns(c, 'main', table))
    for name, definition in additions:
        if name not in existing:
            c.execute(f'ALTER TABLE {q(table)} ADD COLUMN {q(name)} {definition}')


def insert_rows(c, source, table, source_table, expected):
    names = columns(c, source, source_table)
    before = count(c, 'main', table)
    fields = ','.join(q(name) for name in names)
    c.execute(f'INSERT OR IGNORE INTO main.{q(table)}({fields}) '
              f'SELECT {fields} FROM {source}.{q(source_table)}')
    added = count(c, 'main', table) - before
    if added != expected:
        raise ValueError(f'Unexpected number of rows inserted into {table}: {added} of {expected}')


def update_rows(c, table, source_table, keys, expected):
    names = columns(c, 'delta', source_table)
    changed = [name for name in names if name not in keys]
    if not changed:
        return
    set_clause = ','.join(f'{q(name)}=?' for name in changed)
    where = ' AND '.join(f'{q(key)}=?' for key in keys)
    statement = f'UPDATE main.{q(table)} SET {set_clause} WHERE {where}'
    rows = c.execute(f'SELECT * FROM delta.{q(source_table)}')
    total = 0
    while batch := rows.fetchmany(1000):
        for row in batch:
            data = dict(zip(names, row))
            total += c.execute(statement, [*(data[name] for name in changed),
                                           *(data[key] for key in keys)]).rowcount
    if total != expected:
        raise ValueError(f'Unexpected number of changed rows in {table}: {total} of {expected}')


def assemble(args):
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(f'Output already exists: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fsp-current-assemble-', dir=output.parent) as folder:
        temp = Path(folder)
        base = temp / 'assembled.sqlite3'
        delta = temp / DELTA_MEMBER
        tests = temp / TEST_MEMBER
        with zipfile.ZipFile(args.package) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            schema = archive.read('schema.sql').decode('utf-8')
        if manifest.get('kind') != PACKAGE_KIND:
            raise ValueError('Unsupported transfer package')
        extract(args.base_zip, BASE_MEMBER, base)
        if sha256(base) != manifest['base_db_sha256']:
            raise ValueError('fsp-database.zip differs from the package baseline')
        extract(args.package, DELTA_MEMBER, delta)
        extract(args.package, TEST_MEMBER, tests)
        if sha256(delta) != manifest['delta_sha256'] or sha256(tests) != manifest['test_sha256']:
            raise ValueError('Transfer package checksum mismatch')
        c = sqlite3.connect(base)
        try:
            if signature(c, 'main') != manifest['baseline']:
                raise ValueError('Baseline database has changed')
            c.execute('PRAGMA foreign_keys=ON')
            c.executescript(schema)
            add_columns(c, 'vacancies', VACANCY_COLUMNS)
            add_columns(c, 'resumes', RESUME_COLUMNS)
            c.execute('CREATE INDEX IF NOT EXISTS ix_vacancy_discovery ON vacancies(visibility,status,specialization)')
            c.execute('ATTACH DATABASE ? AS delta', (str(delta.resolve()),))
            c.execute('ATTACH DATABASE ? AS tests', (str(tests.resolve()),))
            c.execute('BEGIN')

            test_counts = manifest['test_tables']
            for table in TEST_TABLES:
                if table == 'daily_questions':
                    insert_rows(c, 'tests', table, table, test_counts[table])
                elif count(c, 'main', table) != test_counts[table]:
                    raise ValueError(f'Base {table} differs from packaged test bank')

            counts = manifest['delta_tables']
            for table in LEGACY_KEYS:
                name = 'new_' + table
                if name in counts:
                    insert_rows(c, 'delta', table, name, counts[name])
            for table in NEW_TABLES:
                name = 'all_' + table
                if name in counts:
                    insert_rows(c, 'delta', table, name, counts[name])
            for table, keys in LEGACY_KEYS.items():
                name = 'changed_' + table
                if name in counts:
                    update_rows(c, table, name, keys, counts[name])
            update_rows(c, 'resumes', 'resume_salary', ('id',), counts['resume_salary'])
            update_rows(c, 'vacancies', 'vacancy_patch', ('id',), counts['vacancy_patch'])
            c.execute("INSERT OR IGNORE INTO schema_version VALUES(3,strftime('%Y-%m-%dT%H:%M:%SZ','now'))")
            c.execute("INSERT OR IGNORE INTO schema_version VALUES(4,strftime('%Y-%m-%dT%H:%M:%SZ','now'))")
            problems = c.execute('PRAGMA foreign_key_check').fetchmany(5)
            if problems:
                raise ValueError(f'Foreign key violations: {problems}')
            c.commit()
            if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite quick_check failed')
            if signature(c, 'main') != manifest['current']:
                raise ValueError('Assembled database counts do not match the current database')
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
        os.replace(base, output)
    print(f'{output} ({output.stat().st_size:,} bytes)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    exp = commands.add_parser('export', help='Create one compact ZIP from the current DB')
    exp.add_argument('--base-zip', type=Path, required=True)
    exp.add_argument('--current', type=Path, required=True)
    exp.add_argument('--output', type=Path, required=True)
    exp.set_defaults(run=export)
    ass = commands.add_parser('assemble', help='Merge fsp-database.zip with the compact ZIP')
    ass.add_argument('--base-zip', type=Path, required=True)
    ass.add_argument('--package', type=Path, required=True)
    ass.add_argument('--output', type=Path, required=True)
    ass.set_defaults(run=assemble)
    args = parser.parse_args()
    args.run(args)


if __name__ == '__main__':
    main()
