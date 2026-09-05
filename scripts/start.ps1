$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (Test-Path ".env") {
    Get-Content -LiteralPath ".env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim().Trim('"'), "Process")
        }
    }
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -e .
}
.venv\Scripts\python.exe -m radar init-db
$collector = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "-m", "radar", "collector" -WindowStyle Hidden -PassThru
try {
    .venv\Scripts\python.exe -m radar serve
} finally {
    Stop-Process -Id $collector.Id -ErrorAction SilentlyContinue
}
