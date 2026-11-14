"""Execute the real read-only workstation script with fictitious network results.

No TLS policy, firewall or Windows service is changed. DNS resolves only localhost;
HTTP and TCP probes are doubles. Native PowerShell execution is required in CI.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize('case', ['healthy', 'wrong_application', 'unavailable', 'tls_failure', 'exposed_port'])
def test_windows_diagnostic_matches_health_and_rejects_unsafe_post(tmp_path, case):
    from iberbag.app import app
    with TestClient(app) as client:
        response = client.get('/health')
        assert response.status_code == 200
        health = response.json()
    if case == 'wrong_application':
        health['application'] = 'Another service'
    elif case == 'unavailable':
        health['status'] = 'unavailable'
    health_file = tmp_path / 'synthetic-health.json'
    health_file.write_text(json.dumps(health), encoding='utf-8')
    executable = shutil.which('powershell')
    assert executable, 'Native Windows PowerShell required'
    source = Path(__file__).resolve().parents[2] / 'VERIFICAR_POSTO.ps1'
    env = dict(os.environ, TEMP=str(tmp_path), DDN_TEST_HEALTH=str(health_file), DDN_TEST_CASE=case)
    # Replace only external interactions; execute the script and its health/port
    # decisions, report creation and exit status unchanged.
    mocks = r'''
$ErrorActionPreference = 'Stop'
function Read-Host([string]$Prompt) {
    if ($Prompt -like 'Nome DNS*') { return 'localhost' }
    return ''
}
function Invoke-RestMethod([string]$Uri, [int]$TimeoutSec) {
    if ($Uri -ne 'https://localhost/health' -or $TimeoutSec -ne 10) { throw 'Unexpected probe' }
    if ($env:DDN_TEST_CASE -eq 'tls_failure') { throw 'Synthetic certificate failure' }
    return (Get-Content -Raw $env:DDN_TEST_HEALTH | ConvertFrom-Json)
}
function New-Object([string]$TypeName) {
    if ($TypeName -ne 'System.Net.Sockets.TcpClient') { throw 'Unexpected object' }
    $probe = [pscustomobject]@{Connected = ($env:DDN_TEST_CASE -eq 'exposed_port')}
    $probe | Add-Member ScriptMethod ConnectAsync {
        param($Computer, $Port)
        if ($Computer -ne 'localhost' -or $Port -notin @(5432,8000)) { throw 'Unexpected TCP probe' }
        return [System.Threading.Tasks.Task]::FromResult($true)
    }
    $probe | Add-Member ScriptMethod Dispose {}
    return $probe
}
'''
    def run(script):
        command = mocks + "\n& '" + str(script).replace("'", "''") + "'"
        return subprocess.run([executable, '-NoProfile', '-NonInteractive', '-Command', command],
                              env=env, capture_output=True, timeout=30)
    if case == 'healthy':
        # Reproduce the previous false failure using the actual health response.
        legacy = tmp_path / 'legacy-diagnostic.ps1'
        legacy.write_text(source.read_text().replace("'DDN WMS'", "'Iberbag WMS'"), encoding='utf-8')
        assert run(legacy).returncode == 1
        for report in tmp_path.glob('iberbag-posto-*.txt'):
            report.unlink()  # only this test's own synthetic diagnostic report
    result = run(source)
    assert result.returncode == (0 if case == 'healthy' else 1), result.stderr.decode(errors='replace')
    reports = list(tmp_path.glob('iberbag-posto-*.txt'))
    assert len(reports) == 1
    report = reports[0].read_text(encoding='utf-8-sig')
    if case == 'healthy':
        assert 'FAIL:' not in report and report.count('PASS:') == 3
    elif case == 'exposed_port':
        assert 'FAIL: porta 5432' in report and 'FAIL: porta 8000' in report
    else:
        assert 'FAIL: DNS, HTTPS, certificado ou aplicacao.' in report
