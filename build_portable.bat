@echo off
cd /d "%~dp0"

REM Важно: собирать тем же Python, где стоят pygame и cryptography (проектный .venv).
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo Using: %PY%
%PY% -c "import cryptography, pygame" 2>nul
if errorlevel 1 (
  echo ERROR: В этом Python нет cryptography или pygame.
  echo Создай venv: python -m venv .venv
  echo Затем: .venv\Scripts\pip install pygame cryptography pyinstaller
  pause
  exit /b 1
)

%PY% -m pip --version >nul 2>&1
if errorlevel 1 (
  echo Adding pip to venv...
  %PY% -m ensurepip --upgrade
)

echo Installing PyInstaller if needed...
%PY% -m pip install -q "pyinstaller>=6.0"
echo Building one-file exe...

set "PYI_EXTRA_ARGS=%PYI_EXTRA_ARGS%"
%PY% -m PyInstaller --clean -y shefostycoon.spec %PYI_EXTRA_ARGS%
if errorlevel 1 exit /b 1
echo.
echo Ready: dist\SHEFOS_Tycoon.exe
echo Copy only this exe anywhere; save file appears next to it.
pause
