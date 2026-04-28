@echo off
cd /d "%~dp0"
del _diag_claude.txt 2>nul
start "" pythonw "%~dp0_diag_claude.py"
