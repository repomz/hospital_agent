@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe hospital_agent.py --reconcile
pause
