# Развёртывание Hospital Agent на Windows

1. Распакуйте архив в постоянную папку, например `C:\\ViewerAgent`.
2. Установите Python 3.11 x64 (или Python 3.10+), отметив добавление Python в PATH.
3. Скопируйте `agent_config.json.example` в `agent_config.json`, а `config.json.example` в `config.json`.
4. Заполните `agent_config.json`: адрес backend `https://angio.su/api`, ID агента,
   описание и реальные пути к каталогам протоколов. Значение `agent_id` должно
   совпадать с заведённым агентом в приложении.
5. В `config.json` укажите PACS-хост, AE Title и проверенные сетевые параметры.
6. Если этому компьютеру нужен доступ к Yandex Object Storage, скопируйте
   `.env.example` в `.env` и заполните секреты локально. Не отправляйте `.env`
   через приложение и не добавляйте его в архив.
7. Откройте PowerShell в папке агента и выполните:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m hospital_agent
   ```

8. Проверьте, что в `logs\\agent\\<текущая дата>.log` появились `Agent started`
   и успешные heartbeat-записи. Для постоянной работы настройте запуск команды
   Планировщиком заданий Windows при старте компьютера.

Архив не содержит рабочих PACS/Yandex credentials, персональных данных, логов,
состояния агента, Git, виртуальных окружений или кэшированных DICOM.
