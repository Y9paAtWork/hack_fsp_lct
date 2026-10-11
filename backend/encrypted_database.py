"""Streaming authenticated encryption for SQLite snapshots and transfer archives."""

import argparse
import base64
import os
import sqlite3
import struct
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b'FSPDBE1\0'
CHUNK_SIZE = 4 * 1024 * 1024


def generate_key():
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()


def read_key(value=None):
    raw = value or os.environ.get('FSP_DATA_KEY', '')
    try:
        key = base64.urlsafe_b64decode(raw.encode())
    except Exception as exc:
        raise ValueError('FSP_DATA_KEY должен быть ключом base64') from exc
    if len(key) != 32:
        raise ValueError('Нужен 256-битный FSP_DATA_KEY; создайте его командой keygen')
    return key


def _paths(source, target):
    source, target = Path(source).expanduser().resolve(), Path(target).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(f'Файл уже существует: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    return source, target


def encrypt(source, target, key=None):
    source, target = _paths(source, target)
    cipher = AESGCM(read_key(key))
    prefix = os.urandom(8)
    header = MAGIC + struct.pack('>I', CHUNK_SIZE) + prefix
    with source.open('rb') as inp, target.open('xb') as out:
        out.write(header)
        counter = 0
        while True:
            block = inp.read(CHUNK_SIZE)
            if not block:
                break
            counter_bytes = struct.pack('>I', counter)
            sealed = cipher.encrypt(prefix + counter_bytes, block, MAGIC + counter_bytes)
            out.write(struct.pack('>I', len(sealed)))
            out.write(sealed)
            counter += 1
        out.write(struct.pack('>I', 0))
        out.flush()
        os.fsync(out.fileno())
    os.chmod(target, 0o600)
    return {'bytes': source.stat().st_size, 'chunks': counter, 'output': str(target)}


def decrypt(source, target, key=None, check_sqlite=False):
    source, target = _paths(source, target)
    cipher = AESGCM(read_key(key))
    temp_path = None
    try:
        with source.open('rb') as inp:
            header = inp.read(len(MAGIC) + 12)
            if len(header) != len(MAGIC) + 12 or header[:len(MAGIC)] != MAGIC:
                raise ValueError('Это не зашифрованная база FSP')
            chunk_size = struct.unpack('>I', header[len(MAGIC):len(MAGIC)+4])[0]
            if chunk_size != CHUNK_SIZE:
                raise ValueError('Неизвестный размер блока')
            prefix = header[-8:]
            with tempfile.NamedTemporaryFile('wb', dir=target.parent, delete=False) as out:
                temp_path = Path(out.name)
                counter = 0
                while True:
                    length_raw = inp.read(4)
                    if len(length_raw) != 4:
                        raise ValueError('Зашифрованный файл обрезан')
                    length = struct.unpack('>I', length_raw)[0]
                    if length == 0:
                        if inp.read(1):
                            raise ValueError('Лишние данные после конца архива')
                        break
                    sealed = inp.read(length)
                    if len(sealed) != length:
                        raise ValueError('Зашифрованный файл обрезан')
                    counter_bytes = struct.pack('>I', counter)
                    out.write(cipher.decrypt(prefix + counter_bytes, sealed, MAGIC + counter_bytes))
                    counter += 1
                out.flush()
                os.fsync(out.fileno())
        if check_sqlite:
            with sqlite3.connect(temp_path) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('SQLite integrity_check завершился ошибкой')
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
        temp_path = None
        return {'bytes': target.stat().st_size, 'chunks': counter, 'output': str(target)}
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def main():
    parser = argparse.ArgumentParser(description='AES-256-GCM encryption for FSP SQLite snapshots')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('keygen')
    for name in ('encrypt', 'decrypt'):
        command = commands.add_parser(name)
        command.add_argument('source', type=Path)
        command.add_argument('target', type=Path)
    commands.choices['decrypt'].add_argument('--check-sqlite', action='store_true')
    args = parser.parse_args()
    if args.command == 'keygen':
        print(generate_key())
    elif args.command == 'encrypt':
        print(encrypt(args.source, args.target))
    else:
        print(decrypt(args.source, args.target, check_sqlite=args.check_sqlite))


if __name__ == '__main__':
    main()


