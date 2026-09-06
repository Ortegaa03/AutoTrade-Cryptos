@echo off
cd /d "%~dp0"

echo [1/2] Building frontend into frontend\dist ...
cd /d "%~dp0frontend"
call npm run build
if errorlevel 1 (
  echo Frontend build failed.
  pause
  exit /b 1
)

cd /d "%~dp0"
echo [2/2] Starting unified server on http://127.0.0.1:8000
echo UI + API share the same origin. Open that URL only.
start "AutoTrade" cmd /k "%~dp0.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000"
echo.
echo Abre http://127.0.0.1:8000
pause
