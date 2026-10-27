@echo off
rem Disinstalla AppUtili: chiude l'app, toglie l'avvio con Windows, la cartella
rem in Program Files e il certificato di AppUtili dalle autorita' attendibili.
rem Impostazioni e accessi in %APPDATA%\AppUtili restano: cancellali a mano
rem se non servono piu'. Si auto-eleva ad amministratore.
setlocal

set "DEST_DIR=%ProgramFiles%\AppUtili"
set "OLD_DIR=%ProgramFiles%\AppUtili.old"
set "TASK=AppUtili"
set "CERT_NAME=AppUtili Local Code Signing"

net session >nul 2>&1
if errorlevel 1 (
    echo Richiesta privilegi di amministratore...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo [1/4] Chiusura di AppUtili...
schtasks /End /TN "%TASK%" >nul 2>&1
taskkill /IM AppUtili.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/4] Avvio con Windows...
schtasks /Delete /TN "%TASK%" /F >nul 2>&1

echo [3/4] File del programma...
if exist "%DEST_DIR%" rmdir /s /q "%DEST_DIR%"
if exist "%OLD_DIR%" rmdir /s /q "%OLD_DIR%"

echo [4/4] Certificato...
certutil -delstore Root "%CERT_NAME%" >nul 2>&1
certutil -delstore TrustedPublisher "%CERT_NAME%" >nul 2>&1

echo.
echo Fatto. Restano solo impostazioni e accessi in "%APPDATA%\AppUtili".
pause
