"""Install the committed demo seed as the writable application database."""

import argparse
import shutil
import sqlite3
from pathlib import Path

from .db import ROOT, db_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', default=db_path(), help='Writable SQLite file to create')
    args = parser.parse_args()
    seed = ROOT / 'data' / 'demo_seed.sqlite3'
    target = Path(args.target).expanduser().resolve()
    if not seed.is_file():
        parser.error('Demo seed is missing from data/demo_seed.sqlite3')
    if target.exists():
        parser.error(f'Target already exists; existing data was not overwritten: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(seed, target)
    with sqlite3.connect(target) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            target.unlink()
            parser.error('Copied demo database failed integrity check')
    print(f'Demo database installed: {target}')


if __name__ == '__main__':
    main()
