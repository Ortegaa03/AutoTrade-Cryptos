@echo off
cd /d "%~dp0"
echo [1/2] Backend FastAPI en :8000
start "AutoTrade-API" cmd /k "%~dp0.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000"
cd /d "%~dp0frontend"
echo [2/2] Frontend Vite en :5173
start "AutoTrade-UI" cmd /k "npm run dev"
echo.
echo Abre http://127.0.0.1:5173
echo Recuerda poner WALLET y PRIVATE_KEY en .env
pause
