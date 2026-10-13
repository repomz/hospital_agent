@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Installed. Keep existing agent_config.json, config.json, .env and logs.
echo Start with START.cmd after checking your configuration.
pause
exit /b 0
:failed
echo Installation failed. Do not start the agent until dependencies are installed.
pause
exit /b 1
