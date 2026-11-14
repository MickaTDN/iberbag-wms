"""Native PowerShell parsing/ACL checks and a fail-closed management invocation."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[2]


def run_ps(code):
    result=subprocess.run([shutil.which('powershell'),'-NoProfile','-NonInteractive','-Command',"$env:PSModulePath=[IO.Path]::Combine($PSHOME,'Modules');"+code],capture_output=True,timeout=30)
    if result.returncode:
        import re,sys
        for label in re.findall(r'DDN_DIAGNOSTIC:([A-Za-z ]+)',result.stdout.decode(errors='replace')):
            print('\n::notice title=DDN native guard::'+label, file=sys.__stdout__,flush=True)
    return result


def quote(path):return "'"+str(path).replace("'","''")+"'"


def test_management_scripts_parse_and_missing_ownership_proof_refuses_actions(tmp_path):
    for name in ('gestao.ps1','gestao_launcher.ps1'):
        script=ROOT/'windows'/name
        result=run_ps('$tokens=$null;$errors=$null;[Management.Automation.Language.Parser]::ParseFile('+quote(script)+',[ref]$tokens,[ref]$errors)|Out-Null;if($errors.Count){exit 1}')
        assert result.returncode==0,result.stderr.decode(errors='replace')
    # A fictitious GVA resource is never written or removed, including an attempted Stop.
    gva=tmp_path/'GVA';gva.mkdir();sentinel=gva/'unchanged';sentinel.write_bytes(b'FICTITIOUS')
    result=run_ps('& '+quote(ROOT/'windows/gestao.ps1')+' -Action Stop -Configuration '+quote(tmp_path/'absent.json'))
    assert result.returncode==1 and sentinel.read_bytes()==b'FICTITIOUS'


@pytest.mark.parametrize('unsafe',[False,True])
def test_management_native_acl_refuses_user_writable_control_files(tmp_path,unsafe):
    file=tmp_path/'control.json';file.write_text('{}')
    for args in [('/inheritance:r',),('/grant:r','*S-1-5-32-544:(F)','*S-1-5-18:(F)'),('/setowner','*S-1-5-32-544')]:
        assert subprocess.run(['icacls',str(file),*args],capture_output=True).returncode==0
    if not unsafe:assert subprocess.run(['icacls',str(file),'/grant','*S-1-1-0:(R)'],capture_output=True).returncode==0
    if unsafe:assert subprocess.run(['icacls',str(file),'/grant','*S-1-1-0:(M)'],capture_output=True).returncode==0
    # Extract and run the real guard, not a copy. No service operation is executed.
    code='$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('+quote(ROOT/'windows/gestao.ps1')+',[ref]$t,[ref]$e);$f=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq "Assert-Protected"},$true);Invoke-Expression $f.Extent.Text;try{Assert-Protected '+quote(file)+';exit 0}catch{Write-Output ("DDN_DIAGNOSTIC:"+$_.Exception.Message);exit 1}'
    result=run_ps(code)
    assert result.returncode==(1 if unsafe else 0)


@pytest.mark.parametrize('case',['status','start','forged_gva','gva_dependency','path_mismatch','firewall','existing_rule','health_wrong','certificate_failure'])
def test_management_simulated_scm_only_starts_verified_ddn_services(tmp_path,case):
    import hashlib,json
    deployment=tmp_path/'DDN';deployment.mkdir()
    binary=deployment/'host.exe';binary.write_bytes(b'FICTITIOUS-NOT-EXECUTABLE')
    names=['DDNWMS-Test-Postgres','DDNWMS-Test-WMS','DDNWMS-Test-HTTPS']
    config=deployment/'DDN_SERVICES.json'
    config.write_text(json.dumps(dict(mode='homologacao',httpsUrl='https://ddn.invalid:8447',services=[dict(name=n,binary=str(binary),sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),imagePath=('"C:\\GVA\\host.exe"' if case=='path_mismatch' else '"'+str(binary)+'" '+n),account='NT SERVICE\\'+n) for n in names])))
    for path in (deployment,binary,config):
        for args in [('/inheritance:r',),('/grant:r','*S-1-5-32-544:(F)','*S-1-5-18:(F)'),('/setowner','*S-1-5-32-544')]:
            assert subprocess.run(['icacls',str(path),*args],capture_output=True).returncode==0
    log=tmp_path/'called.txt'
    prefix='$fixture=Get-Content -Raw '+quote(config)+'|ConvertFrom-Json;$log='+quote(log)+';$forged='+('$true' if case=='forged_gva' else '$false')+';$external='+('$true' if case=='gva_dependency' else '$false')+';'
    prefix+='$mode='+quote(case)+';'
    mocks=r'''
function Get-CimInstance($ClassName,$Filter) {
    $name=[regex]::Match($Filter,"Name='([^']+)'").Groups[1].Value
    $entry=@($fixture.services | Where-Object name -eq $name)[0]
    $image=$entry.imagePath;if($forged){$image='GVA - NEVER TOUCH'}
    return [pscustomobject]@{State='Running';StartMode='Auto';PathName=$image;StartName=$entry.account}
}
function Read-Host($Prompt){return 'DDN ENSAIO'}
function Start-Service($Name){Add-Content -LiteralPath $log -Value $Name}
function Stop-Service($Name){throw 'Unexpected stop'}
function Get-Service($Name){$dependencies=@();if($external){$dependencies=@([pscustomobject]@{Name='GVA-SERVICE'})};$s=[pscustomobject]@{RequiredServices=$dependencies};$s|Add-Member ScriptMethod WaitForStatus {param($state,$timeout)};return $s}
function Invoke-RestMethod($Uri,$TimeoutSec) {
    if($Uri -ne 'https://ddn.invalid:8447/health' -or $TimeoutSec -ne 10){throw 'Unexpected request'}
    if($mode -eq 'certificate_failure'){throw 'Fictitious certificate failure'}
    $app='DDN WMS';if($mode -eq 'health_wrong'){$app='GVA'}
    return [pscustomobject]@{status='ok';application=$app}
}
function Get-NetFirewallRule($Name) {if($mode -eq 'existing_rule'){return [pscustomobject]@{Name=$Name}}}
function New-NetFirewallRule($Name,$DisplayName,$Direction,$Action,$Protocol,$LocalPort,$RemoteAddress,$Profile,$Program) {
    @{name=$Name;direction=$Direction;action=$Action;protocol=$Protocol;port=$LocalPort;remote=$RemoteAddress;profile=$Profile;program=$Program}|ConvertTo-Json -Compress|Set-Content -Encoding UTF8 -LiteralPath $log
}

'''
    action='Firewall' if case in ('firewall','existing_rule','health_wrong','certificate_failure') else ('Status' if case=='status' else 'Start')
    result=run_ps(prefix+mocks+';$env:PSModulePath='+quote(tmp_path/'untrusted-modules')+';& '+quote(ROOT/'windows/gestao.ps1')+' -Action '+action+' -Configuration '+quote(config))
    assert result.returncode==(1 if case in ('forged_gva','gva_dependency','path_mismatch','existing_rule','health_wrong','certificate_failure') else 0),result.stderr.decode(errors='replace')
    if case=='firewall':
        assert json.loads(log.read_text(encoding='utf-8-sig'))==dict(name='DDNWMS-Test-HTTPS',direction='Inbound',action='Allow',protocol='TCP',port=8447,remote='LocalSubnet',profile='Private',program=str(binary))
    else:
        assert (log.read_text().splitlines() if log.exists() else [])==(names if case=='start' else [])


def test_assistant_shortcut_native_creation_and_repeat_preserves_file(tmp_path):
    deployment=tmp_path/'DDN espaço á';(deployment/'windows').mkdir(parents=True)
    (deployment/'INSTALAR_DDN_ENSAIO.bat').write_text('@echo off\nexit /b 0\n')
    script=deployment/'windows/atalho_assistente.ps1'
    shutil.copyfile(ROOT/'windows/atalho_assistente.ps1',script)
    desktop=tmp_path/'desktop';desktop.mkdir()
    code="function Read-Host($p){return 'CRIAR ATALHO'};& "+quote(script)+' -Destination '+quote(desktop)
    assert run_ps(code).returncode==0
    link=desktop/'DDN WMS - Assistente de ensaio.lnk';original=link.read_bytes()
    assert run_ps(code).returncode==1 and link.read_bytes()==original
