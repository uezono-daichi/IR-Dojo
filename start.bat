@echo off
rem IR Dojo をこの機械で起動する。127.0.0.1 でしか動きません。
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 が見つかりません。
  echo https://www.python.org/downloads/ から入れてから、もう一度開いてください。
  pause & exit /b 1
)

if not exist .venv (
  echo == 初回の準備をします（1〜2分）==
  python -m venv .venv || (echo 失敗しました & pause & exit /b 1)
  .venv\Scripts\pip install --quiet --upgrade pip
  .venv\Scripts\pip install --quiet -e . || (echo 失敗しました & pause & exit /b 1)
)

echo == 起動します。ブラウザが開きます ==
echo    終わるときは、この画面で Ctrl+C
.venv\Scripts\python -m irdojo
pause
