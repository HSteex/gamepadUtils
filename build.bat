@echo off
rem Compila AppUtili in un singolo exe: dist\AppUtili.exe
cd /d "%~dp0"

echo [1/4] Installazione dipendenze...
python -m pip install -r requirements.txt || goto :errore

echo [2/4] Generazione icona...
python -c "import app; app.save_app_ico('app.ico')" || goto :errore

echo [3/4] Compilazione...
rem --uac-admin: l'exe chiede i privilegi di amministratore
rem --uac-uiaccess: l'overlay puo' comparire sopra i giochi a schermo intero
rem --hidden-import: moduli caricati dinamicamente che PyInstaller non vede
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin ^
    --uac-uiaccess ^
    --name AppUtili --icon app.ico ^
    --hidden-import pystray._win32 ^
    --hidden-import pygame._sdl2.controller ^
    app.py || goto :errore

echo [4/4] Firma digitale...
rem Senza firma valida Windows rifiuta di avviare un exe con UIAccess.
rem Il certificato si crea una volta sola con certificato.ps1.
powershell -NoProfile -Command ^
    "$c = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Subject -eq 'CN=AppUtili Local Code Signing' -and $_.NotAfter -gt (Get-Date) } | Select-Object -First 1;" ^
    "if (-not $c) { Write-Host 'Certificato non trovato: lancia certificato.ps1 come amministratore'; exit 1 };" ^
    "$s = Set-AuthenticodeSignature -FilePath 'dist\AppUtili.exe' -Certificate $c -HashAlgorithm SHA256;" ^
    "Write-Host ('      ' + $s.Status + ' - ' + $s.StatusMessage); if ($s.Status -ne 'Valid') { exit 1 }" ^
    || goto :errore

echo.
echo Fatto: dist\AppUtili.exe
echo Non si avvia da dist: con UIAccess funziona solo da Program Files.
echo Per installarlo al posto della versione attuale lancia installa.bat
pause
exit /b 0

:errore
echo.
echo ERRORE durante la compilazione, vedi i messaggi sopra.
pause
exit /b 1
