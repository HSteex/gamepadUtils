@echo off
rem Compila AppUtili nella cartella dist\AppUtili (exe + librerie in _internal).
rem Niente --onefile: un exe singolo si estrae a ogni avvio in %%TEMP%%, una
rem cartella scrivibile dall'utente, e da li' carica DLL e moduli mentre gira
rem da amministratore. Installata in Program Files, la cartella e' protetta.
cd /d "%~dp0"

echo [1/4] Installazione dipendenze...
python -m pip install -r requirements.txt || goto :errore

echo [2/4] Generazione icona...
python -c "import app; app.save_app_ico('app.ico')" || goto :errore

echo [3/4] Compilazione...
rem --uac-admin: l'exe chiede i privilegi di amministratore
rem --uac-uiaccess: l'overlay puo' comparire sopra i giochi a schermo intero
rem --hidden-import: moduli caricati dinamicamente che PyInstaller non vede
python -m PyInstaller --noconfirm --clean --onedir --windowed --uac-admin ^
    --uac-uiaccess ^
    --name AppUtili --icon app.ico ^
    --hidden-import pystray._win32 ^
    --hidden-import pygame._sdl2.controller ^
    app.py || goto :errore

echo [4/4] Firma digitale (Windows chiede la password della chiave)...
rem Senza firma valida Windows rifiuta di avviare un exe con UIAccess.
rem Il certificato si crea una volta sola con certificato.ps1.
rem Il timestamp tiene valida la firma anche dopo la scadenza del certificato
rem (5 anni); senza internet si firma senza, avvisando.
powershell -NoProfile -Command ^
    "$c = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Subject -eq 'CN=AppUtili Local Code Signing' -and $_.NotAfter -gt (Get-Date) } | Select-Object -First 1;" ^
    "if (-not $c) { Write-Host 'Certificato non trovato: lancia certificato.ps1 come amministratore'; exit 1 };" ^
    "$exe = 'dist\AppUtili\AppUtili.exe';" ^
    "$s = Set-AuthenticodeSignature -FilePath $exe -Certificate $c -HashAlgorithm SHA256 -TimestampServer 'http://timestamp.digicert.com';" ^
    "if ($s.Status -ne 'Valid') { Write-Host '      Timestamp non raggiunto: firmo senza (la firma varra'' fino alla scadenza del certificato)'; $s = Set-AuthenticodeSignature -FilePath $exe -Certificate $c -HashAlgorithm SHA256 };" ^
    "Write-Host ('      ' + $s.Status + ' - ' + $s.StatusMessage); if ($s.Status -ne 'Valid') { exit 1 }" ^
    || goto :errore

echo.
echo Fatto: dist\AppUtili\AppUtili.exe
echo Non si avvia da dist: con UIAccess funziona solo da Program Files.
echo Per installarlo al posto della versione attuale lancia installa.bat,
echo per mandarlo a un'altra persona pacchetto.bat
pause
exit /b 0

:errore
echo.
echo ERRORE durante la compilazione, vedi i messaggi sopra.
pause
exit /b 1
