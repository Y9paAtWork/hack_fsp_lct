import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]

def now():
    return datetime.now(timezone.utc).isoformat()

def db_path():
    return Path(os.environ.get('FSP_DB', str(ROOT / 'data' / 'fsp.sqlite3')))

@contextmanager
def connect(path=None):
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=60000')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def init_db(path=None):
    with connect(path) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript((ROOT / 'backend/schema.sql').read_text())
