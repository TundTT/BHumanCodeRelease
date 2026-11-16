@echo off
rem bh - one command line for this repository. Run "bh" to see what it can do.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0Util\bh\bh.py" %*
  exit /b
)
python "%~dp0Util\bh\bh.py" %*
exit /b
