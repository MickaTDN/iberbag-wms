param([string]$Destination=[Environment]::GetFolderPath('DesktopDirectory'))
$ErrorActionPreference='Stop'
try {
    $root=Split-Path $PSScriptRoot
    $batch=Join-Path $root 'INSTALAR_DDN_ENSAIO.bat'
    if (-not (Test-Path -LiteralPath $batch -PathType Leaf)) { throw 'Package missing' }
    $link=Join-Path $Destination 'DDN WMS - Assistente de ensaio.lnk'
    if (Test-Path -LiteralPath $link) { throw 'Existing shortcut preserved' }
    if ((Read-Host 'Criar atalho pessoal sem elevacao? Escreva CRIAR ATALHO') -cne 'CRIAR ATALHO') { exit 0 }
    $shell=New-Object -ComObject WScript.Shell
    $shortcut=$shell.CreateShortcut($link)
    $shortcut.TargetPath=Join-Path $env:SystemRoot 'System32\cmd.exe'
    $shortcut.Arguments='/d /s /c ""'+$batch+'""'
    $shortcut.WorkingDirectory=$root
    $shortcut.Description='DDN WMS - ensaio; nao inicia servicos administrativos automaticamente'
    $shortcut.Save()
} catch { Write-Host 'Atalho nao criado; atalhos existentes preservados.';exit 1 }
