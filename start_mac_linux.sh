#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then echo "Python 3.11+ bulunamadi."; exit 1; fi
if [ ! -x .venv/bin/python ]; then
  echo "Ilk kurulum: sanal ortam olusturuluyor..."
  "$PY" -m venv .venv
fi
if ! cmp -s requirements.txt .venv/installed-requirements.txt; then
  echo "Gerekli paketler kuruluyor..."
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
  cp requirements.txt .venv/installed-requirements.txt
fi
exec .venv/bin/python run.py
