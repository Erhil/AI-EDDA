[CmdletBinding()]
param([string]$ServerHost = '127.0.0.1', [ValidateRange(1,65535)][int]$Port = 8000, [string]$ProjectRoot, [string]$ConfigRoot)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $cli = Join-Path $PSScriptRoot '.venv\Scripts\ai-edda.exe'
    if (-not (Test-Path $cli)) { throw 'Run setup.ps1 first.' }
    $arguments = @('serve', '--host', $ServerHost, '--port', "$Port")
    if ($ProjectRoot) { $arguments += @('--project-root', $ProjectRoot) }
    if ($ConfigRoot) { $arguments += @('--config-root', $ConfigRoot) }
    & $cli @arguments
    if ($LASTEXITCODE -ne 0) { throw "Server exited with code $LASTEXITCODE." }
} finally { Pop-Location }
