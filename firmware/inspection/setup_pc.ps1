$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtime = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtime)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Python environment.' }
}
$temporary = Join-Path $PSScriptRoot 'build\setup_temp'
$cache = Join-Path $PSScriptRoot 'build\pip_cache'
New-Item -ItemType Directory -Path $temporary, $cache -Force | Out-Null
$previousTemp, $previousTmp, $previousCache = $env:TEMP, $env:TMP, $env:PIP_CACHE_DIR
try {
    $env:TEMP, $env:TMP, $env:PIP_CACHE_DIR = $temporary, $temporary, $cache
    & $runtime -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
} finally {
    $env:TEMP, $env:TMP, $env:PIP_CACHE_DIR = $previousTemp, $previousTmp, $previousCache
}
