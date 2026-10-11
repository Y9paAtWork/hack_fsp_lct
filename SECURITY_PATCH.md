# Защищённый подбор кандидатов

Защита встроена в актуальную финальную версию проекта.

## Установка

Из каталога проекта:

```bash
source .venv/bin/activate
python3 -m pip install -r backend/requirements.txt
npm ci
npm run build
python3 -m unittest discover -s backend/tests -v
npm test
npm run test:ui
python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

При первом запуске `init_db()` добавит таблицы доступа и аудита. Существующие аккаунты работодателей будут одобрены при миграции; новые создаются со статусом `pending`.

Одобрение нового работодателя:

```bash
python3 -m backend.approve_employer company@example.org
```

## Что изменено

- закрыта общая выдача `/api/candidates`;
- полное резюме доступно только в разрешённом взаимодействии;
- подбор разрешён только владельцу открытой вакансии;
- импортированные вакансии больше нельзя использовать для выгрузки кандидатов;
- top-20 выдаётся без имени и внутренних идентификаторов;
- приглашение принимает ограниченный `match_id`, а не произвольный `candidate_id`;
- по умолчанию не более 50 новых кандидатов на компанию в сутки;
- поиск, приглашения и раскрытия записываются в аудит;
- добавлено потоковое AES-256-GCM шифрование снимков SQLite;
- добавлены security-тесты.

Ключ шифрования храните отдельно от `.fspenc` и никогда не добавляйте в Git.

## Шифрование БД для передачи и резервной копии

Остановите backend и создайте ключ один раз:

```bash
export FSP_DATA_KEY="$(python3 -m backend.encrypted_database keygen)"
python3 -m backend.encrypted_database encrypt data/fsp.sqlite3 fsp.sqlite3.fspenc
```

Передавайте `fsp.sqlite3.fspenc`, а ключ — отдельно, например через менеджер паролей.
На другом устройстве:

```bash
export FSP_DATA_KEY='ключ-из-менеджера-паролей'
python3 -m backend.encrypted_database decrypt fsp.sqlite3.fspenc data/fsp.sqlite3 --check-sqlite
```

Работающий SQLite остаётся расшифрованным на диске, поэтому каталог `data/` должен
быть доступен только системному пользователю сервиса. Эта команда защищает именно
передаваемые и резервные копии; ключ нельзя хранить рядом с ними.


