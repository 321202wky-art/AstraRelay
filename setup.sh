#!/usr/bin/env sh
set -eu

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=python
else
  echo "Python 3.9 or newer is required. Install it and run this script again."
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment..."
  "$PYTHON_CMD" -m venv .venv
fi

. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "环境准备就绪，请运行 python app.py"
