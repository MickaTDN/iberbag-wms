"""Restrict new backup folders before any sensitive file is written."""
import os
import re
from pathlib import Path
import subprocess

WINDOWS_ACL=r'''
$ErrorActionPreference='Stop'
try {
$stage='module'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$stage='identity'
$p=$env:IBERBAG_PRIVATE_FOLDER
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$stage='path'
if ((Get-Item -LiteralPath $p).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point' }
if ($env:IBERBAG_SET_ACL -eq '1') {
 $stage='descriptor'
 # Preserve owner/group and modify ONLY the DACL of this new owned folder.
 $acl=Get-Acl -LiteralPath $p
 $stage='protect'
 $acl.SetAccessRuleProtection($true,$false)
 $stage='remove-rules'
 foreach ($r in $acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])) {
  $acl.RemoveAccessRuleSpecific($r)
 }
 foreach ($id in @($sid,'S-1-5-18')) {
  $stage='build-rule'
  $identity=[System.Security.Principal.SecurityIdentifier]::new($id)
  $flags=[System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
  $rule=[System.Security.AccessControl.FileSystemAccessRule]::new($identity,[System.Security.AccessControl.FileSystemRights]::FullControl,$flags,[System.Security.AccessControl.PropagationFlags]::None,[System.Security.AccessControl.AccessControlType]::Allow)
  $stage='add-rule'
  $acl.AddAccessRule($rule)
 }
 $stage='set-acl'
 Set-Acl -LiteralPath $p -AclObject $acl
}
$stage='verify'
$acl=Get-Acl -LiteralPath $p
if (!$acl.AreAccessRulesProtected) { throw 'Inherited ACL' }
foreach ($r in $acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])) {
 if ($r.AccessControlType -ne 'Allow' -or $r.IdentityReference.Value -notin @($sid,'S-1-5-18')) { throw 'Unexpected ACL' }
}
} catch { [Console]::Error.WriteLine('IBERBAG_ACL_STAGE='+$stage); [Console]::Error.WriteLine('IBERBAG_ACL_ERROR='+$_.Exception.GetType().Name); exit 1 }
'''


def private_folder(folder,create=False):
    path=Path(folder).absolute()
    if path.resolve()!=path: raise ValueError('Pasta privada não pode usar symlinks/junctions.')
    if create: path.mkdir(parents=True,exist_ok=False,mode=0o700)
    if os.name=='nt':
        command=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        # Do not inherit PowerShell 7/user module paths into Windows PowerShell 5.1.
        env={k:v for k,v in os.environ.items() if k.upper() not in ('PSMODULEPATH','PSMODULEANALYSISCACHEPATH')}
        env.update(IBERBAG_PRIVATE_FOLDER=str(path),IBERBAG_SET_ACL='1' if create else '0',PSModulePath=str(command.parent/'Modules'))
        result=subprocess.run([str(command),'-NoProfile','-NonInteractive','-Command',WINDOWS_ACL],
            env=env,capture_output=True,timeout=30)
        if result.returncode:
            stage='unknown'; kind='permission-error'
            for value in result.stderr.decode('ascii',errors='ignore').splitlines():
                error=value.removeprefix('IBERBAG_ACL_ERROR=')
                if re.fullmatch(r'[A-Za-z]{1,80}(?:Exception|Error)',error): kind=error
                candidate=value.removeprefix('IBERBAG_ACL_STAGE=')
                if candidate in ('module','identity','path','descriptor','protect','remove-rules','build-rule','add-rule','set-acl','verify'): stage=candidate
            raise ValueError('ACL Windows privada não confirmada ('+stage+'; '+kind+'). Nenhum backup/restauro iniciado.')
    elif path.stat().st_uid!=os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError('Pasta privada deve pertencer ao utilizador e ter permissões 0700.')
    return path


def owned_file(folder,name):
    path=Path(folder)/name
    if path.is_symlink() or path.resolve()!=path.absolute(): raise ValueError('Ficheiro de backup não pode ser ligação externa.')
    return path
