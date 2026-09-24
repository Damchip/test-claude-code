@echo off
chcp 65001 >nul
title Carto Matcher
cd /d "%~dp0"

echo ============================================================
echo   Carto Matcher - demarrage
echo ============================================================

rem --- trouver Python (py ou python) ---
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo.
  echo   Python n'est pas installe ou pas dans le PATH.
  echo   Installe-le depuis https://www.python.org/downloads/
  echo   ^(coche "Add Python to PATH" pendant l'installation^), puis relance ce fichier.
  echo.
  pause
  exit /b 1
)

rem --- installer Flask si absent ---
%PY% -m flask --version >nul 2>nul
if errorlevel 1 (
  echo   Installation de Flask ^(une seule fois^)...
  %PY% -m pip install flask
)

rem --- ouvrir le navigateur apres un court delai, en parallele ---
start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:5000"

echo.
echo   Lancement du serveur local. Garde cette fenetre ouverte.
echo   Pour arreter : ferme cette fenetre ou appuie sur Ctrl+C.
echo.
%PY% app.py

echo.
echo   Le serveur s'est arrete.
pause
