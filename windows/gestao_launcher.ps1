$ErrorActionPreference='Stop'
# Use OS modules only; do not inherit a PowerShell 7/GVA/user module search path.
$env:PSModulePath=[IO.Path]::Combine($PSHOME,'Modules')
try {
    # Never elevate a script from Downloads or another user-writable extraction.
    $root=Join-Path $env:ProgramFiles 'DDN WMS Ensaio'
    $manager=Join-Path $root 'windows\gestao.ps1'
    foreach($path in @($root,(Join-Path $root 'windows'),$manager)) {
        $item=Get-Item -LiteralPath $path
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point' }
        $acl=Get-Acl -LiteralPath $path
        if (-not $acl.AreAccessRulesProtected) { throw 'Unprotected code' }
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Untrusted owner' }
        foreach($rule in $acl.Access) {
            $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
            $write=[Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
            if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $write) -and $sid -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Writable code' }
        }
    }
    Write-Host '1 Estado / 2 Iniciar / 3 Parar / 4 Reiniciar / 5 Firewall / 6 Atalho navegador'
    $choices=@{'1'='Status';'2'='Start';'3'='Stop';'4'='Restart';'5'='Firewall';'6'='Shortcuts'}
    $action=$choices[(Read-Host 'Opcao')]
    if (-not $action) { exit 0 }
    $arguments='-NoProfile -File "'+$manager+'" -Action '+$action
    $engine=[IO.Path]::Combine([Environment]::SystemDirectory,'WindowsPowerShell\v1.0\powershell.exe')
    $process=Start-Process $engine -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    exit $process.ExitCode
} catch {
    Write-Host 'Gestao indisponivel. Requer implantacao DDN protegida em Program Files e aprovacao UAC. Nao executar como administrador a copia em Downloads.'
    exit 1
}
