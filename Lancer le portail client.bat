@echo off
chcp 65001 >nul
title Portail client - Carto Matcher
cd /d "%~dp0"

echo ============================================================
echo   Portail client - Carto Matcher
echo ============================================================
echo.
echo   Le client depose son fichier et recoit un verdict
echo   (compatible / a verifier / non trouve). La bibliotheque
echo   de solutions n'est JAMAIS exposee.
echo.
echo   Ce portail tourne sur le port 5001 : il peut fonctionner
echo   EN MEME TEMPS que l'outil interne (port 5000).
echo.

set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo   Python n'est pas installe ou pas dans le PATH.
  echo   Installe-le depuis https://www.python.org/downloads/
  pause
  exit /b 1
)

%PY% -m flask --version >nul 2>nul
if errorlevel 1 (
  echo   Installation de Flask ^(une seule fois^)...
  %PY% -m pip install flask
)

start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:5001"

echo   Lancement du portail. Garde cette fenetre ouverte.
echo   Pour arreter : ferme cette fenetre ou Ctrl+C.
echo.

set "PORT=5001"
%PY% portal.py

echo.
echo   Le portail s'est arrete.
pause
