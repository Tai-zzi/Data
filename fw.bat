@echo off
REM fw.bat : run everything from the Command Prompt (CMD).  Put this file in C:\CoGoalTis and run it there.
REM   fw list                    show the COM ports
REM   fw check                   check that all python files import correctly
REM   fw build NAME              compile only (NAME = folder name under firmware, for example mic_check)
REM   fw up NAME COM4            compile + upload to the board (asks YES first)
REM   fw peek COM4 [sec] [baud]  print the board text for N seconds (default 20 s, 115200)
REM   fw mic COM4                upload firmware\mic_check and show its output for 45 s (asks YES first)
REM   fw stream COM4             python check_stream.py COM4 10
REM   fw clench COM4             python emg_clench_test.py COM4 (asks YES first, electrodes on body)
REM   fw run COM4                python report_test.py COM4 (asks YES first, electrodes on body)
REM   fw collect COM4 [opts]     live data collection with graphs (collect.py, asks YES first)
setlocal
set "ROOT=%~dp0"
set "FQBN=esp32:esp32:esp32s3"
set "ACT=%~1"
if "%ACT%"=="" goto help
if /i "%ACT%"=="help" goto help
if /i "%ACT%"=="list" goto list
if /i "%ACT%"=="check" goto check
if /i "%ACT%"=="build" goto build
if /i "%ACT%"=="up" goto up
if /i "%ACT%"=="peek" goto peek
if /i "%ACT%"=="mic" goto mic
if /i "%ACT%"=="stream" goto stream
if /i "%ACT%"=="clench" goto clench
if /i "%ACT%"=="run" goto run
if /i "%ACT%"=="collect" goto collect
echo Unknown command: %ACT%
goto help

:help
echo.
echo   fw list                    show the COM ports
echo   fw check                   check that all python files import correctly
echo   fw build NAME              compile only (NAME = folder under firmware, e.g. mic_check)
echo   fw up NAME COM4            compile + upload (asks YES first)
echo   fw peek COM4 [sec] [baud]  print board text for N seconds (default 20 s, 115200)
echo   fw mic COM4                upload mic_check and show its output for 45 s (asks YES first)
echo   fw stream COM4             check the data stream for 10 s
echo   fw clench COM4             jaw clench test, 40 s (asks YES first)
echo   fw run COM4                combined test, 150 s (asks YES first)
echo   fw collect COM4 [opts]     live collection with graphs, saves everything (asks YES first)
echo                              opts: --minutes 60   --no-gui
echo.
exit /b 0

:list
python "%ROOT%serial_source.py" --list
exit /b %errorlevel%

:check
cd /d "%ROOT%"
python -c "import config, dashboard, stream, packet, serial_source, session_logger, evaluate, report_test, collect; print('OK', config.CH_EMG, config.CH_EEG)"
exit /b %errorlevel%

:build
call :need_cli
if errorlevel 1 exit /b 1
if "%~2"=="" goto usage_build
if not exist "%ROOT%firmware\%~2\%~2.ino" goto nosketch
arduino-cli compile --fqbn %FQBN% "%ROOT%firmware\%~2"
exit /b %errorlevel%

:up
call :need_cli
if errorlevel 1 exit /b 1
if "%~2"=="" goto usage_up
if "%~3"=="" goto usage_up
if not exist "%ROOT%firmware\%~2\%~2.ino" goto nosketch
call :confirm_board
if errorlevel 1 exit /b 1
arduino-cli compile --upload -p %~3 --fqbn %FQBN% "%ROOT%firmware\%~2"
exit /b %errorlevel%

:peek
if "%~2"=="" goto usage_peek
set "SEC=%~3"
if "%SEC%"=="" set "SEC=20"
set "BAUD=%~4"
if "%BAUD%"=="" set "BAUD=115200"
python "%ROOT%serial_peek.py" %~2 %SEC% %BAUD%
exit /b %errorlevel%

:mic
call :need_cli
if errorlevel 1 exit /b 1
if "%~2"=="" goto usage_mic
if not exist "%ROOT%firmware\mic_check\mic_check.ino" goto nosketch
call :confirm_board
if errorlevel 1 exit /b 1
arduino-cli compile --upload -p %~2 --fqbn %FQBN% "%ROOT%firmware\mic_check"
if errorlevel 1 exit /b 1
echo Upload done. Reading the board output for 45 s ...
timeout /t 3 /nobreak >nul
python "%ROOT%serial_peek.py" %~2 45 115200
exit /b %errorlevel%

:stream
if "%~2"=="" goto usage_stream
cd /d "%ROOT%"
python check_stream.py %~2 10
exit /b %errorlevel%

:clench
if "%~2"=="" goto usage_clench
call :confirm_body
if errorlevel 1 exit /b 1
cd /d "%ROOT%"
python emg_clench_test.py %~2
exit /b %errorlevel%

:run
if "%~2"=="" goto usage_run
call :confirm_body
if errorlevel 1 exit /b 1
cd /d "%ROOT%"
python report_test.py %~2
exit /b %errorlevel%

:collect
if "%~2"=="" goto usage_collect
call :confirm_body
if errorlevel 1 exit /b 1
cd /d "%ROOT%"
python collect.py %~2 %~3 %~4 %~5 %~6
exit /b %errorlevel%

:need_cli
where arduino-cli >nul 2>nul
if errorlevel 1 goto nocli
exit /b 0

:nocli
echo arduino-cli was not found.
echo Install it with:  winget install ArduinoSA.CLI   then open a NEW Command Prompt window.
exit /b 1

:confirm_board
echo.
echo   SAFETY: this writes a program to the board.
echo   Electrodes must be OFF your body, and the Serial Monitor must be CLOSED.
set "OK="
set /p OK=  Type YES to continue: 
if /i "%OK%"=="YES" exit /b 0
echo Cancelled.
exit /b 1

:confirm_body
echo.
echo   SAFETY: electrodes will be on your body.
echo   1) PC - USB isolator - ESP32 is connected   2) the laptop is on battery (charger unplugged)
echo   3) no stimulation wires are connected       4) the Serial Monitor is CLOSED
set "OK="
set /p OK=  Type YES to continue: 
if /i "%OK%"=="YES" exit /b 0
echo Cancelled.
exit /b 1

:usage_build
echo Usage: fw build NAME      (example: fw build mic_check)
exit /b 1
:usage_up
echo Usage: fw up NAME COM4    (example: fw up cogoaltis_fw2 COM4)
exit /b 1
:usage_peek
echo Usage: fw peek COM4 [seconds] [baud]
exit /b 1
:usage_mic
echo Usage: fw mic COM4
exit /b 1
:usage_stream
echo Usage: fw stream COM4
exit /b 1
:usage_clench
echo Usage: fw clench COM4
exit /b 1
:usage_run
echo Usage: fw run COM4
exit /b 1
:usage_collect
echo Usage: fw collect COM4 [--minutes 60] [--no-gui]
exit /b 1
:nosketch
echo Sketch folder or .ino not found under firmware (check the NAME).
exit /b 1
