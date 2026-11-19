# DPAPI LocalMachine + exclusive service SID ACL; administrators/SYSTEM remain trusted.
$env:PSModulePath=[IO.Path]::Combine($PSHOME,'Modules')
Add-Type -AssemblyName System.Security
function Set-DDNPrivateAcl([string]$Path,[string]$Sid,[string]$Rights='Read') {
    $item=Get-Item -LiteralPath $Path -ErrorAction Stop
    if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'DDN_REPARSE'}
    $acl=if($item.PSIsContainer){New-Object Security.AccessControl.DirectorySecurity}else{New-Object Security.AccessControl.FileSecurity}
    $acl.SetAccessRuleProtection($true,$false)
    $admin=New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $acl.SetOwner($admin)
    foreach($id in @('S-1-5-32-544','S-1-5-18',$Sid)) {
        $r=if($id -eq $Sid){$Rights}else{'FullControl'}
        $identity=New-Object Security.Principal.SecurityIdentifier($id)
        $rule=if($item.PSIsContainer){New-Object Security.AccessControl.FileSystemAccessRule($identity,$r,'ContainerInherit,ObjectInherit','None','Allow')}else{New-Object Security.AccessControl.FileSystemAccessRule($identity,$r,'Allow')}
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl -ErrorAction Stop
}
function Assert-DDNSecretAcl([string]$Path,[string]$Sid) {
    $item=Get-Item -LiteralPath $Path -ErrorAction Stop
    if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'DDN_REPARSE'}
    $acl=Get-Acl -LiteralPath $Path
    if(-not $acl.AreAccessRulesProtected -or $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')){throw 'DDN_SECRET_OWNER'}
    $found=$false
    foreach($rule in $acl.Access) {
        $id=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if($rule.AccessControlType -ne 'Allow' -or $id -notin @('S-1-5-18','S-1-5-32-544',$Sid)){throw 'DDN_SECRET_ACL'}
        if($id -eq $Sid){
            $allowed=[Security.AccessControl.FileSystemRights]::Read -bor [Security.AccessControl.FileSystemRights]::Synchronize
            if($item.PSIsContainer){$allowed=$allowed -bor [Security.AccessControl.FileSystemRights]::ExecuteFile}
            if(($rule.FileSystemRights -band (-bnot $allowed)) -ne 0){throw 'DDN_SECRET_WRITE'}
            $found=$true
        }
    }
    if(-not $found){throw 'DDN_SECRET_SID'}
}
function Save-DDNSecret([string]$Path,[Security.SecureString]$Password,[string]$Sid) {
    if(Test-Path -LiteralPath $Path){throw 'DDN_SECRET_EXISTS'}
    Assert-DDNSecretAcl (Split-Path $Path) $Sid
    $bstr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    $clear=$null
    try {
        $clear=[Text.Encoding]::UTF8.GetBytes([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr))
        if($clear.Length -lt 1 -or $clear.Length -gt 4096){throw 'DDN_SECRET_SIZE'}
        $cipher=[Security.Cryptography.ProtectedData]::Protect($clear,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
        $file=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try{$file.Write($cipher,0,$cipher.Length);$file.Flush($true)}finally{$file.Dispose()}
        Set-DDNPrivateAcl $Path $Sid 'Read'
        Assert-DDNSecretAcl $Path $Sid
    } finally {
        if($clear){[Array]::Clear($clear,0,$clear.Length)}
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
function Read-DDNSecret([string]$Path) {
    $account=New-Object Security.Principal.NTAccount('NT SERVICE','DDNWMS-Test-WMS')
    $sid=$account.Translate([Security.Principal.SecurityIdentifier]).Value
    if([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne $sid){throw 'DDN_SERVICE_IDENTITY'}
    Assert-DDNSecretAcl (Split-Path $Path) $sid
    Assert-DDNSecretAcl $Path $sid
    $cipher=[IO.File]::ReadAllBytes($Path)
    if($cipher.Length -gt 16384){throw 'DDN_SECRET_SIZE'}
    return ,([Security.Cryptography.ProtectedData]::Unprotect($cipher,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine))
}
