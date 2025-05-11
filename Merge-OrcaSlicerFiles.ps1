$orcaSlicerPath = "${Env:APPDATA}\OrcaSlicer"
$userMachinePath = Join-Path -Path $orcaSlicerPath -ChildPath 'user\default\machine'
$systemVoronPath = Join-Path -Path $orcaSlicerPath -ChildPath 'system\Voron\machine'

$machines = Get-ChildItem -Path $userMachinePath -Filter '*.json'
$machines | ForEach-Object {
    $machine = Get-Content $_.FullName -Raw | ConvertFrom-Json -AsHashtable
    Write-Host "Processing $($machine['name'])"

    #$current = $machine['inherits']
    while ($current) {
        $inheritedPath = Join-Path -Path $systemVoronPath "$current.json"
        $inherited = Get-Content $inheritedPath | ConvertFrom-Json -AsHashtable
    
        $machine.Remove('inherits')
        foreach ($key in $inherited.Keys) {
            if ($machine[$key]) {
                Write-Debug "Key $key already exists in machine, skipping."
                continue
            }
        
            $machine[$key] = $inherited[$key]
        }
        
        $current = $inherited['inherits']
    }

    $machine | Sort-Object -Property Name | ConvertTo-Json -Depth 10 | Set-Content "$userMachinePath\$($machine['name']) (Merged).json"
    #$machine.Keys
}

$machine = Get-Content '.\OrcaSlicer\user\default\machine\VT.1399 ERCF.json' | ConvertFrom-Json -AsHashtable


