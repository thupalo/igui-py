# Build dist\iGui (run from anywhere):  powershell -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    & .venv\Scripts\python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
    & .venv\Scripts\pyinstaller packaging\igui.spec --noconfirm --log-level WARN
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
    # site-specific, untracked files (see igui.example.toml)
    foreach ($f in "igui.toml", "macros.json") {
        if (Test-Path $f) { Copy-Item $f "dist\iGui\$f" -Force }
    }
    Write-Host "Built: $root\dist\iGui\iGui.exe"
} finally { Pop-Location }
