@echo off
rem PR Guardian 1.1.0rc1 - local only. Binds 127.0.0.1, never 0.0.0.0.
rem Uploads are enabled for this process only; AI stays off. Nothing leaves this machine.
setlocal EnableExtensions
cd /d "%~dp0"

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8000"

set "PYTHON="
where python >nul 2>&1
if %ERRORLEVEL%==0 set "PYTHON=python"
if not defined PYTHON (
  where py >nul 2>&1
  if %ERRORLEVEL%==0 set "PYTHON=py"
)
if not defined PYTHON (
  echo [error] Python was not found. Install Python 3.12 or newer from
  echo         https://www.python.org/downloads/ and run this script again.
  pause
  exit /b 1
)

%PYTHON% scripts\check_env.py
if errorlevel 3 goto :install_deps
if errorlevel 2 goto :old_python
if errorlevel 1 goto :bad_tree
if errorlevel 0 goto :start

:install_deps
echo.
echo [notice] This would run:  %PYTHON% -m pip install -e .[dev]
echo          It changes only this Python environment, not system variables.
set /p "ANSWER=Install the project dependencies now? [y/N] "
if /i not "%ANSWER%"=="y" (
  echo [stopped] Nothing was installed. Install manually with:
  echo           %PYTHON% -m pip install -e .[dev]
  pause
  exit /b 1
)
%PYTHON% -m pip install -e .[dev]
if errorlevel 1 (
  echo [error] Dependency installation failed. See the output above.
  pause
  exit /b 1
)
%PYTHON% scripts\check_env.py
if errorlevel 1 goto :bad_tree

:start
set "PR_GUARDIAN_UPLOAD_ENABLED=1"
set "PR_GUARDIAN_UPLOAD_AI=0"
set "PR_GUARDIAN_PUBLIC_MODE=0"
set "PR_GUARDIAN_ALLOW_LIVE_LLM=0"
set "PR_GUARDIAN_ALLOW_TEST_EXECUTION=1"

echo.
echo   PR Guardian 1.1.0rc1 - local review server
echo   Open: http://127.0.0.1:%PORT%
echo   Uploads: enabled locally (uploaded code is never executed)
echo   AI review: off    Public access: off
echo   Press Ctrl+C to stop. Nothing is sent to any server.
echo.
%PYTHON% -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
echo.
echo [stopped] The local server is no longer running.
pause
exit /b 0

:old_python
echo [error] Install Python 3.12 or newer, then run this script again.
pause
exit /b 1

:bad_tree
echo [error] The project files look incomplete. Re-download the repository and retry.
pause
exit /b 1

endlocal
