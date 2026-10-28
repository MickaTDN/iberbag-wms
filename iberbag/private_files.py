"""Restrict new backup folders before any sensitive file is written."""
import os
from pathlib import Path
import subprocess

WINDOWS_ACL=r'''
$ErrorActionPreference='Stop'
try {
$stage='identity'
$p=$env:IBERBAG_PRIVATE_FOLDER
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$stage='path'
if ((Get-Item -LiteralPath $p).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point' }
if ($env:IBERBAG_SET_ACL -eq '1') {
 $stage='descriptor'
 $acl=New-Object System.Security.AccessControl.DirectorySecurity
 $acl.SetSecurityDescriptorSddlForm("D:P(A;OICI;FA;;;$sid)(A;OICI;FA;;;SY)",[System.Security.AccessControl.AccessControlSections]::Access)
 $stage='set-acl'
 Set-Acl -LiteralPath $p -AclObject $acl
}
$stage='verify'
$acl=Get-Acl -LiteralPath $p
if (!$acl.AreAccessRulesProtected) { throw 'Inherited ACL' }
foreach ($r in $acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])) {
 if ($r.AccessControlType -ne 'Allow' -or $r.IdentityReference.Value -notin @($sid,'S-1-5-18')) { throw 'Unexpected ACL' }
}
} catch { [Console]::Error.WriteLine('IBERBAG_ACL_STAGE='+$stage); exit 1 }
'''


def private_folder(folder,create=False):
    path=Path(folder).absolute()
    if path.resolve()!=path: raise ValueError('Pasta privada não pode usar symlinks/junctions.')
    if create: path.mkdir(parents=True,exist_ok=False,mode=0o700)
    if os.name=='nt':
        command=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(command),'-NoProfile','-NonInteractive','-Command',WINDOWS_ACL],
            env=dict(os.environ,IBERBAG_PRIVATE_FOLDER=str(path),IBERBAG_SET_ACL='1' if create else '0'),capture_output=True)
        if result.returncode:
            stage='unknown'
            for value in result.stderr.decode('ascii',errors='ignore').splitlines():
                candidate=value.removeprefix('IBERBAG_ACL_STAGE=')
                if candidate in ('identity','path','descriptor','set-acl','verify'): stage=candidate
            raise ValueError('ACL Windows privada não confirmada ('+stage+'). Nenhum backup/restauro iniciado.')
    elif path.stat().st_uid!=os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError('Pasta privada deve pertencer ao utilizador e ter permissões 0700.')
    return path


def owned_file(folder,name):
    path=Path(folder)/name
    if path.is_symlink() or path.resolve()!=path.absolute(): raise ValueError('Ficheiro de backup não pode ser ligação externa.')
    return path
