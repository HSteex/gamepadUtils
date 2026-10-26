@echo off
rem Installa dist\AppUtili.exe al posto della versione in uso e
rem registra l'avvio con Windows. Si auto-eleva ad amministratore.
setlocal

set "SRC=%~dp0dist\AppUtili.exe"
rem Program Files e' obbligatorio: Windows avvia un exe con UIAccess solo
rem da una cartella protetta (vedi certificato.ps1)
set "DEST_DIR=%ProgramFiles%\AppUtili"
set "DEST=%DEST_DIR%\AppUtili.exe"
set "TASK=AppUtili"

rem --- servono i privilegi di amministratore (AppUtili gira elevata) ---
net session >nul 2>&1
if errorlevel 1 (
    echo Richiesta privilegi di amministratore...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

if not exist "%SRC%" (
    echo Non trovo "%SRC%": lancia prima build.bat
    goto :errore
)

powershell -NoProfile -Command "if ((Get-AuthenticodeSignature '%SRC%').Status -ne 'Valid') { exit 1 }"
if errorlevel 1 (
    echo "%SRC%" non ha una firma valida: Windows non lo avvierebbe.
    echo Lancia certificato.ps1 come amministratore, poi di nuovo build.bat.
    goto :errore
)

echo [1/5] Chiusura della versione in esecuzione...
schtasks /End /TN "%TASK%" >nul 2>&1
taskkill /IM AppUtili.exe /F >nul 2>&1
rem attesa che Windows rilasci il file dell'exe
timeout /t 2 /nobreak >nul

echo [2/5] Backup della versione precedente...
if exist "%DEST%" (
    copy /Y "%DEST%" "%DEST%.old" >nul || goto :errore
    echo       salvata in "%DEST%.old"
)

echo [3/5] Copia della nuova versione...
if not exist "%DEST_DIR%" mkdir "%DEST_DIR%"
copy /Y "%SRC%" "%DEST%" >nul || goto :errore

echo [4/5] Registrazione avvio con Windows...
rem sostituisce anche l'attivita' "AppUtili" creata a mano in precedenza
schtasks /Delete /TN "%TASK%" /F >nul 2>&1
start "" /wait "%DEST%" --abilita-avvio
if errorlevel 1 goto :errore

echo [5/5] Avvio di AppUtili...
schtasks /Run /TN "%TASK%" >nul || goto :errore

echo.
echo Fatto. AppUtili e' installata e partira' a ogni accesso a Windows.
pause
exit /b 0

:errore
echo.
echo ERRORE: installazione non completata, vedi i messaggi sopra.
pause
exit /b 1
