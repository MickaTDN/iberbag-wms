# Run on each OFFICE CLIENT, not on the server. Does not change configuration.
$ErrorActionPreference = 'Stop'
$serverName = Read-Host 'Nome DNS do servidor [wms.iberbag.home.arpa]'
if ([string]::IsNullOrWhiteSpace($serverName)) { $serverName = 'wms.iberbag.home.arpa' }
if ($serverName -notmatch '^[a-zA-Z0-9.-]+$') { throw 'Nome DNS invalido.' }
function Test-Port([string]$Computer, [int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.ConnectAsync($Computer, $Port)
        if (-not $pending.Wait(3000)) { return $false }
        return $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}
$results = @()
try {
    $addresses = [System.Net.Dns]::GetHostAddresses($serverName)
    if (-not $addresses) { throw 'Sem enderecos DNS.' }
    $results += 'DNS: resolvido. Confirmar que o IP e o do servidor.'
    $response = Invoke-RestMethod -Uri "https://$serverName/health" -TimeoutSec 10
    if ($response.status -ne 'ok' -or $response.application -ne 'Iberbag WMS') { throw 'Health invalido.' }
    $results += 'PASS: HTTPS, certificado e base disponiveis.'
} catch {
    $results += 'FAIL: DNS, HTTPS, certificado ou aplicacao. Nao ignorar avisos TLS.'
}
foreach ($port in @(5432, 8000)) {
    if (Test-Port $serverName $port) {
        $results += "FAIL: porta $port acessivel. Rever bind e firewall do servidor."
    } else {
        $results += "PASS: porta $port nao acessivel a partir deste posto."
    }
}
$results += 'Falta: iniciar sessoes individuais nos dois postos em simultaneo e validar stock, mapa e permissoes conforme guia.'
$results | ForEach-Object { Write-Host $_ }
$reportPath = Join-Path $env:TEMP ('iberbag-posto-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')
$results | Set-Content -Encoding UTF8 $reportPath
Write-Host "Relatorio: $reportPath"
Read-Host 'Prima Enter para sair'
if ($results -match '^FAIL:') { exit 1 }
