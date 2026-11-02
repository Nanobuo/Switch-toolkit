@echo off
rem Build SwitchToolkit.exe (GUI) and switchtk-cli.exe (riga di comando) in dist\
rem Richiede Python 3.10+ installato e nel PATH.
setlocal
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
pip install -r requirements.txt pyinstaller pillow || goto :error
set QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests || goto :error
set QT_QPA_PLATFORM=
pyinstaller --noconfirm --onefile --windowed --name SwitchToolkit --icon assets\switchtk.png --add-data "assets\switchtk.png;assets" --collect-submodules zstandard --exclude-module tkinter switchtk_gui.py || goto :error
pyinstaller --noconfirm --onefile --console --name switchtk-cli --icon assets\switchtk.png --collect-submodules zstandard --exclude-module PySide6 --exclude-module qfluentwidgets --exclude-module tkinter switchtk_cli.py || goto :error
echo.
echo Fatto: dist\SwitchToolkit.exe e dist\switchtk-cli.exe
exit /b 0
:error
echo Build fallita.
exit /b 1
