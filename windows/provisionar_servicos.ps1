# Run only by the protected launcher after visible UAC. No production mode.
param([string]$Root=(Join-Path $env:ProgramFiles 'DDN WMS Ensaio'))
$ErrorActionPreference='Stop'
$env:PSModulePath=[IO.Path]::Combine($PSHOME,'Modules')
$entries=@()
function Stop-DDNInstall([string]$Code){throw $Code}
function Read-DDNJson([string]$Path){Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json}
function Write-DDNNew([string]$Path,[string]$Text){
    $stream=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$bytes=[Text.Encoding]::UTF8.GetBytes($Text);$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}
try {
    # Load the already reviewed read-only ownership guard without running its main.
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'gestao.ps1'),[ref]$tokens,[ref]$errors)
    if($errors.Count){Stop-DDNInstall 'DDN_GUARD_INVALID'}
    $definition=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-Protected'},$true)
    Invoke-Expression $definition.Extent.Text
    if([IO.Path]::GetFullPath($Root).TrimEnd('\') -cne (Join-Path $env:ProgramFiles 'DDN WMS Ensaio')){Stop-DDNInstall 'DDN_EXCLUSIVE_ROOT'}
    Assert-Protected $Root
    foreach($item in @(Get-ChildItem -LiteralPath $Root -Recurse -Force)) { Assert-Protected $item.FullName }
    . (Join-Path $PSScriptRoot 'segredo_servico.ps1')
    $settingsPath=Join-Path $Root 'DDN_LOCAL.json'
    if(-not(Test-Path -LiteralPath $settingsPath)){$settingsPath=Join-Path $Root 'DDN_ENSAIO.json'}
    Assert-Protected $settingsPath
    $settings=Read-DDNJson $settingsPath
    if($settings.database -cne 'ddn_wms_ensaio_test' -or $settings.user -cne 'ddn_wms_runtime'){Stop-DDNInstall 'DDN_TEST_ONLY'}
    $ports=@($settings.postgres,$settings.http,$settings.https)
    if(@($ports | Select-Object -Unique).Count -ne 3){Stop-DDNInstall 'DDN_PORTS'}
    foreach($port in $ports){if($port -isnot [int] -and $port -isnot [long]){Stop-DDNInstall 'DDN_PORTS'};if($port -lt 1024 -or $port -gt 65535 -or $port -in @(5432,8000,55432,8765,8443)){Stop-DDNInstall 'DDN_PORTS'}}
    $registry=Join-Path $Root 'DDN_SERVICES.json'
    if(Test-Path -LiteralPath $registry){
        $old=Read-DDNJson $registry
        if($old.settingsHash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $settingsPath).Hash){Stop-DDNInstall 'DDN_EXISTING_SETTINGS_CHANGED'}
        foreach($entry in $old.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $entry.Name).Hash -ne $entry.Value){Stop-DDNInstall 'DDN_EXISTING_FILES_CHANGED'}}
        & (Join-Path $PSScriptRoot 'gestao.ps1') -Action Status -Configuration $registry
        if($LASTEXITCODE -ne 0){Stop-DDNInstall 'DDN_EXISTING_MISMATCH'}
        Write-Host 'DDN_ALREADY_INSTALLED_UNCHANGED';exit 0
    }
    $journal=Join-Path $Root 'DDN_INSTALLATION.json'
    if(Test-Path -LiteralPath $journal){Stop-DDNInstall 'DDN_PARTIAL_PRESERVED_TECHNICAL_REVIEW'}
    $names=@('DDNWMS-Test-Postgres','DDNWMS-Test-WMS','DDNWMS-Test-HTTPS')
    foreach($name in $names){if(Get-Service -Name $name -ErrorAction SilentlyContinue){Stop-DDNInstall 'DDN_NAME_COLLISION'}}
    foreach($port in $ports){if(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue){Stop-DDNInstall 'DDN_PORT_OCCUPIED'}}
    $framework=[Microsoft.Win32.Registry]::LocalMachine.OpenSubKey('SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full')
    if(-not $framework -or [int]$framework.GetValue('Release') -lt 528040){Stop-DDNInstall 'DDN_FRAMEWORK_48_REQUIRED'}
    $framework.Dispose()
    $winsw=Join-Path $Root 'components\WinSW.NET461.exe'
    Assert-Protected $winsw
    if((Get-FileHash -Algorithm SHA256 -LiteralPath $winsw).Hash -ne 'b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f'){Stop-DDNInstall 'DDN_HOST_HASH'}
    $bundled=Read-DDNJson (Join-Path $Root 'components\BUNDLED.json')
    $caddy=Join-Path $Root 'components\caddy.exe';Assert-Protected $caddy
    if((Get-FileHash -Algorithm SHA256 -LiteralPath $caddy).Hash -ne $bundled.caddy_sha256){Stop-DDNInstall 'DDN_CADDY_HASH'}
    # PostgreSQL binaries come from a separately reviewed official vendor bundle.
    $approvalPath=Join-Path $Root 'DDN_POSTGRES_APPROVED.json';Assert-Protected $approvalPath
    $approval=Read-DDNJson $approvalPath
    $origin=[Uri]$approval.origin
    if($origin.Scheme -ne 'https' -or $origin.Host -notin @('get.enterprisedb.com','www.enterprisedb.com','ftp.postgresql.org')){Stop-DDNInstall 'DDN_PG_ORIGIN'}
    if($approval.version -notmatch '^17\.[0-9]+$'){Stop-DDNInstall 'DDN_PG_VERSION'}
    foreach($actual in @(Get-ChildItem -LiteralPath (Join-Path $Root 'components\postgres') -Recurse -File)){
        $relative=$actual.FullName.Substring($Root.Length+1).Replace('\','/')
        if(-not $approval.files.PSObject.Properties[$relative]){Stop-DDNInstall 'DDN_UNVERIFIED_PG_FILE'}
    }
    foreach($entry in $approval.files.PSObject.Properties){
        if($entry.Name -notmatch '^components/postgres/[A-Za-z0-9_./-]+$' -or $entry.Name.Contains('..')){Stop-DDNInstall 'DDN_PG_PATH'}
        $path=Join-Path $Root $entry.Name;Assert-Protected $path
        if((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash -ne $entry.Value){Stop-DDNInstall 'DDN_PG_HASH'}
    }
    $postgres=Join-Path $Root 'components\postgres\bin\postgres.exe'
    $pgctl=Join-Path $Root 'components\postgres\bin\pg_ctl.exe'
    foreach($file in @('components/postgres/bin/postgres.exe','components/postgres/bin/pg_ctl.exe')){if(-not $approval.files.PSObject.Properties[$file]){Stop-DDNInstall 'DDN_PG_HASH_MISSING'}}
    $data=Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'DDNWMS-Ensaio'
    Assert-Protected $data
    $pgdata=Join-Path $data 'postgres'
    Assert-Protected $pgdata
    if(Test-Path -LiteralPath (Join-Path $pgdata 'postmaster.pid')){Stop-DDNInstall 'DDN_CLUSTER_MUST_BE_STOPPED'}
    $markerPath=Join-Path $pgdata 'DDN_CLUSTER.json';Assert-Protected $markerPath
    $marker=Read-DDNJson $markerPath
    if($marker.mode -ne 'homologacao' -or $marker.database -cne $settings.database -or $marker.port -ne $settings.postgres -or $marker.system_identifier -notmatch '^[0-9]+$'){Stop-DDNInstall 'DDN_CLUSTER_PROOF'}
    if((Get-Content -Raw -LiteralPath (Join-Path $pgdata 'PG_VERSION')).Trim() -ne '17'){Stop-DDNInstall 'DDN_CLUSTER_VERSION'}
    if(Get-ChildItem -LiteralPath $pgdata -Recurse -Force | Where-Object {$_.Attributes -band [IO.FileAttributes]::ReparsePoint}){Stop-DDNInstall 'DDN_CLUSTER_REPARSE'}
    # Offline proof prevents adoption of a different cluster at the same path.
    $control=Join-Path $Root 'components\postgres\bin\pg_controldata.exe'
    if(-not $approval.files.PSObject.Properties['components/postgres/bin/pg_controldata.exe']){Stop-DDNInstall 'DDN_PG_HASH_MISSING'}
    $env:LC_MESSAGES='C';$env:LANG='C'
    foreach($key in @(Get-ChildItem Env:PG*)) {Remove-Item -LiteralPath ('Env:'+ $key.Name)}
    $controlText=& $control -D $pgdata 2>&1
    if($LASTEXITCODE -ne 0 -or ($controlText -join "`n") -notmatch ('(?m)^Database system identifier:\s*'+[regex]::Escape($marker.system_identifier)+'\s*$')){Stop-DDNInstall 'DDN_CLUSTER_ID'}
    $tls=Join-Path $data 'tls'
    Assert-Protected $tls
    foreach($file in @('server.crt','server.key')){
        Assert-Protected (Join-Path $tls $file)
        foreach($rule in (Get-Acl -LiteralPath (Join-Path $tls $file)).Access){if($rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')){Stop-DDNInstall 'DDN_TLS_PRIVATE'}}
    }
    $networkPath=Join-Path $Root 'DDN_HTTPS.json';Assert-Protected $networkPath
    $network=Read-DDNJson $networkPath
    if($network.hostname -notmatch '^[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$'){Stop-DDNInstall 'DDN_DNS'}
    $python=Join-Path $Root '.ddn-venv\Scripts\python.exe'
    $active=Join-Path $Root 'DDN_ACTIVE_ENV.json'
    if(Test-Path -LiteralPath $active){$v=(Read-DDNJson $active).environment;if($v -notmatch '^\.ddn-envs/env-[a-f0-9]{32}$'){Stop-DDNInstall 'DDN_ENV'};$python=Join-Path $Root ($v+'\Scripts\python.exe')}
    Assert-Protected $python
    $signature=Get-AuthenticodeSignature -LiteralPath $python
    if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation'){Stop-DDNInstall 'DDN_PYTHON_SIGNATURE'}
    if((Read-Host 'Instalar e iniciar SO DDN, cluster/base ficticia e certificados DDN comprovados? Escreva INSTALAR SERVICOS DDN') -cne 'INSTALAR SERVICOS DDN'){exit 0}
    foreach($path in @((Join-Path $Root 'services'),(Join-Path $data 'secrets'),(Join-Path $data 'wms-tmp'),(Join-Path $data 'logs'))){if(Test-Path -LiteralPath $path){Stop-DDNInstall 'DDN_EXISTING_RESOURCE'}}
    Write-DDNNew $journal ((@{status='installing';id=[Guid]::NewGuid().ToString();created_utc=[DateTime]::UtcNow.ToString('o');names=$names})|ConvertTo-Json)
    $powershell=[IO.Path]::Combine([Environment]::SystemDirectory,'WindowsPowerShell\v1.0\powershell.exe')
    $serviceDir=Join-Path $Root 'services'
    if(Test-Path -LiteralPath $serviceDir){Stop-DDNInstall 'DDN_EXISTING_DIRECTORY'}
    New-Item -ItemType Directory -Path $serviceDir | Out-Null
    $entries=@()
    foreach($name in $names){
        $exe=Join-Path $serviceDir ($name+'.exe')
        Copy-Item -LiteralPath $winsw -Destination $exe -ErrorAction Stop
        $xml=New-Object Xml.XmlDocument
        $xml.AppendChild($xml.CreateElement('service'))|Out-Null
        $values=@{id=$name;name=$name;description='DDN WMS ENSAIO - independente da GVA';workingdirectory=$Root;startmode='Manual';stoptimeout='90 sec';resetfailure='1 day'}
        if($name -eq $names[0]){$values.executable=$postgres;$values.arguments='-D "'+$pgdata+'" -h 127.0.0.1 -p '+$settings.postgres;$values.stopexecutable=$pgctl;$values.stoparguments='stop -D "'+$pgdata+'" -m fast -w'}
        elseif($name -eq $names[1]){$values.executable=$python;$values.arguments='-m iberbag.windows_service --config "'+(Join-Path $Root 'DDN_WMS_SERVICE.json')+'"';$values.depend=$names[0]}
        else{$values.executable=$caddy;$values.arguments='run --config "'+(Join-Path $Root 'DDN_Caddyfile')+'" --adapter caddyfile';$values.depend=$names[1]}
        foreach($key in $values.Keys){$e=$xml.CreateElement($key);$e.InnerText=$values[$key];$xml.DocumentElement.AppendChild($e)|Out-Null}
        $account=$xml.CreateElement('serviceaccount')
        foreach($pair in @(@('domain','NT SERVICE'),@('user',$name),@('allowservicelogon','false'))){$e=$xml.CreateElement($pair[0]);$e.InnerText=$pair[1];$account.AppendChild($e)|Out-Null}
        $xml.DocumentElement.AppendChild($account)|Out-Null
        $logPath=Join-Path $data ('logs\'+$name);New-Item -ItemType Directory -Path $logPath -Force | Out-Null
        foreach($key in @('TEMP','TMP','XDG_CONFIG_HOME','XDG_DATA_HOME')){$ev=$xml.CreateElement('env');$ev.SetAttribute('name',$key);$ev.SetAttribute('value',$logPath);$xml.DocumentElement.AppendChild($ev)|Out-Null}
        $e=$xml.CreateElement('logpath');$e.InnerText=$logPath;$xml.DocumentElement.AppendChild($e)|Out-Null
        $log=$xml.CreateElement('log');$log.SetAttribute('mode','roll-by-size');$xml.DocumentElement.AppendChild($log)|Out-Null
        $failure=$xml.CreateElement('onfailure');$failure.SetAttribute('action','restart');$failure.SetAttribute('delay','10 sec');$xml.DocumentElement.AppendChild($failure)|Out-Null
        Write-DDNNew (Join-Path $serviceDir ($name+'.xml')) $xml.OuterXml
        & $exe install *> $null
        if($LASTEXITCODE -ne 0){Stop-DDNInstall 'DDN_SCM_INSTALL_FAILED'}
        $sid=(New-Object Security.Principal.NTAccount('NT SERVICE',$name)).Translate([Security.Principal.SecurityIdentifier]).Value
        Set-DDNPrivateAcl $logPath $sid 'Modify'
        $entries+=@{name=$name;binary=$exe;sha256='b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f';imagePath=('"'+$exe+'"');account=('NT SERVICE\'+$name);sid=$sid}
    }
    # Grant only code read/execute to the three virtual identities; no code writes.
    foreach($path in @((Get-Item -LiteralPath $Root)) + @(Get-ChildItem -LiteralPath $Root -Recurse -Force)){
        if($path.Attributes -band [IO.FileAttributes]::ReparsePoint){Stop-DDNInstall 'DDN_CODE_REPARSE'}
        $acl=Get-Acl -LiteralPath $path.FullName
        $acl.SetAccessRuleProtection($true,$false)
        $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
        foreach($rule in @($acl.Access)){$acl.RemoveAccessRuleSpecific($rule)}
        foreach($sid in @('S-1-5-32-544','S-1-5-18')+@($entries.sid)){
            $rights=if($sid -in @('S-1-5-32-544','S-1-5-18')){'FullControl'}else{'ReadAndExecute'}
            $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),$rights,'Allow')))
        }
        Set-Acl -LiteralPath $path.FullName -AclObject $acl
    }
    foreach($shared in @($data,(Join-Path $data 'logs'))){
        $acl=New-Object Security.AccessControl.DirectorySecurity;$acl.SetAccessRuleProtection($true,$false)
        $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
        foreach($sid in @('S-1-5-32-544','S-1-5-18')+@($entries.sid)){
            $rights=if($sid -in @('S-1-5-32-544','S-1-5-18')){'FullControl'}else{'ReadAndExecute'}
            $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),$rights,'Allow')))
        }
        Set-Acl -LiteralPath $shared -AclObject $acl
    }
    # Data isolation is distinct from code. Never grant WMS access to cluster files.
    foreach($path in @((Get-Item -LiteralPath $pgdata)) + @(Get-ChildItem -LiteralPath $pgdata -Recurse -Force)){Set-DDNPrivateAcl $path.FullName $entries[0].sid 'Modify'}
    Set-DDNPrivateAcl $tls $entries[2].sid 'ReadAndExecute'
    foreach($file in @('server.crt','server.key')){Set-DDNPrivateAcl (Join-Path $tls $file) $entries[2].sid 'Read'}
    $secrets=Join-Path $data 'secrets'
    if(Test-Path -LiteralPath $secrets){Stop-DDNInstall 'DDN_EXISTING_SECRET_DIRECTORY'}
    New-Item -ItemType Directory -Path $secrets | Out-Null
    Set-DDNPrivateAcl $secrets $entries[1].sid 'ReadAndExecute'
    $secret=Join-Path $secrets 'runtime.dpapi'
    Save-DDNSecret $secret (Read-Host 'Palavra-passe PostgreSQL ddn_wms_runtime (nao sera mostrada)' -AsSecureString) $entries[1].sid
    $temporary=Join-Path $data 'wms-tmp';New-Item -ItemType Directory -Path $temporary | Out-Null;Set-DDNPrivateAcl $temporary $entries[1].sid 'Modify'
    $profile=@{database=$settings.database;user=$settings.user;postgres=$settings.postgres;http=$settings.http;https=$settings.https;python=$python;release=$Root;temporary=$temporary;secret=$secret}
    Write-DDNNew (Join-Path $Root 'DDN_WMS_SERVICE.json') ($profile|ConvertTo-Json)
    $caddyText="{`n admin off`n auto_https off`n servers {`n  protocols h1 h2`n }`n}`nhttps://$($network.hostname):$($settings.https) {`n tls `"$($tls.Replace('\','/'))/server.crt`" `"$($tls.Replace('\','/'))/server.key`"`n reverse_proxy 127.0.0.1:$($settings.http)`n}`n"
    Write-DDNNew (Join-Path $Root 'DDN_Caddyfile') $caddyText
    foreach($path in @('DDN_WMS_SERVICE.json','DDN_Caddyfile')){Set-DDNPrivateAcl (Join-Path $Root $path) $entries[1].sid 'Read'}
    # Caddy configuration needs only HTTPS identity read, not WMS secret access.
    Set-DDNPrivateAcl (Join-Path $Root 'DDN_Caddyfile') $entries[2].sid 'Read'
    $tracked=@($secret,$winsw,$caddy,$postgres,$pgctl,$control,$approvalPath,(Join-Path $Root 'DDN_Caddyfile'),(Join-Path $Root 'DDN_WMS_SERVICE.json'),(Join-Path $Root 'iberbag\windows_service.py'),(Join-Path $Root 'iberbag\windows_credentials.py'),(Join-Path $Root 'windows\segredo_servico.ps1'))+@((Get-ChildItem -LiteralPath $serviceDir -File).FullName)
    $hashes=@{};foreach($path in $tracked){$hashes[$path]=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash}
    # Enable automatic start only after identities, credentials and profiles exist.
    foreach($entry in $entries){
        $service=Get-CimInstance Win32_Service -Filter ("Name='"+$entry.name+"'")
        if($service.PathName -cne $entry.imagePath -or $service.StartName -cne $entry.account){Stop-DDNInstall 'DDN_SCM_IDENTITY_CHANGED'}
        $change=Invoke-CimMethod -InputObject $service -MethodName Change -Arguments @{StartMode='Automatic'}
        if($change.ReturnValue -ne 0){Stop-DDNInstall 'DDN_SCM_AUTOSTART'}
        $xmlFile=[IO.Path]::ChangeExtension($entry.binary,'.xml')
        $document=New-Object Xml.XmlDocument;$document.Load($xmlFile);$document.service.startmode='Automatic';$document.Save($xmlFile)
        $hashes[$xmlFile]=(Get-FileHash -Algorithm SHA256 -LiteralPath $xmlFile).Hash
    }
    $pending=Join-Path $Root 'DDN_SERVICES_PENDING.json'
    Write-DDNNew $pending ((@{mode='homologacao';ports=$settings;settingsHash=(Get-FileHash -Algorithm SHA256 -LiteralPath $settingsPath).Hash;files=$hashes;httpsUrl=('https://'+$network.hostname+':'+$settings.https);services=$entries})|ConvertTo-Json -Depth 6)
    Set-DDNPrivateAcl $pending $entries[1].sid 'Read'
    foreach($entry in $entries){Start-Service -Name $entry.name -ErrorAction Stop;(Get-Service $entry.name).WaitForStatus('Running',[TimeSpan]::FromSeconds(30))}
    $ready=$false
    for($i=0;$i -lt 12;$i++){
        $result=& (Join-Path $PSScriptRoot 'gestao.ps1') -Action Status -Configuration $pending 6>&1
        if(($result -join "`n").Contains('DDN_READY_LOCAL_HTTPS_VALIDATED')){$ready=$true;break}
        Start-Sleep -Seconds 3
    }
    if(-not $ready){Stop-DDNInstall 'DDN_HEALTH_NOT_READY'}
    [IO.File]::Move($pending,$registry)
    Write-Host 'DDN_INSTALLED_RUNNING_AUTO_LOCAL_HEALTH_OK. Firewall e trust stores nao foram alterados; reboot real por homologar.'
} catch {
    # Stop/disable only resources created by this invocation, with exact ownership.
    foreach($entry in @($entries | Sort-Object name -Descending)){
        try{
            $service=Get-CimInstance Win32_Service -Filter ("Name='"+$entry.name+"'")
            if($service -and $service.PathName -ceq $entry.imagePath -and $service.StartName -ceq $entry.account){
                Invoke-CimMethod -InputObject $service -MethodName Change -Arguments @{StartMode='Manual'}|Out-Null
                Stop-Service -Name $entry.name -ErrorAction Stop
            }
        }catch{Write-Host 'DDN_PARTIAL_SERVICE_REQUIRES_MANUAL_STATE_CHECK'}
    }
    Write-Host 'DDN_INSTALL_REFUSED_OR_PARTIAL. Recursos existentes preservados; consultar o procedimento e diario. Nenhum rollback destrutivo.';exit 1}
