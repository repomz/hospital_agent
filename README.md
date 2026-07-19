# Hospital Agent

Единый Python-проект для постоянного агента больничного компьютера: heartbeat в backend, PACS polling, отправка протоколов операций, выполнение команд из `viewer_url/user_requests`, скачивание DICOM, отправка в Yandex Cloud, импорт в MAPDR/Orthanc и генерация отчетов.

Требуется Python 3.10 или новее. Для разработки и проверки проекта рекомендуется Python 3.11.

## Структура

```text
.
├── pyproject.toml              # стандартное описание Python-проекта
├── requirements.txt            # зависимости для ручной установки
├── config.json                 # PACS и локальные настройки
├── agent_config.json           # настройки приложения hospital_agent
├── hospital_agent.py           # запуск агента без аргументов
├── hospital_agent/             # весь код проекта
└── json_examples/              # примеры входных/выходных JSON
```

## Установка

### Вариант 1: запуск без установки проекта

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

После `pip install -e .` доступна команда `hospital-agent`.

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

Для отправки в Yandex Cloud используются переменные окружения `YANDEX_ACCESS_KEY_ID`, `YANDEX_SECRET_ACCESS_KEY`, `YANDEX_BUCKET`, `YANDEX_ENDPOINT`.

## Приложение hospital_agent

`hospital_agent` читает `agent_config.json` и постоянно работает как клиент к основному backend на `viewer_url`.

Внутренняя структура пакета:

- `app.py`, `runner.py` — запуск и основной цикл агента;
- `config.py`, `state.py`, `http_client.py` — конфигурация, локальное состояние и HTTP;
- `polling/` — polling-задачи агента: heartbeat, `/agent_request`, `/user_requests`, отправка DOCX-протоколов;
- `services/commands.py` — единый диспетчер команд backend;
- `services/pacs.py` — PACS FIND и скачивание исследования;
- `services/yandex.py` — отправка DICOM в Yandex Object Storage;
- `services/mapdr.py` — отправка DICOM в MAPDR/Orthanc;
- `services/operation_reports.py` — парсинг DOCX, план операций и генерация отчета;
- `support/` — вспомогательные функции для DICOM-конфига, дат и backend payload.

Что делает агент:

- по настройкам `study_polling` сканирует `operations_dirs`;
- новые `.docx` протоколы операций парсит функциями из `hospital_agent.services.operation_reports`;
- отправляет JSON одного протокола POST-запросом на endpoint из `study_polling.endpoint`;
- по настройкам `xa_polling` выполняет PACS FIND `modality="XA"`;
- по настройкам `ct_polling` выполняет PACS FIND `modality="CT"`;
- для `work_option="on_request"` опрашивает `viewer_url/agent_request`;
- по `user_requests_polling` опрашивает `viewer_url/user_requests` и запускает команды;
- отправляет результаты на `viewer_url/xa_studies` и `viewer_url/ct_studies`.

Пример `agent_config.json`:

```json
{
  "viewer_url": "http://127.0.0.1:8080",
  "environment": "test",
  "agent_id": 2,
  "description": "операционная №2",
  "log_dir": "logs/agent",
  "state_file": "logs/agent/state.json",
  "request_timeout_seconds": 30,
  "alive_polling_interval_min": 5,
  "pacs_config_path": "config.json",
  "user_requests_polling": {
    "state": true,
    "work_option": "on_request",
    "interval_min": 1,
    "on_time": "07:30",
    "endpoint": "/user_requests"
  },
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

## Команды из backend `/user_requests`

Агент принимает объект или список объектов. ID запроса берется из `request_id`, `id` или `uuid`; если ID нет, вычисляется стабильный hash payload. Обработанные ID сохраняются в `state_file`, поэтому один и тот же список запросов не выполняется повторно после следующего polling или перезапуска агента. Журнал ограничен последними 1000 ID.

Поддержанные команды в поле `command`, `action` или `type`:

- `send_study_to_yandex`: требуется `study_uid`. Агент скачивает исследование из PACS во временную локальную папку, отправляет скачанные файлы в Yandex Cloud и затем удаляет временную папку.
- `send_dicom_to_mapdr`: требуется `dicom_path`, опционально `mapdr_host`, `mapdr_port`, `mapdr_username`, `mapdr_password`.
- `generate_operations_report`: опционально `period`, `time`, `dir1`, `dir2`, `plan_dir`, `report_dir`. Команда создает текстовый и JSON-файлы отчета; JSON также возвращается в результате `user_request`.

Агент запрашивает только свою очередь: `GET /user_requests?agent_id=<agent_id>`. Backend атомарно выдает одно задание и передает `response_endpoint`. После выполнения агент отправляет туда `agent_id`, `ok`, `retryable`, `result` и `error`.

Успешные, неподдерживаемые и некорректно сформированные команды отмечаются обработанными только после подтверждения backend. Неподтвержденный terminal result сохраняется в `state_file` и при повторной выдаче отправляется снова без повторного выполнения команды. Временные ошибки отправляются с `retryable=true`; backend повторяет задание не более `max_attempts`.

Возможные `work_option`: `on_time`, `on_request`, `interval`, `exit_session`, `logging_session`. Если `state` равен `false`, соответствующий polling полностью выключен.

Поле `environment` может быть `prod`, `dev` или `test`. Настройки из `environments.<environment>` накладываются поверх базового конфига. В текущем `test` окружении `study_polling.operations_dir` переопределяется на `C:\Операции\testing`. Для протоколов можно использовать `operations_dir` строкой или списком путей.

Каждые `alive_polling_interval_min` минут агент отправляет POST на `viewer_url/agent_status`. JSON содержит `agent_id` и `status`.

JSON протокола операции соответствует backend-структуре `StudyRequest`: `study_id`, `patient`, `age`, `department`, `name_operation`, `study_type`, `descr_operation`, `time_beginning`, `time_duration`, `surgeon`, `dicom_link`. Тип исследования определяется из названия операции, а хирург нормализуется до фамилии из backend-справочника. JSON на `/xa_studies` и `/ct_studies` содержит `agent_id`, `request_id`, `query`, `studies`, `sent_at`.

JSON отчета на `/reports` содержит границы периода, количество плановых и экстренных операций, списки выполненных операций и текущий план. Для пациентов текущего плана добавляется история предыдущих операций, если пациента удалось сопоставить по фамилии и дате рождения.

Маппинг полей `/studies` из DOCX-протокола:

- `study_id` — номер после `Операция:`.
- `patient`, `age`, `time_beginning`, `name_operation` — существующие парсеры из `hospital_agent.services.operation_reports`.
- `study_type` — классификация названия операции в один из типов backend.
- `department` — по номеру после `Карта стационарного больного`: `44` = `кардиология`, `42` = `рсц`, `26` = `сосудистая хирургия`, `179` = `неврология`.
- `descr_operation` — текст после `Описание операции:` до `Исход:`, `Рек-но:`, `Расходные материалы` или `Опер.:`.
- `time_duration` — минуты из `Длительность операции`.
- `surgeon` — значение после `Опер.:_______`.
- `dicom_link` всегда пустой: backend заполнит его позже при обновлении записи.

PACS/Yandex, MAPDR и отчеты запускаются только агентом по командам из `/user_requests`; отдельной CLI-логики в проекте нет.

## Назначение основных функций

Все функции и классы в `.py` файлах снабжены docstring-описанием назначения. Общие группы:

- конфигурация: `load_agent_config`, `load_pacs_config`, `build_date_range`;
- PACS: `PACSClient.find_studies`, `PACSClient.download_study`;
- backend-команды: `poll_user_requests`, `run_user_request`, `execute_user_command`;
- MAPDR: `upload_path_to_mapdr`, `upload_file`;
- отчеты: `generate_operations_report`, `read_docx_text`, `parse_operation_datetime`, `parse_patient_from_content`, `shorten_operation_name`, `generate_report`;

## Проверка проекта

Проверка синтаксиса:

```powershell
python -m compileall hospital_agent hospital_agent.py
```

Запуск автоматических тестов:

```powershell
python -m unittest discover -v
```

Для интеграционной проверки PACS/Yandex/MAPDR нужен доступ к больничной сети, корректные AE-настройки в `config.json`, backend viewer и установленные зависимости из `requirements.txt`.
