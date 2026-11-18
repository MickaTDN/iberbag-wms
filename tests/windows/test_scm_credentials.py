"""Real SCM/virtual identities on an ephemeral Windows runner, fictitious secrets.

Only the three DDN names, absent at fixture entry, may be created/deleted. No PG
cluster, existing service, certificate store or firewall is changed by this test.
"""
import base64,hashlib,json,secrets,shutil,subprocess,time,uuid,urllib.request
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
NAMES=['DDNWMS-Test-Postgres','DDNWMS-Test-WMS','DDNWMS-Test-HTTPS']
HASH='e139f9c1dec5c87a0f51a01668b6cc819286c26b8d26c21c6a2ac128993496c6'


def quote(path):return "'"+str(path).replace("'","''")+"'"


def ps(code,stdin=None):
    result=subprocess.run([shutil.which('powershell'),'-NoProfile','-NonInteractive','-Command',"$ErrorActionPreference='Stop';$env:PSModulePath=[IO.Path]::Combine($PSHOME,'Modules');"+'try{'+code+'}catch{Write-Output ("DDN_FIXTURE_EXCEPTION:"+$_.Exception.GetType().Name+":"+$_.InvocationInfo.ScriptLineNumber);if($_.Exception.Message -match "^DDN_[A-Z_]+$"){Write-Output $_.Exception.Message};exit 1}'],input=stdin,capture_output=True,text=True,timeout=90)
    return result


def expect(code,stdin=None):
    result=ps(code,stdin)
    if result.returncode:
        import re
        stage='install' if ' install;' in code else ('setup' if 'Save-DDNSecret' in code else 'native')
        hints=[label for word,label in [('account name is invalid','invalid-account'),('Access is denied','access-denied'),('already exists','name-collision')] if word.lower() in (result.stdout+result.stderr).lower()]
        hints+=re.findall(r'DDN_FIXTURE_EXCEPTION:([A-Za-z0-9:]+)|\b(DDN_SECRET_[A-Z_]+)\b',result.stdout)
        if stage=='install':
            # This invocation has no password/config credential; redact fixture paths.
            detail=re.sub(r'[A-Za-z]:[\\/][^\r\n]+', '[fixture-path]',result.stdout+' '+result.stderr)
            for line in detail.splitlines():
                if any(word in line.lower() for word in ('error','exception','fail','account')):
                    print('\n::notice title=DDN host install diagnostic::'+line[:400].replace('%','%25'),flush=True)
        print('\n::error title=DDN real SCM fixture::'+stage+' failed; exit='+str(result.returncode)+'; safe hints='+str(hints),flush=True)
    assert result.returncode==0,'Native SCM/credential fixture failed; no fallback or privilege weakening permitted.'
    return result.stdout


@pytest.fixture(scope='module')
def scm(tmp_path_factory):
    root=tmp_path_factory.mktemp('DDN-SCM-'+uuid.uuid4().hex)
    # Refuse to touch ANY resource already present, even if its name looks DDN.
    expect("foreach($n in @("+','.join(quote(n) for n in NAMES)+")){if(Get-Service -Name $n -ErrorAction SilentlyContinue){exit 9}}")
    url='https://github.com/winsw/winsw/releases/download/v2.13.0/WinSW.NET462.exe_Release'
    with urllib.request.urlopen(url,timeout=45) as response:binary=response.read(2*1024*1024)
    assert hashlib.sha256(binary).hexdigest()==HASH
    module=root/'segredo_servico.ps1';shutil.copyfile(ROOT/'windows/segredo_servico.ps1',module)
    shutil.copyfile(ROOT/'iberbag/windows_credentials.py',root/'credentials_native.py')
    secret=root/'private/runtime.dpapi';secret.parent.mkdir()
    password='FICTITIOUS-'+secrets.token_urlsafe(32)
    digest=hashlib.sha256(password.encode()).hexdigest()
    created=[]
    try:
        for name in NAMES:
            exe=root/(name+'.exe');exe.write_bytes(binary)
            child=root/(name+'.py');output=root/(name+'.result');output.write_text('')
            child.write_text("import hashlib,time\nfrom pathlib import Path\nfrom credentials_native import read_secret\ntry:\n b=read_secret("+repr(str(secret))+");r='OK' if hashlib.sha256(b).hexdigest()=="+repr(digest)+" else 'BADHASH'\nexcept Exception:r='DENIED'\nPath("+repr(str(output))+").write_text(r)\ntime.sleep(120)\n",encoding='utf-8')
            xml=root/(name+'.xml')
            import xml.etree.ElementTree as ET
            tree=ET.Element('service')
            import os
            values={'id':name,'name':name,'description':'Fictitious DDN virtual service credential test','executable':__import__('sys').executable,'arguments':'"'+str(child)+'"','startmode':'Automatic','stoptimeout':'10 sec','logpath':str(root/(name+'-logs'))}
            for key,value in values.items():ET.SubElement(tree,key).text=value
            account=ET.SubElement(tree,'serviceaccount')
            for key,value in {'domain':'NT SERVICE','user':name,'allowservicelogon':'false'}.items():ET.SubElement(account,key).text=value
            ET.SubElement(tree,'onfailure',action='restart',delay='5 sec')
            xml.write_bytes(ET.tostring(tree))
            expect('& '+quote(exe)+' install;exit $LASTEXITCODE')
            created.append(name)
        setup=". "+quote(module)+";$sid=(New-Object Security.Principal.NTAccount('NT SERVICE','DDNWMS-Test-WMS')).Translate([Security.Principal.SecurityIdentifier]).Value;Set-DDNPrivateAcl "+quote(root)+" $sid ReadAndExecute;"
        # Root has read access for all three identities; each output/log has only its own writer.
        setup+="$acl=Get-Acl -LiteralPath "+quote(root)+";"
        for name in (NAMES[0],NAMES[2]):
            setup+="$s=(New-Object Security.Principal.NTAccount('NT SERVICE',"+quote(name)+")).Translate([Security.Principal.SecurityIdentifier]);$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($s,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow')));"
        setup+="Set-Acl -LiteralPath "+quote(root)+" -AclObject $acl;Set-DDNPrivateAcl "+quote(secret.parent)+" $sid ReadAndExecute;"
        setup+="$p=ConvertTo-SecureString ([Console]::In.ReadLine()) -AsPlainText -Force;Save-DDNSecret "+quote(secret)+" $p $sid;"
        for name in NAMES:
            logs=root/(name+'-logs')
            setup+="New-Item -ItemType Directory -Path "+quote(logs)+" -Force|Out-Null;$s=(New-Object Security.Principal.NTAccount('NT SERVICE',"+quote(name)+")).Translate([Security.Principal.SecurityIdentifier]).Value;Set-DDNPrivateAcl "+quote(logs)+" $s Modify;Set-DDNPrivateAcl "+quote(root/(name+'.result'))+" $s Modify;"
        expect(setup,password+'\n')
        assert password.encode() not in secret.read_bytes()
        yield root,secret,binary
    finally:
        # Ownership proof before every cleanup: never stop/uninstall a foreign service.
        for name in reversed(created):
            exe=root/(name+'.exe')
            code="$s=Get-CimInstance Win32_Service -Filter \"Name='"+name+"'\";if($s -and $s.PathName -ceq '\""+str(exe).replace("'","''")+"\"'){& "+quote(exe)+" stop|Out-Null;& "+quote(exe)+" uninstall|Out-Null}"
            expect(code)


def read_result(root,name,expected):
    output=root/(name+'.result')
    for _ in range(200):
        if output.read_text()==expected:return
        time.sleep(.1)
    pytest.fail('Native service did not produce the expected identity/credential result')


def test_real_virtual_services_can_decrypt_only_as_wms_and_collisions_do_not_reinstall(scm):
    root,secret,binary=scm
    for name in NAMES:expect('& '+quote(root/(name+'.exe'))+' start;exit $LASTEXITCODE')
    read_result(root,NAMES[1],'OK')
    for name in (NAMES[0],NAMES[2]):read_result(root,name,'DENIED')
    status=expect('Get-CimInstance Win32_Service|Where-Object Name -like "DDNWMS-Test-*"|Select-Object Name,StartName,StartMode,State|ConvertTo-Json -Compress')
    rows=json.loads(status)
    assert {r['Name'] for r in rows}==set(NAMES)
    assert all(r['StartName']=='NT SERVICE\\'+r['Name'] and r['StartMode']=='Auto' and r['State']=='Running' for r in rows)
    # The official host also refuses a repeated install; no account or image changes.
    assert ps('& '+quote(root/(NAMES[1]+'.exe'))+' install;exit $LASTEXITCODE').returncode!=0
    assert json.loads(expect('Get-CimInstance Win32_Service|Where-Object Name -like "DDNWMS-Test-*"|Select-Object Name,StartName,StartMode,State|ConvertTo-Json -Compress'))==rows


def test_real_service_refuses_cipher_tampering_and_permissive_acl_without_new_secret(scm):
    root,secret,_=scm;original=secret.read_bytes();exe=root/(NAMES[1]+'.exe')
    damaged=bytearray(original);damaged[-1]^=1;secret.write_bytes(damaged)
    (root/(NAMES[1]+'.result')).write_text('')
    expect('& '+quote(exe)+' restart;exit $LASTEXITCODE');read_result(root,NAMES[1],'DENIED')
    secret.write_bytes(original)
    expect("icacls "+quote(secret)+" /grant '*S-1-1-0:(R)'|Out-Null;if($LASTEXITCODE){exit 1}")
    (root/(NAMES[1]+'.result')).write_text('')
    expect('& '+quote(exe)+' restart;exit $LASTEXITCODE');read_result(root,NAMES[1],'DENIED')
    expect('. '+quote(root/'segredo_servico.ps1')+";$s=(New-Object Security.Principal.NTAccount('NT SERVICE','DDNWMS-Test-WMS')).Translate([Security.Principal.SecurityIdentifier]).Value;Set-DDNPrivateAcl "+quote(secret)+' $s Read')
    (root/(NAMES[1]+'.result')).write_text('')
    expect('& '+quote(exe)+' restart;exit $LASTEXITCODE');read_result(root,NAMES[1],'OK')
    assert secret.read_bytes()==original
