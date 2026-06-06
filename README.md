# Hospital Agent Tools

Набор Python-скриптов и приложение-агент для больничного компьютера. В папке есть два типа кода:

- постоянные задачи: приложение `hospital_agent`, мониторинг PACS, интерактивная работа с DICOM, скачивание исследований;
- одноразовые утилиты: загрузка папки в Yandex Cloud, импорт DICOM в Orthanc/MAPDR, синхронизация папок, формирование отчетов по операциям.

Скрипты вынесены в папку `scripts`. Проект можно установить как обычный Python-пакет и запускать часть сценариев через консольные команды.

## Структура

```text
.
├── pyproject.toml              # стандартное описание Python-проекта
├── requirements.txt            # зависимости для ручной установки
├── config.json                 # PACS и локальные настройки
├── agent_config.json           # настройки приложения hospital_agent
├── hospital_agent.py           # запуск агента без аргументов
├── hospital_agent/             # приложение для постоянных задач
├── hospital_agent_tools/       # пакетная оболочка проекта
└── scripts/                    # отдельные одноразовые и служебные скрипты
    ├── dicom_cli.py            # интерактивный и CLI-клиент PACS + Yandex Cloud
    ├── pypacs_download.py      # поиск и скачивание исследований из PACS
    ├── pacs_to_yandex.py       # скачивание из PACS с отправкой в Yandex Cloud
    ├── dicom_monitor.py        # фоновый мониторинг CT brain/head исследований
    ├── upload_to_yandex.py     # загрузка файла или папки в Yandex Cloud Object Storage
    ├── upload_to_mapdr.py      # импорт DICOM-файлов в Orthanc/MAPDR через REST API
    ├── report.py               # отчет по операциям с аргументами командной строки
    ├── report_plan.py          # быстрый отчет по операциям с дефолтными путями
    └── copy1.py                # синхронизация месячной папки операций
```

## Установка

### Вариант 1: обычный запуск скриптов

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Вариант 2: установка как проекта

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

После `pip install -e .` доступны команды из секции `project.scripts`: `hospital-agent`, `dicom-cli`, `pacs-download`, `pacs-to-yandex`, `dicom-monitor`, `upload-to-yandex`, `report-operations`, `report-plan`, `copy-month-operations`.

## Конфигурация

Основной файл настроек: `config.json`.

```json
{
  "pacs": {
    "ip": "pacs2022.okb.local",
    "port": 11112,
    "ae_title": "PACSOKB"
  },
  "local": {
    "ae_title": "RADIANT",
    "output_dir": "./dicom",
    "log_dir": "./logs",
    "dimse_timeout": 300,
    "acse_timeout": 30,
    "network_timeout": 15,
    "retry_attempts": 3,
    "retry_delay": 5
  }
}
```

Важно: в `scripts/dicom_cli.py`, `scripts/pacs_to_yandex.py` и `scripts/upload_to_yandex.py` сейчас жестко прописаны ключи Yandex Cloud. Для промышленного запуска их лучше перенести в переменные окружения или отдельный локальный конфиг, который не хранится в репозитории.

## Приложение hospital_agent

`hospital_agent` читает `agent_config.json` и постоянно работает как клиент к основному backend на `viewer_url`.

Что делает агент:

- по настройкам `study_polling` сканирует `operations_dirs`;
- новые `.docx` протоколы операций парсит функциями из `scripts.report_plan`;
- отправляет JSON одного протокола POST-запросом на `viewer_url/studies`;
- по настройкам `xa_polling` выполняет PACS FIND `modality="XA"`;
- по настройкам `ct_polling` выполняет PACS FIND `modality="CT"`;
- для `work_option="on_request"` опрашивает `viewer_url/agent_request`;
- отправляет результаты на `viewer_url/xa_studies` и `viewer_url/ct_studies`.

Пример `agent_config.json`:

```json
{
  "viewer_url": "http://127.0.0.1:8080",
  "environment": "test",
  "agent_id": "hospital-agent",
  "description": "операционная №2",
  "log_dir": "logs/agent",
  "state_file": "logs/agent/state.json",
  "request_timeout_seconds": 30,
  "alive_polling_interval_min": 5,
  "pacs_config_path": "config.json",
  "ct_polling": {
    "state": false,
    "work_option": "on_request",
    "interval_min": 10,
    "on_time": "07:30",
    "period": "today",
    "endpoint": "/ct_studies"
  },
  "xa_polling": {
    "state": false,
    "work_option": "on_request",
    "interval_min": 10,
    "on_time": "07:30",
    "period": "today",
    "endpoint": "/xa_studies"
  },
  "study_polling": {
    "state": false,
    "work_option": "interval",
    "interval_min": 10,
    "on_time": "07:30",
    "endpoint": "/studies",
    "operations_dir": [
      "C:\\Users\\Angio_hir1\\Desktop\\Операции 2026",
      "C:\\Users\\Angio_hir1\\Desktop\\2026 Опер №2"
    ]
  },
  "environments": {
    "prod": {},
    "dev": {},
    "test": {
      "study_polling": {
        "operations_dir": "C:\\Операции\\testing"
      }
    }
  }
}
```

Запуск:

```powershell
python hospital_agent.py
pythonw hospital_agent.py
```

Для фонового запуска при входе пользователя в Windows используйте `pythonw hospital_agent.py` в автозагрузке или планировщике задач.

Возможные `work_option`: `on_time`, `on_request`, `interval`, `exit_session`, `logging_session`. Если `state` равен `false`, соответствующий polling полностью выключен.

Поле `environment` может быть `prod`, `dev` или `test`. Настройки из `environments.<environment>` накладываются поверх базового конфига. В текущем `test` окружении `study_polling.operations_dir` переопределяется на `C:\Операции\testing`. Для протоколов можно использовать `operations_dir` строкой или списком путей.

Каждые `alive_polling_interval_min` минут агент отправляет POST на `viewer_url/agent_alive`. JSON содержит только `agent_id`, `status`, `sent_at`, `errors`.

JSON на `/studies` соответствует backend-структуре `StudyRequest`: `id`, `created_at`, `updated_at`, `study_id`, `patient`, `age`, `department`, `name_operation`, `descr_operation`, `time_begining`, `time_duration`, `surgeon`, `dicom_link`. JSON на `/xa_studies` и `/ct_studies` содержит `agent_id`, `request_id`, `query`, `studies`, `sent_at`.

Маппинг полей `/studies` из DOCX-протокола:

- `study_id` — номер после `Операция:`.
- `patient`, `age`, `time_begining`, `name_operation` — существующие парсеры из `scripts.report_plan`.
- `department` — по номеру после `Карта стационарного больного`: `44` = `кардиология`, `42` = `рсц`, `26` = `сосудистая хирургия`, `179` = `неврология`.
- `descr_operation` — текст после `Описание операции:` до `Исход:`, `Рек-но:`, `Расходные материалы` или `Опер.:`.
- `time_duration` — минуты из `Длительность операции`.
- `surgeon` — значение после `Опер.:_______`.
- `id` — стабильный UUID от пути и подписи файла, `created_at`/`updated_at` — время парсинга, `dicom_link` всегда пустой: backend заполнит его позже при обновлении записи.

## Команды PACS и DICOM

### `dicom_cli.py`

Интерактивный клиент для поиска исследований в PACS, скачивания исследования и опциональной загрузки в Yandex Cloud.

```powershell
python scripts\dicom_cli.py
python scripts\dicom_cli.py --interactive
python scripts\dicom_cli.py --config config.json find --modality CT --period today
python scripts\dicom_cli.py find --modality XA --period yesterday
python scripts\dicom_cli.py find --patient "Иванов" --date 2026-06-02
python scripts\dicom_cli.py get --study "1.2.840.113619..." 
python scripts\dicom_cli.py get --study "1.2.840.113619..." --yandex
```

После установки проекта:

```powershell
dicom-cli find --modality CT --period week
dicom-cli get --study "1.2.840.113619..." --yandex
```

### `pypacs_download.py`

Утилита для поиска и скачивания исследований из PACS. Пишет подробные логи в папку из `config.json`.

```powershell
python scripts\pypacs_download.py find --ct --period today
python scripts\pypacs_download.py find --xa --period last_day
python scripts\pypacs_download.py find --modality MR --patient "Petrov"
python scripts\pypacs_download.py find --ct --descr brain --period week
python scripts\pypacs_download.py find --date 2026-06-02
python scripts\pypacs_download.py get --study "1.2.840.113619..."
python scripts\pypacs_download.py get --study "1.2.840.113619..." --debug
```

После установки проекта:

```powershell
pacs-download find --ct --period today
pacs-download get --study "1.2.840.113619..."
```

### `pacs_to_yandex.py`

Вариант PACS-клиента, который скачивает исследование и параллельно отправляет полученные DICOM-файлы в Yandex Cloud.

```powershell
python scripts\pacs_to_yandex.py find --ct --period today
python scripts\pacs_to_yandex.py find --xa --period last_three_days
python scripts\pacs_to_yandex.py find --patient "Ivanov" --date 2026-06-02
python scripts\pacs_to_yandex.py get --study "1.2.840.113619..." --yandex
python scripts\pacs_to_yandex.py get --study "1.2.840.113619..." --debug --yandex
```

После установки проекта:

```powershell
pacs-to-yandex find --ct --period today
pacs-to-yandex get --study "1.2.840.113619..." --yandex
```

### `dicom_monitor.py`

Фоновая постоянная задача. Каждые `CHECK_INTERVAL` секунд ищет CT-исследования с `brain` или `head` в описании, записывает новые исследования в txt-файл и хранит обработанные UID в JSON.

```powershell
python scripts\dicom_monitor.py
```

После установки проекта:

```powershell
dicom-monitor
```

### `upload_to_mapdr.py`

Одноразовый импорт файла или папки DICOM в Orthanc/MAPDR через REST API.

```powershell
python scripts\upload_to_mapdr.py localhost 8042 "C:\dicom\patient"
python scripts\upload_to_mapdr.py localhost 8042 "C:\dicom\file.dcm"
python scripts\upload_to_mapdr.py localhost 8042 "C:\dicom\patient" username password
```

## Команды Yandex Cloud

### `upload_to_yandex.py`

Загружает один файл или папку в bucket `dicom-yandex`. Для папки может сохранять относительную структуру.

```powershell
python scripts\upload_to_yandex.py "C:\Users\Angio_hir1\Desktop\dicom\patient"
python scripts\upload_to_yandex.py "C:\Users\Angio_hir1\Desktop\file.dcm" --flat
python scripts\upload_to_yandex.py "C:\Users\Angio_hir1\Desktop\dicom" --workers 5
```

После установки проекта:

```powershell
upload-to-yandex "C:\Users\Angio_hir1\Desktop\dicom" --workers 5
```

## Команды отчетов

### `report.py`

Формирует текстовый отчет по операциям за период. Ищет `.docx` в двух папках операций, извлекает дату, пациента, возраст и тип операции, сверяет с планом и сохраняет отчет.

```powershell
python scripts\report.py
python scripts\report.py -p 2
python scripts\report.py -p 3 -t 14.00
python scripts\report.py -d1 "C:\Операции 2026" -d2 "C:\2026 Опер №2"
python scripts\report.py -pd "C:\План Отчеты" -rd "C:\План Отчеты\отчеты"
```

После установки проекта:

```powershell
report-operations -p 1 -t 08:00
```

### `report_plan.py`

Быстрый вариант отчета с дефолтными путями внутри файла. Подходит для задачи Windows без ручных аргументов.

```powershell
python scripts\report_plan.py
```

После установки проекта:

```powershell
report-plan
```

## Синхронизация папок

### `copy1.py`

Одноразовый скрипт для копирования новых или измененных файлов из сетевой месячной папки операций в локальную папку рабочего стола.

```powershell
python scripts\copy1.py
```

После установки проекта:

```powershell
copy-month-operations
```

## Назначение основных функций

Все функции и классы в `.py` файлах снабжены docstring-описанием назначения. Общие группы:

- конфигурация: `load_config`, `build_date_range`;
- PACS: `PACSClient.find`, `PACSClient.get`, `_perform_c_get`, `_build_store_handler`;
- загрузка: `setup_s3_client`, `upload_file`, `upload_folder`, `UploadFile`;
- отчеты: `read_docx_text`, `parse_operation_datetime`, `parse_patient_from_content`, `shorten_operation_name`, `generate_report`;
- мониторинг: `find_ct_brain_studies`, `write_to_file`, `load_processed`, `save_processed`;
- синхронизация: `need_to_copy`, `sync_folders`, `get_month_folder`.

## Проверка проекта

Быстрая проверка синтаксиса:

```powershell
python -m compileall .
```

Проверка доступности PACS выполняется самими PACS-скриптами через DICOM association/C-ECHO. Для нее нужен доступ к больничной сети и корректные AE-настройки в `config.json`.
