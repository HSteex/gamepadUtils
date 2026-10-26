# Crea (una volta sola) il certificato con cui build.bat firma AppUtili.exe e
# lo rende attendibile su questo PC. Serve per UIAccess: Windows avvia un exe
# con uiAccess="true" solo se e' firmato con un certificato attendibile e sta
# in Program Files. UIAccess e' cio' che permette all'overlay di comparire
# sopra i giochi a schermo intero.
#
# La chiave privata resta nell'archivio dell'utente e non e' esportabile;
# nell'archivio della macchina va solo la parte pubblica.
#
# Va lanciato da un PowerShell come amministratore:
#   powershell -NoProfile -ExecutionPolicy Bypass -File certificato.ps1

$ErrorActionPreference = 'Stop'
$subject = 'CN=AppUtili Local Code Signing'

$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -eq $subject -and $_.NotAfter -gt (Get-Date) } |
    Select-Object -First 1
if (-not $cert) {
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $subject `
        -CertStoreLocation Cert:\CurrentUser\My -KeyExportPolicy NonExportable `
        -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 `
        -NotAfter (Get-Date).AddYears(5)
    Write-Output "Certificato creato: $($cert.Thumbprint)"
} else {
    Write-Output "Certificato gia' presente: $($cert.Thumbprint)"
}

$cer = Join-Path $env:TEMP 'AppUtiliCodeSigning.cer'
Export-Certificate -Cert $cert -FilePath $cer | Out-Null
foreach ($store in 'Root', 'TrustedPublisher') {
    Import-Certificate -FilePath $cer -CertStoreLocation "Cert:\LocalMachine\$store" | Out-Null
    Write-Output "Reso attendibile in LocalMachine\$store"
}
Remove-Item $cer
