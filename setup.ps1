[CmdletBinding()]
param([ValidateSet('cuda','cpu')][string]$Device = 'cuda', [switch]$RebuildFrontend, [switch]$DownloadModel, [string[]]$TlsName, [string]$ProjectRoot, [string]$ConfigRoot)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Push-Location $root
try {
    $uv = Join-Path $root '.tools\uv.exe'
    if (-not (Test-Path $uv)) {
        New-Item -ItemType Directory -Force (Split-Path $uv) | Out-Null
        $temp = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
        New-Item -ItemType Directory $temp | Out-Null
        try {
            $zip = Join-Path $temp 'uv.zip'
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing 'https://github.com/astral-sh/uv/releases/download/0.9.18/uv-x86_64-pc-windows-msvc.zip' -OutFile $zip
            if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne '28CBE5D30907A774BFE27A517A39B494EC6F7D3816BDA8BBF6F9645490449182') { throw 'uv archive SHA-256 mismatch.' }
            Expand-Archive $zip -DestinationPath $temp
            $binary = Get-ChildItem $temp -Filter uv.exe -Recurse | Select-Object -First 1
            if (-not $binary) { throw 'uv.exe missing from archive.' }
            Copy-Item $binary.FullName $uv
        } finally {
            $tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
            $cleanupTarget = [IO.Path]::GetFullPath($temp).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
            $parentPrefix = $tempParent + [IO.Path]::DirectorySeparatorChar
            if ([string]::Equals($cleanupTarget, $tempParent, [StringComparison]::OrdinalIgnoreCase) -or
                -not $cleanupTarget.StartsWith($parentPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing to remove temporary path outside the temp directory: $cleanupTarget"
            }
            $cleanupItem = Get-Item -LiteralPath $cleanupTarget -Force
            if (-not $cleanupItem.PSIsContainer -or ($cleanupItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Refusing to remove unexpected temporary path: $cleanupTarget"
            }
            Remove-Item -LiteralPath $cleanupTarget -Recurse -Force
        }
    }
    $version = & $uv --version
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^uv\s+0\.9\.18(?:\s|$)') { throw 'Expected .tools\uv.exe version 0.9.18.' }
    if ($RebuildFrontend -or -not (Test-Path 'src\ai_edda\static\index.html')) {
        $node = Get-Command node -ErrorAction SilentlyContinue
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if (-not $node -or -not $npm) { throw 'Frontend build requires Node.js 24 and npm. Install Node.js 24, then retry.' }
        $nodeVersion = & $node.Source --version
        if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v24\.') { throw 'Frontend build requires Node.js 24.' }
        Push-Location (Join-Path $root 'web')
        try {
            & $npm.Source ci
            if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
            & $npm.Source run build
            if ($LASTEXITCODE -ne 0) { throw 'npm run build failed.' }
        } finally { Pop-Location }
    }
    & $uv python install 3.12.11
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
    & $uv venv --python 3.12.11 --managed-python .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    & $uv sync --frozen --no-dev --extra $Device --python (Join-Path $root '.venv\Scripts\python.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    $cli = Join-Path $root '.venv\Scripts\ai-edda.exe'
    $paths = @()
    if ($ProjectRoot) { $paths += @('--project-root', $ProjectRoot) }
    if ($ConfigRoot) { $paths += @('--config-root', $ConfigRoot) }
    & $cli init @paths
    if ($LASTEXITCODE -ne 0) { throw 'Initialization failed.' }
    $config = @()
    if ($ConfigRoot) { $config += @('--config-root', $ConfigRoot) }
    if ($DownloadModel) { & $cli download-model @config; if ($LASTEXITCODE -ne 0) { throw 'Model download failed.' } }
    if ($TlsName) {
        $names = @(); foreach ($name in $TlsName) { $names += @('--name', $name) }
        & $cli tls @names @config
        if ($LASTEXITCODE -ne 0) { throw 'TLS setup failed.' }
    }
    Write-Host 'Setup complete. Run .\start.ps1 to start.'
} finally { Pop-Location }
