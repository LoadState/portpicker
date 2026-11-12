@echo off
rem Run PortPicker from source (installs PySide6 the first time)
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -c "import PySide6" 2>nul || %PY% -m pip install -r requirements.txt
rem start without a console window, so closing the black window cannot kill an install
where pyw >nul 2>nul && (start "" pyw -3 app.py) || (start "" pythonw app.py)
