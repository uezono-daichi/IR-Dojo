#!/bin/bash
# IR Dojo をこの機械で起動する。**127.0.0.1 でしか動きません。**
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 が見つかりません。"
  echo "https://www.python.org/downloads/ から入れてから、もう一度開いてください。"
  read -r -p "Enter で閉じます" _; exit 1
fi

if [ ! -d .venv ]; then
  echo "== 初回の準備をします（1〜2分）=="
  python3 -m venv .venv || { read -r -p "失敗しました。Enter で閉じます" _; exit 1; }
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -e . || { read -r -p "失敗しました。Enter で閉じます" _; exit 1; }
fi

echo "== 起動します。ブラウザが開きます =="
echo "   終わるときは、この画面で Ctrl+C"
./.venv/bin/python -m irdojo
read -r -p "終了しました。Enter で閉じます" _
