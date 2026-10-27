@echo off
rem Prepara dist\AppUtili-pacchetto.zip da mandare a un'altra persona: l'app
rem firmata, il certificato PUBBLICO con cui e' firmata (AppUtili.cer, senza
rem chiave privata), installa.bat, disinstalla.bat e LEGGIMI.txt.
rem Va lanciato dopo build.bat.
setlocal
cd /d "%~dp0"
set "PKG=dist\AppUtili-pacchetto"
set "ZIP=dist\AppUtili-pacchetto.zip"

if not exist "dist\AppUtili\AppUtili.exe" (
    echo Non trovo dist\AppUtili\AppUtili.exe: lancia prima build.bat
    goto :errore
)

echo [1/3] Copia dei file...
if exist "%PKG%" rmdir /s /q "%PKG%"
mkdir "%PKG%" || goto :errore
rem robocopy: codici di uscita da 0 a 7 = riuscito
robocopy "dist\AppUtili" "%PKG%\AppUtili" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :errore
for %%f in (installa.bat disinstalla.bat LEGGIMI.txt) do (
    copy /Y "%%f" "%PKG%\" >nul || goto :errore
)

echo [2/3] Certificato pubblico...
rem Deve essere quello con cui e' firmato l'exe, altrimenti chi installa non
rem potrebbe avviarlo: se il certificato e' stato rinnovato va rifatto build.bat
powershell -NoProfile -Command ^
    "$exe = Get-AuthenticodeSignature 'dist\AppUtili\AppUtili.exe';" ^
    "if ($exe.Status -ne 'Valid') { Write-Host 'L''exe non ha una firma valida: rifai build.bat'; exit 1 };" ^
    "Export-Certificate -Cert $exe.SignerCertificate -FilePath '%PKG%\AppUtili.cer' | Out-Null" ^
    || goto :errore

echo [3/3] Zip...
rem tar.exe di Windows crea zip standard (percorsi con "/"). Qualche
rem tentativo: l'antivirus tiene aperti per un attimo i file appena copiati
powershell -NoProfile -Command ^
    "for ($i = 1; $i -le 5; $i++) { if (Test-Path '%ZIP%') { Remove-Item '%ZIP%' };" ^
    "tar.exe -a -c -f '%ZIP%' -C '%PKG%' AppUtili AppUtili.cer installa.bat disinstalla.bat LEGGIMI.txt;" ^
    "if ($LASTEXITCODE -eq 0) { exit 0 }; Start-Sleep -Seconds 2 }; exit 1" ^
    || goto :errore

echo.
echo Fatto: %ZIP%
echo Chi lo riceve: estrae lo zip e lancia installa.bat (vedi LEGGIMI.txt).
pause
exit /b 0

:errore
echo.
echo ERRORE: pacchetto non creato, vedi i messaggi sopra.
pause
exit /b 1
