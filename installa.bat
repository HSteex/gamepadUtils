@echo off
rem Installa AppUtili (exe + librerie) al posto della versione in uso e
rem registra l'avvio con Windows. Si auto-eleva ad amministratore.
rem Funziona dal repo (dopo build.bat, prende dist\AppUtili) e dal pacchetto
rem di pacchetto.bat (cartella AppUtili accanto a questo file). Se accanto c'e'
rem AppUtili.cer, il certificato con cui e' firmato, lo rende attendibile.
setlocal

set "SRC_DIR=%~dp0AppUtili"
if not exist "%SRC_DIR%\AppUtili.exe" set "SRC_DIR=%~dp0dist\AppUtili"
set "SRC=%SRC_DIR%\AppUtili.exe"
set "CER=%~dp0AppUtili.cer"
rem Program Files e' obbligatorio: Windows avvia un exe con UIAccess solo
rem da una cartella protetta (vedi certificato.ps1). Protegge anche le
rem librerie in _internal, caricate da un programma che gira da amministratore
set "DEST_DIR=%ProgramFiles%\AppUtili"
set "OLD_DIR=%ProgramFiles%\AppUtili.old"
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

if exist "%CER%" (
    echo Certificato di chi ha firmato AppUtili: lo rendo attendibile su questo PC...
    certutil -f -addstore Root "%CER%" >nul || goto :errore
    certutil -f -addstore TrustedPublisher "%CER%" >nul || goto :errore
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
rem attesa che Windows rilasci i file
timeout /t 2 /nobreak >nul

echo [2/5] Backup della versione precedente...
if exist "%DEST_DIR%" (
    if exist "%OLD_DIR%" rmdir /s /q "%OLD_DIR%" || goto :errore
    move "%DEST_DIR%" "%OLD_DIR%" >nul || goto :errore
    echo       salvata in "%OLD_DIR%"
)

echo [3/5] Copia della nuova versione...
rem robocopy: codici di uscita da 0 a 7 = riuscito
robocopy "%SRC_DIR%" "%DEST_DIR%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :errore
rem file estratti da uno zip scaricato: via il segno "da Internet", che
rem farebbe intervenire SmartScreen all'avvio automatico
powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%DEST_DIR%' -Recurse -File | Unblock-File"

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
