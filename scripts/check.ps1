$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
$json2ts = Join-Path $root 'web\node_modules\.bin\json2ts.cmd'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { Write-Error "Missing local Python: $python"; exit 1 }
if (-not $npm) { Write-Error 'Missing npm.cmd. Install Node.js before running checks.'; exit 1 }
if (-not (Test-Path -LiteralPath $json2ts -PathType Leaf)) { Write-Error "Missing local json2ts: $json2ts. Install web dependencies before running checks."; exit 1 }
$temp = Join-Path ([IO.Path]::GetTempPath()) ("ai-edda-check-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
$failed = $false
function Invoke-Check([string]$Name, [scriptblock]$Action) {
    Write-Host "`n=== $Name ==="
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) { throw "Exit code $LASTEXITCODE" }
    } catch { Write-Host "FAILED: $Name - $_"; $script:failed = $true }
}
Push-Location $root
try {
    Invoke-Check 'Ruff' { & $python -m ruff check src tests scripts }
    Invoke-Check 'pytest' { & $python -m pytest tests }
    Invoke-Check 'Schema drift' {
        & $python scripts/export_schema.py --output (Join-Path $temp 'ui-contract.json')
        if ($LASTEXITCODE -ne 0) { throw 'Schema generation failed' }
        if ((Get-FileHash (Join-Path $temp 'ui-contract.json')).Hash -ne (Get-FileHash 'web/schema/ui-contract.json').Hash) { throw 'Schema differs from web/schema/ui-contract.json' }
    }
    Invoke-Check 'Type drift' {
        & $json2ts -i (Join-Path $temp 'ui-contract.json') -o (Join-Path $temp 'ui-contract.ts')
        if ($LASTEXITCODE -ne 0) { throw 'Type generation failed' }
        if ((Get-FileHash (Join-Path $temp 'ui-contract.ts')).Hash -ne (Get-FileHash 'web/src/generated/ui-contract.ts').Hash) { throw 'Types differ from web/src/generated/ui-contract.ts' }
    }
    Invoke-Check 'Typecheck' { & $npm.Source --prefix web run typecheck }
    Invoke-Check 'Node tests' { & $npm.Source --prefix web test }
    Invoke-Check 'Frontend tests' { & $npm.Source --prefix web run test:frontend }
    Invoke-Check 'Production build' { & $npm.Source --prefix web run build -- --outDir (Join-Path $temp 'build') }
} finally {
    Pop-Location
    $resolved = [IO.Path]::GetFullPath($temp).TrimEnd('\', '/')
    $parent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/')
    $ownedName = [IO.Path]::GetFileName($resolved)
    $ownedPattern = '^ai-edda-check-[0-9a-fA-F]{32}$'
    $strictDescendant = $resolved.Length -gt $parent.Length -and $resolved.StartsWith($parent + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
    $item = Get-Item -LiteralPath $resolved -Force
    if ($strictDescendant -and $ownedName -cmatch $ownedPattern -and $item.PSIsContainer -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    } else { Write-Error "Refusing to remove temporary path: $resolved" }
}
if ($failed) { exit 1 }
Write-Host 'All checks passed.'
