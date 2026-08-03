# Hospital Agent

Единый Python-проект для постоянного агента больничного компьютера: heartbeat в backend, PACS polling, отправка протоколов операций, выполнение команд из `viewer_url/user_requests`, скачивание DICOM, отправка в Yandex Cloud и генерация отчетов. Импорт из Yandex в remote PACS выполняет backend.

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

## Конфигурация PACS

Файл `config.json` содержит настройки PACS и локального DICOM-каталога.

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

Для отправки в Yandex Cloud используются `YANDEX_ACCESS_KEY_ID`,
`YANDEX_SECRET_ACCESS_KEY`, `YANDEX_BUCKET`, `YANDEX_ENDPOINT`. Агент загружает
их из стандартного файла `.env` рядом с `agent_config.json`. Уже заданные
системные переменные окружения имеют приоритет и не перезаписываются. `env.txt`
не используется. Перед скачиванием крупного исследования агент проверяет
полноту конфигурации и доступность указанного бакета.

## Приложение hospital_agent

`hospital_agent` читает `agent_config.json` и постоянно работает как клиент к основному backend на `viewer_url`.

Поле `viewer_url` — это базовый URL viewer backend. Значение `http://127.0.0.1:8080` подходит только тогда, когда backend запущен на том же компьютере, что и агент. Если backend работает на другом компьютере, укажите его доступный IP-адрес или DNS-имя, например `http://192.168.1.20:8080`.

Поля `log_dir`, `state_file`, `pacs_config_path` и относительные пути к папкам операций разрешаются от каталога, в котором находится `agent_config.json`. Поэтому запуск из Планировщика Windows не зависит от текущей рабочей папки процесса.

Внутренняя структура пакета:

- `app.py`, `runner.py` — запуск и основной цикл агента;
- `config.py`, `state.py`, `http_client.py` — конфигурация, локальное состояние и HTTP;
- `polling/` — независимые задачи heartbeat, `/user_requests`, DOCX, CT/XA и очистки Yandex;
- `services/commands.py` — единый диспетчер команд backend;
- `services/pacs.py` — PACS FIND и скачивание исследования;
- `services/yandex.py` — отправка DICOM в Yandex Object Storage;
- `services/operation_reports.py` — парсинг DOCX, план операций и генерация отчета;
- `support/` — вспомогательные функции для DICOM-конфига, дат и backend payload.

Что делает агент:

- независимо выполняет heartbeat, user requests, DOCX, CT и XA polling;
- новые `.docx` протоколы отправляет на фиксированный endpoint `/studies`;
- пустые DOCX и временные файлы Word `~$...` не обрабатывает, а неизменившийся
  некорректный протокол не перечитывает при каждом polling;
- копии одной операции в разных папках дедуплицирует для `/studies`, отчётов и
  результатов `find_study`;
- CT/XA polling обрабатывает исследования от момента включения до ближайших 08:00;
- скачивает DICOM напрямую по StudyInstanceUID, проверяет финальный статус C-GET,
  полноту, фактическую модальность, пациента и дату, затем загружает в Yandex;
- принимает вместе с CT/XA распространенные вторичные DICOM-объекты исследования:
  enhanced images, secondary capture, presentation states, waveforms, SR и PDF;
- передает метаданные и трехдневные ссылки на `/ct_studies` или `/xa_studies`;
- не формирует отчёты: их строит backend из уже загруженных протоколов и плана;
- удаляет DICOM из Yandex через три суток по локальной очереди в `state_file`.

Пример `agent_config.json`:

```json
{
  "viewer_url": "http://127.0.0.1:8080",
  "agent_id": 2,
  "description": "операционная №2",
  "log_dir": "logs/agent",
  "state_file": "logs/agent/state.json",
  "alive_polling_interval_min": 5,
  "pacs_config_path": "config.json",
  "user_requests_polling": {
    "state": true,
    "interval_min": 1
  },
  "ct_polling": {
    "state": false,
    "interval_min": 10
  },
  "xa_polling": {
    "state": true,
    "interval_min": 10
  },
  "study_polling": {
    "state": false,
    "interval_min": 1,
    "operations_dir": [
      "C:\\Users\\Angio_hir1\\Desktop\\Операции 2026",
      "C:\\Users\\Angio_hir1\\Desktop\\2026 Опер №2"
    ]
  }
}
```

Запуск:

```powershell
python hospital_agent.py
pythonw hospital_agent.py
```

Режим логирования выбирается автоматически:

- `python hospital_agent.py` — агент полностью работает, а сообщения выводятся только в терминал; лог-файлы не создаются;
- `pythonw hospital_agent.py` — агент полностью работает без окна терминала, а сообщения записываются только в `log_dir`.

В фоновом режиме создается отдельный текстовый лог на каждый день: `logs/agent/YYYY-MM-DD.log`. После полуночи агент автоматически начинает писать в файл новой даты.

Формат каждой строки:

```text
дата | уровень | агент | python-файл:строка | событие и параметры
```

Например:

```text
2026-07-27 08:00:03,442 | INFO | agent_2 | hospital_agent/runner.py:146 | Duty report sent: duty_end=2026-07-27 period_days=3
```

### Планировщик заданий Windows

Рекомендуемая настройка действия:

- программа: полный путь к `pythonw.exe`, лучше из виртуального окружения проекта — `C:\путь\к\проекту\.venv\Scripts\pythonw.exe`;
- аргументы: полный путь к `hospital_agent.py`;
- рабочая папка: каталог проекта. Поле можно не заполнять, но явное значение упрощает диагностику.

Используйте тот же интерпретатор, в который установлены зависимости из `requirements.txt`. После первого запуска проверьте файл текущей даты в `logs\agent`. В нем должны появиться строки `Logging mode=daily_file` и `Agent started`.

В свойствах задания включите перезапуск при сбое, отключите ограничение
«Останавливать задачу, выполняемую дольше», а также ненужные условия остановки
при простое или переходе на питание от батареи. Внутренний runtime агента сам
перезапускается через 10 секунд после неожиданной программной ошибки, но не
может восстановиться после принудительного завершения процесса Windows.

## Команды из backend `/user_requests`

Агент принимает объект или список объектов. ID запроса берется из `request_id`, `id` или `uuid`; если ID нет, вычисляется стабильный hash payload. Обработанные ID сохраняются в `state_file`, поэтому один и тот же список запросов не выполняется повторно после следующего polling или перезапуска агента. Журнал ограничен последними 1000 ID.

Имя команды принимается только из поля `command`. Поддержаны:

- `find_xa`, `find_ct` — поиск по фамилии (`patient`) и периоду
  `today`, `yesterday`, `three_days`, `week`, `month`, `six_months`, `year`
  или точной дате `YYYY-MM-DD`;
- `get_xa`, `get_ct` — C-GET по `study_uid`, загрузка в Yandex, регистрация
  в backend и импорт backend → remote PACS; UID и фактическая модальность
  исследования проверяются до отправки в облако; импорт в remote PACS
  выполняется и для уже зарегистрированного исследования;
- `find_study` — поиск стандартизованных протоколов операций по фамилии;
- `import_study` — загрузка в backend одного выбранного результата по
  непрозрачному `protocol_ref`;
- `sync_studies` — немедленная проверка всех `operations_dir` и отправка
  новых протоколов на `/studies`;
- `send_xa_to_pacs`, `send_ct_to_pacs` — повторная безопасная отправка
  выбранного DICOM-исследования в remote PACS;
- `ct_polling_on`, `ct_polling_off`, `xa_polling_on`, `xa_polling_off` —
  изменение режима в памяти и в `agent_config.json`.

При включённом `xa_polling` агент каждые `interval_min` минут проверяет только
XA текущей календарной недели. Новые XA проходят через Yandex, backend и
удалённый PACS. В отличие от CT этот режим не выключается автоматически в
08:00.

Протоколы операций управляются отдельным блоком `study_polling`. При
`study_polling.state: true` агент проверяет `operations_dir` с его собственным
`interval_min` и отправляет на `/studies` только новые протоколы с 00:00
понедельника текущей календарной недели до текущего момента. Старый архив не
отправляется даже после длительного простоя агента или удаления `state.json`.
При `state: false`
фоновая проверка протоколов не выполняется, но ручная команда `sync_studies`
продолжает работать. Локальный план больничного компьютера агент не читает:
единственный план создаётся и хранится в backend через web/mobile.

Агент запрашивает только свою очередь: `GET /user_requests?agent_id=<agent_id>`.
Backend переводит запрос `pending → in_progress`, а после callback с полями
`agent_id`, `ok`, `retryable`, `result`, `errors` — в `completed` или `error`.

Успешные, неподдерживаемые и некорректно сформированные команды отмечаются обработанными только после подтверждения backend. Неподтвержденный terminal result сохраняется в `state_file` и при повторной выдаче отправляется снова без повторного выполнения команды. Временные ошибки отправляются с `retryable=true`; backend повторяет задание не более `max_attempts`.

Каждые `alive_polling_interval_min` минут агент отправляет POST на `viewer_url/agent_status`. JSON содержит `agent_id` и `status`.

JSON протокола операции соответствует backend-структуре `StudyRequest`: `study_id`, `patient`, `age`, `department`, `name_operation`, `study_type`, `descr_operation`, `time_beginning`, `time_duration`, `surgeon`, `dicom_link`. Тип исследования определяется из названия операции, а хирург нормализуется до фамилии без проверки по фиксированным справочникам. JSON на `/xa_studies` и `/ct_studies` содержит пациента, возраст, дату, StudyInstanceUID, `dicom_link` и временные URL файлов для импорта в remote PACS.

JSON отчета на `/reports` содержит границы периода, количество плановых и
экстренных операций, плановый список предыдущего дня и текущий план из
недельного файла. Для пациентов текущего плана добавляется возраст и история
предыдущих операций, если пациента удалось сопоставить по фамилии и дате
рождения.

Маппинг полей `/studies` из DOCX-протокола:

- `study_id` — номер после `Операция:`.
- `patient`, `age`, `time_beginning` — существующие парсеры из
  `hospital_agent.services.operation_reports`.
- `name_operation` — полное, несокращённое название из строки `Операция:`.
- `study_type` — нормализованный известный тип либо сокращенное название новой операции.
- `department` — по номеру после `Карта стационарного больного`: `44` = `кардиология`, `42` = `рсц`, `26` = `сосудистая хирургия`, `179` = `неврология`.
- `descr_operation` — подготовленный агентом текст: сначала клиническое
  заключение, затем сокращённый ход операции и рекомендации. Повторяющиеся
  технические фразы и стандартные этапы доступа удаляются до отправки.
- `time_duration` — минуты из `Длительность операции`.
- `surgeon` — значение после `Опер.:_______`.
- `dicom_link` всегда пустой: backend заполнит его позже при обновлении записи.

Endpoint и граница дежурства 08:00 являются частью протокола и не дублируются в конфигурации.

## Назначение основных функций

Все функции и классы в `.py` файлах снабжены docstring-описанием назначения. Общие группы:

- конфигурация: `load_agent_config`, `load_pacs_config`, `build_date_range`;
- PACS: `PACSClient.find_studies`, `PACSClient.download_study`;
- backend-команды: `poll_user_requests`, `run_user_request`, `execute_user_command`;
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

Проверка реального каталога протоколов теми же парсерами, что использует агент:

```powershell
python scripts\audit_protocols.py "C:\Users\Angio_hir1\Desktop\Операции 2026"
```

Команда показывает количество пустых и временных DOCX, ошибки парсинга и
дубликаты операций. Нулевой exit code означает, что каждый непустой рабочий
DOCX подходит и для отчёта, и для `/studies`.

Для интеграционной проверки PACS/Yandex/MAPDR нужен доступ к больничной сети, корректные AE-настройки в `config.json`, backend viewer и установленные зависимости из `requirements.txt`.
