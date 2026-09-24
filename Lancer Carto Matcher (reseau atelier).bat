@echo off
chcp 65001 >nul
title Carto Matcher - Reseau atelier
cd /d "%~dp0"

echo ============================================================
echo   Carto Matcher - mode RESEAU ATELIER (LAN)
echo ============================================================
echo.
echo   Ce mode rend l'outil accessible depuis les AUTRES postes
echo   de ton reseau local. La base reste sur CE poste et n'est
echo   PAS exposee sur internet.
echo.

rem --- trouver Python (py ou python) ---
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo   Python n'est pas installe ou pas dans le PATH.
  echo   Installe-le depuis https://www.python.org/downloads/
  echo   ^(coche "Add Python to PATH"^), puis relance ce fichier.
  pause
  exit /b 1
)

rem --- installer Flask si absent ---
%PY% -m flask --version >nul 2>nul
if errorlevel 1 (
  echo   Installation de Flask ^(une seule fois^)...
  %PY% -m pip install flask
)

rem --- autoriser le port 5000 dans le pare-feu Windows (une seule fois) ---
rem    (necessite un clic "Oui" si Windows demande les droits administrateur)
netsh advfirewall firewall show rule name="Carto Matcher 5000" >nul 2>nul
if errorlevel 1 (
  echo   Ouverture du port 5000 dans le pare-feu Windows...
  netsh advfirewall firewall add rule name="Carto Matcher 5000" dir=in action=allow protocol=TCP localport=5000 >nul 2>nul
  if errorlevel 1 (
    echo   ^(Impossible d'ajouter la regle pare-feu automatiquement.^)
    echo   Si les autres postes n'arrivent pas a se connecter, lance ce
    echo   fichier en tant qu'administrateur, ou autorise le port 5000 a la main.
  )
)

rem --- afficher l'adresse IP de ce poste ---
echo.
echo   Adresse(s) de CE poste sur le reseau local :
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo       http://%%a:5000
echo.
echo   Sur les AUTRES postes : ouvre l'une de ces adresses dans le navigateur.
echo   Garde cette fenetre ouverte. Pour arreter : ferme-la ou Ctrl+C.
echo.

rem --- mode reseau : ecoute sur toutes les interfaces ---
set "CARTO_HOST=0.0.0.0"
%PY% app.py

echo.
echo   Le serveur s'est arrete.
pause
