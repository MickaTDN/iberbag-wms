# Read-only by default. Elevation is requested visibly by GESTAO_DDN.bat.
param([ValidateSet('Status','Start','Stop','Restart','Firewall','Shortcuts')][string]$Action='Status',
      [string]$Configuration=(Join-Path $env:ProgramFiles 'DDN WMS Ensaio\DDN_SERVICES.json'))
$ErrorActionPreference='Stop'
function Assert-Protected([string]$Path) {
    $item=Get-Item -LiteralPath $Path
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point' }
    $acl=Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected) { throw 'Inherited ACL' }
    $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Untrusted owner' }
    foreach($rule in $acl.Access) {
        $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        $write=[Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
        if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $write) -and $sid -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Writable by another identity' }
    }
}
try {
    Assert-Protected (Split-Path $Configuration)
    Assert-Protected $Configuration
    $config=Get-Content -Raw -LiteralPath $Configuration | ConvertFrom-Json
    if ($config.mode -ne 'homologacao') { throw 'Only rehearsal' }
    $deployment=(Split-Path (Resolve-Path -LiteralPath $Configuration).Path)
    $required=@('DDNWMS-Test-Postgres','DDNWMS-Test-WMS','DDNWMS-Test-HTTPS')
    if (@($config.services).Count -ne 3) { throw 'Three dedicated services required' }
    $seen=@()
    foreach($entry in $config.services) {
        if ($entry.name -notin $required -or $entry.name -in $seen) { throw 'Unknown/duplicate service' }
        $seen+=$entry.name
        $binary=[IO.Path]::GetFullPath($entry.binary)
        if (-not $binary.StartsWith($deployment+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Binary outside DDN deployment' }
        $parent=Split-Path $binary
        while($parent -ne $deployment) { Assert-Protected $parent; $parent=Split-Path $parent }
        Assert-Protected $entry.binary
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $entry.binary).Hash -ne $entry.sha256) { throw 'Binary changed' }
        $service=Get-CimInstance Win32_Service -Filter "Name='$($entry.name)'"
        if (-not $service -or $service.StartMode -ne 'Auto' -or $service.PathName -cne $entry.imagePath -or $service.StartName -cne $entry.account) { throw 'Service identity differs' }
        if ($entry.account -notin @('NT AUTHORITY\LocalService',"NT SERVICE\$($entry.name)")) { throw 'Privileged service account refused' }
        foreach($dependency in (Get-Service -Name $entry.name).RequiredServices) { if ($dependency.Name -notin $required) { throw 'External service dependency' } }
        Write-Host ($entry.name+': '+$service.State+' (estado SCM; nao prova saude da aplicacao)')
    }
    if ($Action -eq 'Status') { exit 0 }
    if ((Read-Host "Confirme $Action apenas DDN: escreva DDN ENSAIO") -cne 'DDN ENSAIO') { exit 0 }
    $order=@('DDNWMS-Test-Postgres','DDNWMS-Test-WMS','DDNWMS-Test-HTTPS')
    if ($Action -in @('Stop','Restart')) {
        foreach($name in @('DDNWMS-Test-HTTPS','DDNWMS-Test-WMS','DDNWMS-Test-Postgres')) {
            Stop-Service -Name $name -ErrorAction Stop
            (Get-Service $name).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30))
        }
    }
    if ($Action -in @('Start','Restart')) {
        foreach($name in $order) {
            Start-Service -Name $name -ErrorAction Stop
            (Get-Service $name).WaitForStatus('Running',[TimeSpan]::FromSeconds(30))
        }
    }
    if ($Action -in @('Firewall','Shortcuts')) {
        $url=[Uri]$config.httpsUrl
        if ($url.Scheme -ne 'https' -or $url.Port -in @(443,5432,8000,55432,8765,8443) -or $url.Port -lt 1024 -or $url.UserInfo) { throw 'Unsafe URL' }
        # TLS verification stays enabled. No cookie, credential or certificate override.
        $health=Invoke-RestMethod -Uri ($url.GetLeftPart([UriPartial]::Authority)+'/health') -TimeoutSec 10
        if ($health.status -ne 'ok' -or $health.application -cne 'DDN WMS') { throw 'Unhealthy endpoint' }
        if ($Action -eq 'Firewall') {
            if (Get-NetFirewallRule -Name 'DDNWMS-Test-HTTPS' -ErrorAction SilentlyContinue) { throw 'Existing firewall rule preserved' }
            $tls=@($config.services | Where-Object name -eq 'DDNWMS-Test-HTTPS')[0]
            New-NetFirewallRule -Name 'DDNWMS-Test-HTTPS' -DisplayName 'DDN WMS ENSAIO HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $url.Port -RemoteAddress LocalSubnet -Profile Private -Program $tls.binary | Out-Null
        } else {
            $path=Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'DDN WMS Ensaio.url'
            if (Test-Path -LiteralPath $path) { throw 'Existing shortcut preserved' }
            [IO.File]::WriteAllText($path,"[InternetShortcut]`r`nURL=$($url.AbsoluteUri)`r`n")
        }
    }
} catch {
    $reason=$_.Exception.Message
    $known=@('Reparse point','Inherited ACL','Untrusted owner','Writable by another identity','Only rehearsal','Three dedicated services required','Unknown/duplicate service','Binary outside DDN deployment','Binary changed','Service identity differs','Privileged service account refused','External service dependency','Unsafe URL','Unhealthy endpoint','Existing firewall rule preserved','Existing shortcut preserved')
    if ($reason -notin $known) { $reason=$_.Exception.GetType().Name }
    Write-Host ('DDN_DIAGNOSTIC:'+ $reason)
    Write-Host 'Operacao DDN recusada/falhou; sem rollback automatico. Ver procedimento tecnico e estado real. Nao alterar a GVA.'
    exit 1
}
