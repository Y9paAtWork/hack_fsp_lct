"""Export user-data changes and the test bank; assemble them over fsp-database.zip.

Only Python's standard library is required. The user package contains rows absent
from the baseline archive plus tables added after that archive was created.
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


BASE_MEMBER = "data/fsp.sqlite3"
USER_MEMBER = "user_delta.sqlite3"
TEST_MEMBER = "test_bank.sqlite3"
BASE_TABLES = ("users", "resumes", "vacancies", "technologies", "test_questions")

# Existing baseline tables: export only rows with new primary keys.
LEGACY_KEYS = {
    "users": ("id",),
    "candidate_profiles": ("user_id",),
    "employer_profiles": ("user_id",),
    "resumes": ("id",),
    "resume_technologies": ("resume_id", "technology"),
    "work_items": ("id",),
    "embedding_chunks": ("id",),
    "embeddings": ("content_sha256", "model", "revision", "task", "dimensions"),
    "item_vectors": ("item_id", "model", "revision", "task", "dimensions"),
}

# All rows in these tables are new relative to the baseline. Empty tables are
# omitted from the transfer DB; schema.sql creates them during assembly.
USER_TABLES = (
    "candidate_grade_estimates",
    "candidate_grades",
    "skill_test_results",
    "candidate_skill_vectors",
    "candidate_activity",
    "candidate_activity_days",
    "candidate_activity_days_msk",
    "candidate_daily_tests",
    "candidate_fsp_achievements",
    "candidate_initial_grades",
    "candidate_test_grades",
    "candidate_specialization_grades",
    "assessment_attempts",
    "assessment_answers",
    "technology_level_results",
    "candidate_verified_grades",
    "daily_sessions",
    "hiring_interactions",
    "hiring_messages",
)
TEST_TABLES = ("technologies", "test_questions", "daily_questions")


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def copy_zip_member(archive: Path, member: str, destination: Path) -> None:
    with zipfile.ZipFile(archive) as source, source.open(member) as inp, destination.open("wb") as out:
        shutil.copyfileobj(inp, out, 1024 * 1024)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, uri=True)
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def attach_readonly(connection: sqlite3.Connection, path: Path, alias: str) -> None:
    # SQLite URI mode=ro also prevents writing through the attached database.
    connection.execute(f"ATTACH DATABASE ? AS {alias}", (path.resolve().as_uri() + "?mode=ro",))


def baseline_signature(connection: sqlite3.Connection, schema: str) -> dict:
    counts = {
        table: connection.execute(f"SELECT count(*) FROM {schema}.{quoted(table)}").fetchone()[0]
        for table in BASE_TABLES
    }
    runs = connection.execute(
        f"SELECT kind, sha256, row_count FROM {schema}.import_runs ORDER BY kind, sha256"
    ).fetchall()
    return {"counts": counts, "import_runs": [list(row) for row in runs]}


def make_user_delta(current: Path, base: Path, target: Path) -> dict[str, int]:
    connection = connect(target)
    attach_readonly(connection, current, "live")
    attach_readonly(connection, base, "baseline")
    counts: dict[str, int] = {}
    try:
        connection.execute("BEGIN")
        for table, keys in LEGACY_KEYS.items():
            name = quoted(table)
            condition = " AND ".join(f"b.{quoted(key)}=l.{quoted(key)}" for key in keys)
            connection.execute(f"CREATE TABLE {name} AS SELECT * FROM live.{name} WHERE 0")
            connection.execute(
                f"INSERT INTO {name} SELECT l.* FROM live.{name} AS l "
                f"WHERE NOT EXISTS(SELECT 1 FROM baseline.{name} AS b WHERE {condition})"
            )
            counts[table] = connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            if not counts[table]:
                connection.execute(f"DROP TABLE {name}")
                del counts[table]
        for table in USER_TABLES:
            name = quoted(table)
            count = connection.execute(f"SELECT count(*) FROM live.{name}").fetchone()[0]
            if not count:
                continue
            connection.execute(f"CREATE TABLE {name} AS SELECT * FROM live.{name} WHERE 0")
            connection.execute(f"INSERT INTO {name} SELECT * FROM live.{name}")
            counts[table] = count
        connection.commit()
    finally:
        connection.close()
    return counts


def make_test_bank(current: Path, target: Path) -> dict[str, int]:
    bank = connect(target)
    source = sqlite3.connect(current.resolve().as_uri() + "?mode=ro", uri=True)
    counts: dict[str, int] = {}
    try:
        for table in TEST_TABLES:
            definition = source.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not definition:
                raise ValueError(f"Missing test table: {table}")
            bank.execute(definition[0])
            columns = [row[1] for row in source.execute(f"PRAGMA table_info({quoted(table)})")]
            placeholders = ",".join("?" for _ in columns)
            rows = source.execute(f"SELECT * FROM {quoted(table)}")
            while batch := rows.fetchmany(1000):
                bank.executemany(f"INSERT INTO {quoted(table)} VALUES({placeholders})", batch)
            counts[table] = bank.execute(f"SELECT count(*) FROM {quoted(table)}").fetchone()[0]
        bank.execute("CREATE INDEX ix_test_questions_tech_grade ON test_questions(technology,grade)")
        bank.commit()
    finally:
        source.close()
        bank.close()
    return counts


def package(target: Path, database: Path, member: str, manifest: dict, readme: str) -> None:
    manifest = {**manifest, "database_sha256": sha256(database)}
    script = Path(__file__)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(database, member)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("README.txt", readme)
        if member == USER_MEMBER:
            archive.write(script, "transfer_compact_db.py")
            archive.write(script.with_name("schema.sql"), "schema.sql")
            archive.write(script.parents[1] / "TRANSFER_COMPACT_DB.md", "TRANSFER_COMPACT_DB.md")


def export(args: argparse.Namespace) -> None:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    user_zip = args.out_dir / "fsp_user_delta.zip"
    test_zip = args.out_dir / "fsp_test_bank.zip"
    if user_zip.exists() or test_zip.exists():
        raise FileExistsError("Output ZIP already exists; choose another --out-dir")
    with tempfile.TemporaryDirectory(prefix="fsp-transfer-export-") as folder:
        temp = Path(folder)
        base = temp / "baseline.sqlite3"
        copy_zip_member(args.base_zip, BASE_MEMBER, base)
        signature_connection = connect(base)
        signature = baseline_signature(signature_connection, "main")
        signature_connection.close()
        current_connection = sqlite3.connect(args.current.resolve().as_uri() + "?mode=ro", uri=True)
        live_signature = baseline_signature(current_connection, "main")
        current_connection.close()
        if live_signature["import_runs"] != signature["import_runs"]:
            raise ValueError("Current database and baseline archive have different imported datasets")
        for table in ("vacancies", "technologies", "test_questions"):
            if live_signature["counts"][table] != signature["counts"][table]:
                raise ValueError(f"Baseline count changed in {table}; review the transfer scope")
        user_db, test_db = temp / USER_MEMBER, temp / TEST_MEMBER
        user_counts = make_user_delta(args.current, base, user_db)
        test_counts = make_test_bank(args.current, test_db)
        common = {"created_utc": datetime.now(timezone.utc).isoformat(), "baseline": signature}
        package(user_zip, user_db, USER_MEMBER,
                {**common, "kind": "fsp-user-delta-v1", "tables": user_counts},
                "Данные пользователей сверх fsp-database.zip. Инструкция: см. transfer_compact_db.py assemble --help.\n")
        package(test_zip, test_db, TEST_MEMBER,
                {**common, "kind": "fsp-test-bank-v1", "tables": test_counts},
                "Отдельная SQLite БД: технологии, технологические и ежедневные вопросы.\n")
    for path, counts in ((user_zip, user_counts), (test_zip, test_counts)):
        print(f"{path} ({path.stat().st_size:,} bytes): {counts}")


def unpack_transfer(archive: Path, member: str, target: Path, expected_kind: str) -> dict:
    with zipfile.ZipFile(archive) as source:
        manifest = json.loads(source.read("manifest.json"))
    if manifest.get("kind") != expected_kind:
        raise ValueError(f"Unexpected package kind in {archive}")
    copy_zip_member(archive, member, target)
    if sha256(target) != manifest["database_sha256"]:
        raise ValueError(f"Checksum mismatch in {archive}")
    return manifest


def assert_baseline(connection: sqlite3.Connection, expected: dict) -> None:
    actual = baseline_signature(connection, "main")
    if actual != expected:
        raise ValueError(f"Base DB does not match fsp-database.zip: {actual['counts']}")


def add_vacancy_columns(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(vacancies)")}
    additions = (
        ("visibility", "TEXT NOT NULL DEFAULT 'public' CHECK(visibility IN ('public','private'))"),
        ("status", "TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed'))"),
        ("closed_at", "TEXT"),
    )
    for name, definition in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE vacancies ADD COLUMN {name} {definition}")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_vacancy_discovery "
        "ON vacancies(visibility,status,specialization)"
    )


def insert_tables(connection: sqlite3.Connection, source: str, tables: dict[str, int], order: tuple[str, ...]) -> None:
    for table in order:
        if table not in tables:
            continue
        name = quoted(table)
        before = connection.execute(f"SELECT count(*) FROM main.{name}").fetchone()[0]
        connection.execute(f"INSERT OR IGNORE INTO main.{name} SELECT * FROM {source}.{name}")
        after = connection.execute(f"SELECT count(*) FROM main.{name}").fetchone()[0]
        expected_added = tables[table] if source == "delta" else max(0, tables[table] - before)
        if after - before != expected_added:
            raise ValueError(f"Unexpected duplicate or missing rows in {table}: {after - before} inserted")


def assemble(args: argparse.Namespace) -> None:
    if args.output.exists():
        raise FileExistsError(f"Output already exists: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fsp-transfer-", dir=args.output.parent) as folder:
        temp = Path(folder)
        base, delta, tests = temp / "assembled.sqlite3", temp / USER_MEMBER, temp / TEST_MEMBER
        copy_zip_member(args.base_zip, BASE_MEMBER, base)
        user_manifest = unpack_transfer(args.users_zip, USER_MEMBER, delta, "fsp-user-delta-v1")
        test_manifest = unpack_transfer(args.tests_zip, TEST_MEMBER, tests, "fsp-test-bank-v1")
        if user_manifest["baseline"] != test_manifest["baseline"]:
            raise ValueError("The user and test packages were built from different baselines")
        connection = connect(base)
        try:
            assert_baseline(connection, user_manifest["baseline"])
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
            add_vacancy_columns(connection)
            attach_readonly(connection, delta, "delta")
            attach_readonly(connection, tests, "tests")
            connection.execute("BEGIN")
            insert_tables(connection, "tests", test_manifest["tables"], TEST_TABLES)
            insert_tables(connection, "delta", user_manifest["tables"],
                          (*LEGACY_KEYS, *USER_TABLES))
            connection.execute(
                "INSERT OR IGNORE INTO schema_version(version,applied_at) "
                "VALUES(3,strftime('%Y-%m-%dT%H:%M:%SZ','now'))"
            )
            violations = connection.execute("PRAGMA foreign_key_check").fetchmany(5)
            if violations:
                raise ValueError(f"Foreign key violations: {violations}")
            connection.commit()
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite quick_check failed")
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        os.replace(base, args.output)
    print(f"Ready: {args.output} ({args.output.stat().st_size:,} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    exp = commands.add_parser("export", help="Create the two compact transfer ZIPs")
    exp.add_argument("--base-zip", type=Path, required=True)
    exp.add_argument("--current", type=Path, required=True)
    exp.add_argument("--out-dir", type=Path, required=True)
    exp.set_defaults(run=export)
    ass = commands.add_parser("assemble", help="Build a full DB from the baseline and the two ZIPs")
    ass.add_argument("--base-zip", type=Path, required=True)
    ass.add_argument("--users-zip", type=Path, required=True)
    ass.add_argument("--tests-zip", type=Path, required=True)
    ass.add_argument("--output", type=Path, required=True)
    ass.set_defaults(run=assemble)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
