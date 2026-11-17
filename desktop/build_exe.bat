@echo off
rem Builds iLabFree.exe (dist folder) for PCs without Python
cd /d "%~dp0"
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --windowed --name iLabFree --add-data "data;data" --collect-binaries libusb_package run.py
pause
