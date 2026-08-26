<#
.SYNOPSIS
    Flattens OrcaSlicer user presets so each one is self-contained ("fork and modify").

.DESCRIPTION
    The hierarchical dependencies that OrcaSlicer creates from its default profiles
    for printer, process, and filament settings are annoying and require you to keep
    all sorts of system presets around that you absolutely do not care about if
    you do anything custom.

    This script goes through and figures out the final rendered versions of those settings
    and flattens them into a single file.

    Presets that are already flat are left untouched, so the script is idempotent and
    safe to re-run.

.PARAMETER Report
    List every preset with its inheritance chain and exit without writing anything.

.PARAMETER Kind
    Which kinds of preset to flatten. Can be one or all of 'machine', 'process', or 'filament'.

.PARAMETER RepoRoot
    The root of the repo to work on. Defaults to the script's parent folder.

.PARAMETER UserRoot
    Where the user presets live. The default should be sufficient.

.PARAMETER SystemRoot
    Where the system presets live. The default should be sufficient.

.EXAMPLE
    .\Merge-OrcaSlicerFiles.ps1 -Report
.EXAMPLE
    .\Merge-OrcaSlicerFiles.ps1 -WhatIf
.EXAMPLE
    .\Merge-OrcaSlicerFiles.ps1 -Kind process
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string] $RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string] $UserRoot = (Join-Path $RepoRoot 'OrcaSlicer\user\default'),
    [string] $SystemRoot = (Join-Path $RepoRoot 'OrcaSlicer\system'),
    [ValidateSet('machine', 'process', 'filament')]
    [string[]] $Kind = @('machine', 'process', 'filament'),
    [switch] $Report
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$DropKeys = @('inherits', 'instantiation', 'setting_id', 'type', 'sub_path')

$SettingsIdKey = @{
    machine  = 'printer_settings_id'
    process  = 'print_settings_id'
    filament = 'filament_settings_id'
}

function Get-SystemIndex {
    param([string] $Root, [string] $PresetKind)

    $index = @{}
    if (-not (Test-Path $Root)) { return $index }

    Get-ChildItem -Path $Root -Directory | ForEach-Object {
        $vendor = $_.Name
        $dir = Join-Path $_.FullName $PresetKind
        if (Test-Path $dir) {
            Get-ChildItem -Path $dir -Filter '*.json' -File -Recurse | ForEach-Object {
                if (-not $index.ContainsKey($_.BaseName)) {
                    $index[$_.BaseName] = [System.Collections.Generic.List[object]]::new()
                }
                $index[$_.BaseName].Add([pscustomobject]@{ Path = $_.FullName; Vendor = $vendor })
            }
        }
    }
    return $index
}

function Get-Chain {
    param([hashtable] $Preset, [hashtable] $Index)

    $chain = [System.Collections.Generic.List[object]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new()
    $current = if ($Preset.ContainsKey('inherits')) { $Preset['inherits'] } else { '' }
    $vendor = $null

    while (-not [string]::IsNullOrWhiteSpace($current)) {
        if (-not $seen.Add($current)) {
            throw "Inheritance loop at '$current'."
        }

        $hit = $null
        if ($Index.ContainsKey($current)) {
            $candidates = $Index[$current]
            if ($vendor) { $hit = $candidates | Where-Object { $_.Vendor -eq $vendor } | Select-Object -First 1 }
            if (-not $hit) { $hit = $candidates[0] }
        }

        if (-not $hit) {
            $chain.Add([pscustomobject]@{ Name = $current; Path = $null; Config = $null })
            break
        }

        $path = $hit.Path
        $vendor = $hit.Vendor
        $config = Get-Content -LiteralPath $path -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
        $chain.Add([pscustomobject]@{ Name = $current; Path = $path; Config = $config })
        $current = if ($config.ContainsKey('inherits')) { $config['inherits'] } else { '' }
    }
    return , $chain
}

function ConvertTo-OrcaJson {
    param([System.Collections.IDictionary] $Preset)

    $keys = [string[]] @($Preset.Keys)
    [Array]::Sort($keys, [StringComparer]::Ordinal)

    $ordered = [ordered]@{}
    foreach ($key in $keys) { $ordered[$key] = $Preset[$key] }

    $json = $ordered | ConvertTo-Json -Depth 32
    ($json -split "`r?`n" | ForEach-Object { $_ -replace '^( +)', '$1$1' }) -join "`n"
}

function Write-TextLf {
    param([string] $Path, [string] $Text)

    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.UTF8Encoding]::new($false))
}

function Clear-InfoBaseId {
    param([string] $Path)

    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $lines = Get-Content -LiteralPath $Path
    if (-not ($lines -match '^\s*base_id\s*=\s*\S')) { return $false }
    $updated = $lines | ForEach-Object { $_ -replace '^\s*base_id\s*=.*$', 'base_id = ' }
    if ($PSCmdlet.ShouldProcess($Path, 'Clear base_id')) {
        Write-TextLf -Path $Path -Text (($updated -join "`n") + "`n")
    }
    return $true
}

$exitDirty = $false

foreach ($presetKind in $Kind) {
    $dir = Join-Path $UserRoot $presetKind
    if (-not (Test-Path $dir)) {
        Write-Warning "No $presetKind directory under $UserRoot"
        continue
    }

    $index = Get-SystemIndex -Root $SystemRoot -PresetKind $presetKind
    Write-Host "`n== $presetKind ==" -ForegroundColor Cyan

    foreach ($file in Get-ChildItem -Path $dir -Filter '*.json' -File | Sort-Object Name) {
        $preset = Get-Content -LiteralPath $file.FullName -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
        $name = if ($preset.ContainsKey('name')) { $preset['name'] } else { $file.BaseName }
        $chain = Get-Chain -Preset $preset -Index $index

        if ($chain.Count -eq 0) {
            Write-Host ("  {0,-40} flat ({1} keys)" -f $name, $preset.Count) -ForegroundColor DarkGray
            if (-not $Report) { [void](Clear-InfoBaseId -Path ([IO.Path]::ChangeExtension($file.FullName, '.info'))) }
            continue
        }

        $missing = $chain | Where-Object { $null -eq $_.Path }
        $chainText = ($chain | ForEach-Object { if ($_.Path) { $_.Name } else { "$($_.Name) [MISSING]" } }) -join ' -> '

        if ($missing) {
            Write-Host ("  {0,-40} UNRESOLVED: {1}" -f $name, $chainText) -ForegroundColor Red
            $exitDirty = $true
            continue
        }

        Write-Host ("  {0,-40} {1} keys <- {2}" -f $name, $preset.Count, $chainText) -ForegroundColor Yellow
        if ($Report) { continue }

        $merged = [ordered]@{}
        foreach ($key in $preset.Keys) {
            if ($DropKeys -notcontains $key) { $merged[$key] = $preset[$key] }
        }
        foreach ($link in $chain) {
            foreach ($key in $link.Config.Keys) {
                if ($DropKeys -notcontains $key -and -not $merged.Contains($key)) {
                    $merged[$key] = $link.Config[$key]
                }
            }
        }

        $merged['inherits'] = ''
        $merged['from'] = 'User'
        $merged['name'] = $name

        $idKey = $SettingsIdKey[$presetKind]
        if ($presetKind -eq 'filament') {
            $merged[$idKey] = @([string] $name)
        }
        else {
            $merged[$idKey] = [string] $name
        }

        if ($PSCmdlet.ShouldProcess($file.FullName, "Flatten ($($preset.Count) -> $($merged.Count) keys)")) {
            Write-TextLf -Path $file.FullName -Text ((ConvertTo-OrcaJson $merged) + "`n")
            Write-Host ("      -> {0} keys" -f $merged.Count) -ForegroundColor Green
        }
        [void](Clear-InfoBaseId -Path ([IO.Path]::ChangeExtension($file.FullName, '.info')))
    }
}

if ($exitDirty) {
    Write-Host "`nOne or more presets inherit from a base that is not installed. Fix or delete them." -ForegroundColor Red
    exit 1
}
