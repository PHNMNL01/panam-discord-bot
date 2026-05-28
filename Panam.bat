@echo off
cd /d "%~dp0"

echo ==============================
echo Spoustim Panam Discord bota...
echo ==============================

call .venv\Scripts\activate.bat

python bot.py

echo.
echo Panam se zastavila. Stiskni libovolnou klavesu pro zavreni okna.
pause