@echo off
chcp 65001 >nul
title Portail client - PRODUCTION (waitress)
cd /d "%~dp0"

echo ============================================================
echo   Portail client - mode PRODUCTION
echo ============================================================
echo.
echo   Ce mode utilise un serveur adapte a une exposition
echo   continue (waitress) et ecoute sur le reseau. A utiliser
echo   derriere un tunnel HTTPS ou un reverse proxy - voir la
echo   section "Mise en ligne du portail" du README.
echo.

set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo   Python n'est pas installe ou pas dans le PATH.
  pause
  exit /b 1
)

%PY% -c "import flask" >nul 2>nul
if errorlevel 1 ( echo   Installation de Flask... & %PY% -m pip install flask )
%PY% -c "import waitress" >nul 2>nul
if errorlevel 1 ( echo   Installation de waitress ^(serveur production^)... & %PY% -m pip install waitress )

rem --- pare-feu : port 5001 ---
netsh advfirewall firewall show rule name="Portail client 5001" >nul 2>nul
if errorlevel 1 (
  netsh advfirewall firewall add rule name="Portail client 5001" dir=in action=allow protocol=TCP localport=5001 >nul 2>nul
)

echo   Lancement du portail en production sur le port 5001.
echo   Garde cette fenetre ouverte. Pour arreter : Ctrl+C.
echo.

set "PORT=5001"
set "CARTO_HOST=0.0.0.0"
set "CARTO_PROD=1"
%PY% portal.py

echo.
echo   Le portail s'est arrete.
pause
