@echo off
REM ============================================================
REM  Joy4_Novel - Standalone EXE builder (PyInstaller)
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo [1/4] Installing build dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 goto :error
python -m pip install pyinstaller
if errorlevel 1 goto :error

echo.
echo [2/4] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [3/4] Building with PyInstaller (this takes 1-3 minutes)...
python -m PyInstaller Joy4_Novel.spec --noconfirm
if errorlevel 1 goto :error

echo.
echo [4/4] Done.
echo.
echo ============================================================
echo  Output folder:   dist\Joy4_Novel\
echo  Executable:      dist\Joy4_Novel\Joy4_Novel.exe
echo.
echo  To distribute: zip the entire 'dist\Joy4_Novel' folder
echo  and copy to the target PC. Double-click Joy4_Novel.exe.
echo.
echo  For a proper installer, compile 'installer.iss' with
echo  Inno Setup Compiler (https://jrsoftware.org/isdl.php).
echo ============================================================
pause
exit /b 0

:error
echo.
echo *** BUILD FAILED ***
pause
exit /b 1
