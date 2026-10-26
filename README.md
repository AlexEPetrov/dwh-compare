# dwh-compare

Проверка, что два инстанса PostgreSQL DWH содержат одинаковые данные.

**Python 3.12.** В реестре — список таблиц. Поля не перечисляются: MD5 считается по всем колонкам, кроме ignore-листа.

## Установка

```bash
./setup.sh
```

## Конфиг

`config/config.yaml` — сервер + две database + глобальный ignore.

`config/registry.yaml` — какие таблицы гонять:

```yaml
tables:
  - schema: mart
    table: orders
  - schema: mart
    table: customers
    enabled: false   # временно выключить
```

Опционально у таблицы: `date_column`, `ignore_columns`, `where`.

## Запуск

```bash
./dwh-compare.sh run -c config/config.yaml -r config/registry.yaml -p yesterday
./dwh-compare.sh list-tables -r config/registry.yaml
```

Сравнение: строки матчятся по **PRIMARY KEY** (авто из Postgres; при необходимости `keys:` в реестре). Payload — MD5 всех колонок кроме ignore. В Diffs для `values_differ` выводятся **только поля с разными значениями** (`source=… target=…`).
