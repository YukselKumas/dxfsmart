@echo off
chcp 65001 >nul
cd /d "%~dp0"
title DXFsmart 0.9

rem --- Python bul: once py baslaticisi, sonra python ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python bulunamadi. python.org adresinden Python 3.11+ kurun ve "Add Python to PATH" secenegini isaretleyin.
  pause & exit /b 1
)

rem --- Ilk calistirmada sanal ortam olustur ---
if not exist ".venv\Scripts\python.exe" (
  echo Ilk kurulum: sanal ortam olusturuluyor...
  %PY% -m venv .venv || (echo Sanal ortam olusturulamadi. & pause & exit /b 1)
)

rem --- Paketleri yalnizca requirements.txt degistiyse kur (internetsiz tekrar acilis) ---
fc /b requirements.txt ".venv\installed-requirements.txt" >nul 2>nul
if errorlevel 1 (
  echo Gerekli paketler kuruluyor...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || (echo Paket kurulumu basarisiz. Yukaridaki hata mesajini kontrol edin (internet veya Python surumu). Python 3.12-3.13 onerilir. & pause & exit /b 1)
  copy /y requirements.txt ".venv\installed-requirements.txt" >nul
)

".venv\Scripts\python.exe" run.py
pause
