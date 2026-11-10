@echo off
REM Atajo para Windows: construye el ejecutable (ver build.py). Uso: doble clic o  build.bat --package
cd /d "%~dp0"
where py >nul 2>nul && (py -3 build.py %*) || (python build.py %*)
if errorlevel 1 (echo. & echo Fallo la construccion. & pause & exit /b 1)
pause
