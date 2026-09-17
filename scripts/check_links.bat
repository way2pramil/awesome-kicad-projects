@echo off
REM Run the local link checker from the repo root.
REM Usage: scripts\check_links.bat [--strict] [files...]
cd /d "%~dp0\.."
python scripts\check_links.py %*
