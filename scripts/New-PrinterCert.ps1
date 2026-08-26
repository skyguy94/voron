<#
.SYNOPSIS
    Issues printer TLS certificates from a local name-constrained root CA.

.DESCRIPTION
    This cert is used to create and install a root ca cert that it can also
    use to generate leaf certificates for encrypting 3d printer traffic.

    Requires openssl, which ships with Git for Windows.

.PARAMETER PrinterName
    The printer's short host name. Combined with -Domain to make the certificate
    name, so trident becomes trident.lan.

.PARAMETER Domain
    The DNS suffix the printers live under. Defaults to lan.

.PARAMETER ExtraDns
    Extra DNS names for the subject alternative name.

.PARAMETER IPAddress
    Addresses for the subject alternative name.

.PARAMETER Days
    How long a leaf certificate lasts. Defaults to 825 days.

.PARAMETER InitCa
    Create the root CA. Prompts for a passphrase.

.PARAMETER CaYears
    How long the root CA lasts. Defaults to 10 years.

.PARAMETER TrustCa
    Install the root CA into the Windows certificate store.

.PARAMETER Machine
    Trust machine-wide instead of per-user. Requires elevation.

.PARAMETER ProtectCaKey
    Add a passphrase to a CA key that doesn't have one.

.PARAMETER CaPassword
    The CA passphrase, for scripted use. Prompts when omitted.

.PARAMETER PkiRoot
    Where keys and certificates are written. Deliberately outside the repo.

.PARAMETER CaCommonName
    The root CA's common name.

.PARAMETER ConfigFile
    The openssl config holding the certificate extensions. The default should be
    sufficient.

.EXAMPLE
    .\New-PrinterCert.ps1 -InitCa -TrustCa

.EXAMPLE
    .\New-PrinterCert.ps1 -ProtectCaKey

.EXAMPLE
    .\New-PrinterCert.ps1 -PrinterName trident -IPAddress 192.168.1.26 -ExtraDns trident, trident.local

.EXAMPLE
    .\New-PrinterCert.ps1 -PrinterName delta -ExtraDns delta, delta.local
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Position = 0)]
    [string] $PrinterName,
    [string] $Domain = 'lan',
    [string[]] $ExtraDns = @(),
    [string[]] $IPAddress = @(),
    [int] $Days = 825,
    [switch] $InitCa,
    [int] $CaYears = 10,
    [switch] $TrustCa,
    [switch] $Machine,
    [switch] $ProtectCaKey,
    [securestring] $CaPassword,
    [string] $PkiRoot = 'D:\Projects\voron-mods\pki',
    [string] $CaCommonName = 'Voron Home CA',
    [string] $ConfigFile = (Join-Path $PSScriptRoot 'printer-cert.cnf')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$CaCertPath = Join-Path $PkiRoot 'voron-home-ca.crt'
$CaKeyPath = Join-Path $PkiRoot 'voron-home-ca.key'

function Test-EncryptedKey {
    param([string] $Path)

    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $head = Get-Content -LiteralPath $Path -TotalCount 3 -ErrorAction SilentlyContinue
    return [bool] ($head -match 'ENCRYPTED')
}

function Set-CaPasswordEnv {
    param([securestring] $Password, [string] $Prompt)

    if (-not $Password) {
        $Password = Read-Host -Prompt $Prompt -AsSecureString
    }
    $plain = [System.Net.NetworkCredential]::new('', $Password).Password
    if (-not $plain) { throw 'Empty passphrase.' }
    $Env:VORON_CA_PASS = $plain
}

function Get-OpenSsl {
    $onPath = Get-Command openssl.exe -ErrorAction SilentlyContinue
    $candidates = @(
        if ($onPath) { $onPath.Source }
        "$Env:ProgramFiles\Git\mingw64\bin\openssl.exe"
        "${Env:ProgramFiles(x86)}\Git\mingw64\bin\openssl.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    throw 'openssl not found. It ships with Git for Windows; install Git or put openssl on PATH.'
}

function Invoke-OpenSsl {
    param([string[]] $Arguments)

    $output = & $script:OpenSsl @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "openssl $($Arguments[0]) failed:`n$($output -join "`n")"
    }
    return $output
}

$script:OpenSsl = Get-OpenSsl

if (-not (Test-Path -LiteralPath $ConfigFile)) {
    throw "Missing extension config: $ConfigFile"
}

$Env:VORON_CN = $CaCommonName
$Env:VORON_SAN = 'DNS:unused.invalid'

if (-not (Test-Path -LiteralPath $PkiRoot)) {
    if ($PSCmdlet.ShouldProcess($PkiRoot, 'Create PKI directory')) {
        $null = New-Item -ItemType Directory -Path $PkiRoot -Force
    }
}

if ($InitCa) {
    if ((Test-Path -LiteralPath $CaCertPath) -and
        -not $PSCmdlet.ShouldProcess($CaCertPath, 'Overwrite existing CA')) {
        throw "CA already exists at $CaCertPath."
    }

    if ($PSCmdlet.ShouldProcess($CaCertPath, 'Create root CA')) {
        $Env:VORON_CN = $CaCommonName
        Set-CaPasswordEnv -Password $CaPassword -Prompt 'Passphrase for the new CA key'
        Invoke-OpenSsl @(
            'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:4096',
            '-aes-256-cbc', '-pass', 'env:VORON_CA_PASS', '-out', $CaKeyPath
        ) | Out-Null

        Invoke-OpenSsl @(
            'req', '-x509', '-new', '-key', $CaKeyPath, '-passin', 'env:VORON_CA_PASS',
            '-sha256', '-days', ($CaYears * 365),
            '-config', $ConfigFile, '-extensions', 'v3_ca', '-out', $CaCertPath
        ) | Out-Null

        $Env:VORON_CA_PASS = $null
        $expiry = (Invoke-OpenSsl @('x509', '-in', $CaCertPath, '-noout', '-enddate')) -replace '^notAfter='
        Write-Host "Root CA   : CN=$CaCommonName" -ForegroundColor Green
        Write-Host "  expires : $expiry"
        Write-Host "  cert    : $CaCertPath"
        Write-Host "  key     : $CaKeyPath"
    }
}

if ($ProtectCaKey) {
    if (-not (Test-Path -LiteralPath $CaKeyPath)) {
        throw "No CA key at $CaKeyPath."
    }
    if (Test-EncryptedKey $CaKeyPath) {
        Write-Host "CA key is already encrypted." -ForegroundColor DarkGray
    }
    elseif ($PSCmdlet.ShouldProcess($CaKeyPath, 'Encrypt CA key with a passphrase')) {
        Set-CaPasswordEnv -Password $CaPassword -Prompt 'New passphrase for the CA key'
        $tmp = "$CaKeyPath.protecting"
        Invoke-OpenSsl @(
            'pkey', '-in', $CaKeyPath, '-aes-256-cbc',
            '-passout', 'env:VORON_CA_PASS', '-out', $tmp
        ) | Out-Null
        Move-Item -LiteralPath $tmp -Destination $CaKeyPath -Force
        $Env:VORON_CA_PASS = $null
        Write-Host "Encrypted : $CaKeyPath (AES-256)" -ForegroundColor Green
        Write-Host "  The key material is unchanged, so the CA certificate, its thumbprint,"
        Write-Host "  every issued leaf and any existing trust all remain valid."
    }
}

if ($TrustCa) {
    $store = if ($Machine) { 'LocalMachine\Root' } else { 'CurrentUser\Root' }
    $ca = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($CaCertPath)
    $already = Get-ChildItem "Cert:\$store" | Where-Object { $_.Thumbprint -eq $ca.Thumbprint }
    if ($already) {
        Write-Host "Already trusted in $store ($($ca.Thumbprint))." -ForegroundColor DarkGray
    }
    elseif ($PSCmdlet.ShouldProcess($store, "Trust $($ca.Subject)")) {
        Import-Certificate -FilePath $CaCertPath -CertStoreLocation "Cert:\$store" | Out-Null
        Write-Host "Trusted   : $($ca.Subject) in $store" -ForegroundColor Green
    }
}

if (-not $PrinterName) {
    if (-not ($InitCa -or $TrustCa -or $ProtectCaKey)) {
        throw 'Specify -PrinterName to issue a certificate, or -InitCa / -TrustCa / -ProtectCaKey.'
    }
    return
}

if (-not (Test-Path -LiteralPath $CaCertPath)) {
    throw "No CA at $CaCertPath. Run with -InitCa first."
}

$fqdn = "$PrinterName.$Domain"
$names = @(@($fqdn) + $ExtraDns | Select-Object -Unique)
$san = (@($names | ForEach-Object { "DNS:$_" }) + @($IPAddress | ForEach-Object { "IP:$_" })) -join ','

$outDir = Join-Path $PkiRoot $PrinterName
$certPath = Join-Path $outDir "$fqdn.crt"
$keyPath = Join-Path $outDir "$fqdn.key"
$csrPath = Join-Path $outDir "$fqdn.csr"
$chainPath = Join-Path $outDir "$fqdn.fullchain.crt"

if (-not $PSCmdlet.ShouldProcess($certPath, "Issue certificate for $fqdn ($san)")) { return }

$null = New-Item -ItemType Directory -Path $outDir -Force

$Env:VORON_CN = $fqdn
$Env:VORON_SAN = $san

Invoke-OpenSsl @(
    'req', '-new', '-newkey', 'rsa:2048', '-noenc', '-sha256',
    '-config', $ConfigFile, '-keyout', $keyPath, '-out', $csrPath
) | Out-Null

$signArgs = @(
    'x509', '-req', '-in', $csrPath, '-sha256', '-days', $Days,
    '-CA', $CaCertPath, '-CAkey', $CaKeyPath, '-CAcreateserial',
    '-extfile', $ConfigFile, '-extensions', 'v3_leaf',
    '-out', $certPath
)

if (Test-EncryptedKey $CaKeyPath) {
    Set-CaPasswordEnv -Password $CaPassword -Prompt "Passphrase for $CaKeyPath"
    $signArgs += @('-passin', 'env:VORON_CA_PASS')
}

Invoke-OpenSsl $signArgs | Out-Null
$Env:VORON_CA_PASS = $null

Remove-Item -LiteralPath $csrPath -Force
Set-Content -LiteralPath $chainPath -Value ((Get-Content -Raw $certPath) + (Get-Content -Raw $CaCertPath)) -NoNewline

$expiry = (Invoke-OpenSsl @('x509', '-in', $certPath, '-noout', '-enddate')) -replace '^notAfter='
Write-Host "Issued    : $fqdn" -ForegroundColor Green
Write-Host "  san     : $san"
Write-Host "  expires : $expiry"
Write-Host "  cert    : $certPath"
Write-Host "  key     : $keyPath"
Write-Host "  chain   : $chainPath"
