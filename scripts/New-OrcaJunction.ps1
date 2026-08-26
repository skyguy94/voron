<#
.SYNOPSIS
    Points %APPDATA%\OrcaSlicer at this repo with a directory junction.

.DESCRIPTION
    OrcaSlicer only reads its configuration from %APPDATA%\OrcaSlicer and that's
    annoying if you want to check your configuration into source control.

    This script creates a junction from %APPDATA%\OrcaSlicer to the repo of your 
    choice via a junction which can be created without elevation or Developer Mode.

    Close OrcaSlicer first; it rewrites its configuration on exit.

.PARAMETER RepairPaths
    Rewrite the machine presets' absolute paths to match this repo's location.

.PARAMETER RepoRoot
    The root of the repo to link to. Defaults to the script's parent folder.

.PARAMETER LinkPath
    The OrcaSlicer configuration path. The default should be sufficient.

.EXAMPLE
    .\New-OrcaJunction.ps1

.EXAMPLE
    .\New-OrcaJunction.ps1 -RepairPaths
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string] $RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string] $LinkPath = (Join-Path $Env:APPDATA 'OrcaSlicer'),
    [switch] $RepairPaths
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$target = Join-Path $RepoRoot 'OrcaSlicer'

if (-not (Test-Path -LiteralPath $target)) {
    throw "No OrcaSlicer directory in the repo at $target."
}

if (Get-Process -Name '*orca*' -ErrorAction SilentlyContinue) {
    throw 'OrcaSlicer is running. Close it first; it rewrites its configuration on exit.'
}

$existing = Get-Item -LiteralPath $LinkPath -Force -ErrorAction SilentlyContinue

if ($existing -and $existing.LinkType) {
    $current = @($existing.Target)[0].TrimEnd('\')
    if ($current -eq $target.TrimEnd('\')) {
        Write-Host "Already linked: $LinkPath -> $target" -ForegroundColor DarkGray
    }
    elseif ($PSCmdlet.ShouldProcess($LinkPath, "Repoint $($existing.LinkType) from $current")) {
        Remove-Item -LiteralPath $LinkPath -Force
        $null = New-Item -ItemType Junction -Path $LinkPath -Target $target
        Write-Host "Repointed : $LinkPath -> $target" -ForegroundColor Green
    }
}
elseif ($existing) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = "$LinkPath.pre-junction-$stamp"
    if ($PSCmdlet.ShouldProcess($LinkPath, "Move aside to $backup, then junction")) {
        Move-Item -LiteralPath $LinkPath -Destination $backup
        $null = New-Item -ItemType Junction -Path $LinkPath -Target $target
        Write-Host "Linked    : $LinkPath -> $target" -ForegroundColor Green
        Write-Warning "Existing configuration moved to $backup. Delete it once OrcaSlicer looks right."
    }
}
elseif ($PSCmdlet.ShouldProcess($LinkPath, "Junction to $target")) {
    $null = New-Item -ItemType Junction -Path $LinkPath -Target $target
    Write-Host "Linked    : $LinkPath -> $target" -ForegroundColor Green
}

if (-not $RepairPaths) {
    Write-Host ''
    Write-Host 'Next: run OrcaSlicer once and tick the Voron and Orca Filament Library'
    Write-Host 'vendors in the wizard. The vendor bundle is not tracked, and without it'
    Write-Host 'the printer thumbnail falls back to the placeholder.'
    return
}

$bedDir = (Join-Path $target 'bed').Replace('\', '/')
$machineDir = Join-Path $target 'user\default\machine'
$changed = 0

foreach ($file in Get-ChildItem -Path $machineDir -Filter '*.json' -File) {
    $json = Get-Content -LiteralPath $file.FullName -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
    $dirty = $false

    foreach ($key in @('bed_custom_model', 'bed_custom_texture')) {
        if (-not $json.Contains($key)) { continue }
        $value = [string] $json[$key]
        if (-not $value) { continue }
        $wanted = "$bedDir/$(Split-Path -Leaf ($value.Replace('/', '\')))"
        if ($value -ne $wanted) {
            Write-Host "  $($file.BaseName): $key"
            Write-Host "      $value"
            Write-Host "   -> $wanted"
            $json[$key] = $wanted
            $dirty = $true
        }
    }

    if ($json.Contains('printhost_cafile')) {
        $ca = [string] $json['printhost_cafile']
        if ($ca -and -not (Test-Path -LiteralPath $ca)) {
            Write-Warning "$($file.BaseName): printhost_cafile does not resolve: $ca"
        }
    }

    if ($dirty) {
        $keys = [string[]] @($json.Keys)
        [Array]::Sort($keys, [StringComparer]::Ordinal)
        $ordered = [ordered]@{}
        foreach ($k in $keys) { $ordered[$k] = $json[$k] }
        $text = ($ordered | ConvertTo-Json -Depth 32) -split "`r?`n" |
        ForEach-Object { $_ -replace '^( +)', '$1$1' }
        if ($PSCmdlet.ShouldProcess($file.FullName, 'Rewrite absolute paths')) {
            [System.IO.File]::WriteAllText($file.FullName, (($text -join "`n") + "`n"),
                [System.Text.UTF8Encoding]::new($false))
            $changed++
        }
    }
}

Write-Host ''
Write-Host "Repaired  : $changed machine preset(s)" -ForegroundColor Green
