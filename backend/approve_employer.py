"""Approve or block employer access to the candidate matching API."""

import argparse

from .db import connect, init_db, now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('email')
    parser.add_argument('--block', action='store_true')
    args = parser.parse_args()
    init_db()
    status = 'blocked' if args.block else 'approved'
    with connect() as c:
        row = c.execute("SELECT id FROM users WHERE email=? AND role='employer' AND origin='registration'",
                        (args.email.strip().lower(),)).fetchone()
        if not row:
            raise SystemExit('Работодатель с такой почтой не найден')
        c.execute('UPDATE employer_profiles SET access_status=?,approved_at=? WHERE user_id=?',
                  (status, now() if status == 'approved' else None, row['id']))
    print(f'{args.email}: {status}')


if __name__ == '__main__':
    main()


