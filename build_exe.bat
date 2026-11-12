@echo off
rem Build PortPicker.exe (single file) with PyInstaller
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo [!] Python not found. Install Python 3.10+ from https://www.python.org/downloads/ ^(tick "Add to PATH"^)
  pause & exit /b 1
)
%PY% -m pip install --upgrade -r requirements.txt pyinstaller || (pause & exit /b 1)
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name PortPicker app.py || (pause & exit /b 1)
echo.
echo Done: %~dp0dist\PortPicker.exe
explorer "%~dp0dist"
pause
