@echo off
setlocal
cd /d "%~dp0"
py -m pip install --upgrade pip
py -m pip install -r requirements.txt pyinstaller
py -m PyInstaller --noconfirm --clean CampaignForge.spec
if errorlevel 1 exit /b 1
echo.
echo Portable build created at dist\CampaignForge.exe
pause
