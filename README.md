# dwh-compare

Проверка воспроизводимости DWH: одинаковые ли данные на двух PostgreSQL (prod ↔ test).

**Python 3.12.** В реестре — список таблиц. Поля для сравнения не перечисляются: в MD5 идут все колонки, кроме ignore.

## Установка

```bash
./setup.sh
./dwh-compare.sh run -c config/config.yaml -r config/registry.yaml -p yesterday
```

## Конфиг

### `config/config.yaml`

- `server` + `source_database` / `target_database` (один хост, две БД)
- `ignore_columns` — техполя, не входят в MD5 и **не используются как ключ матчинга** (`id`, `rid`, `created_at`, …)

### `config/registry.yaml`

```yaml
tables:
  - schema: mart
    table: orders
    enabled: true
    # Бизнес-ключ (рекомендуется, если PK = rid/id в ignore)
    keys:
      - order_nk
    # Поле фильтра периода (-p yesterday / дата)
    date_column: created_at   # load-time или business-date — на ваш выбор
    # ignore_columns:         # доп. ignore только для этой таблицы
    #   - ingest_ts
    # where: "status <> 'draft'"
```

| Поле | Зачем |
|------|--------|
| `keys` | Бизнес-ключ матчинга строк. Составной — список. **Не должен быть в ignore.** |
| `date_column` | Колонка для `-p` (окно прихода или бизнес-дата — решаете вы) |
| `ignore_columns` | Доп. техполя поверх глобального ignore |
| `where` | Доп. фильтр на обеих сторонах |
| `enabled` | `false` — не проверять |

## Как сравнивает

1. Ключ строк:
   - если задан `keys:` — по нему;
   - иначе PRIMARY KEY из Postgres;
   - колонки из ignore из ключа выкидываются.
2. Если usable-ключа не осталось (типично: PK=`rid`, он в ignore) → режим **MD5-only**: сравниваются наборы хешей строк, без «поле X изменилось».
3. Payload = все колонки минус ignore минус ключ → `md5` на стороне Postgres.
4. При том же ключе и разном MD5 в Diffs — **только отличающиеся поля**.

Для нормального матчинга на DWH почти всегда нужен явный бизнес-ключ:

```yaml
keys:
  - order_nk
```

## Период

```bash
./dwh-compare.sh run ... -p yesterday
./dwh-compare.sh run ... -p 2026-10-25
./dwh-compare.sh run ... -p 2026-10-01:2026-10-25
./dwh-compare.sh run ... -p all          # без фильтра по дате
```

Без `date_column` при `-p yesterday` таблица будет `skipped`.

## Прочее

```bash
./dwh-compare.sh list-tables -r config/registry.yaml
```

Exit code `1` при расхождениях/ошибках (`--no-fail-on-diff` отключает).
