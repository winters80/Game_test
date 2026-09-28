# System Breaker launcher (Windows PowerShell).
#
# Makes sure the AI side is ready, then starts the game:
#   1. installs Python dependencies if any are missing
#   2. starts the Ollama server if nothing is answering
#   3. pulls the models named in config.py if they aren't downloaded yet
#   4. runs the game (the game itself preloads the models into memory)
#
# Usage:  .\scripts\start.ps1           prepare everything, then play
#         .\scripts\start.ps1 -Check    prepare everything, don't launch
# Or double-click start.bat in the repo root.
#
# If Ollama isn't installed the game still starts, without AI.
param([switch]$Check)

Set-Location (Join-Path $PSScriptRoot "..")

function Say($msg)  { Write-Host "  $msg" }
function Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

$py = $null
foreach ($cand in @("python", "py", "python3")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $py = $cand; break }
}
if (-not $py) { Warn "Python 3.11+ is required: https://www.python.org/downloads/"; exit 1 }

# 1. Python dependencies
& $py -c "import rich, questionary, pydantic, inspect, ollama; assert 'think' in inspect.signature(ollama.Client.generate).parameters" 2>$null
if ($LASTEXITCODE -ne 0) {
    Say "Installing Python dependencies..."
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Warn "pip install failed."; exit 1 }
}

function Cfg($expr) { (& $py -c "import config; print($expr)").Trim() }
$aiOn   = Cfg "int(config.AI_ENABLED)"
$url    = Cfg "config.OLLAMA_BASE_URL"
$models = (Cfg "' '.join(dict.fromkeys(m for m in (config.OLLAMA_MODEL, config.OLLAMA_FAST_MODEL) if m))").Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries)

function Test-Ollama {
    try { Invoke-RestMethod -Uri "$url/api/tags" -TimeoutSec 2 | Out-Null; return $true }
    catch { return $false }
}

if ($aiOn -eq "1") {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Warn "Ollama isn't installed, so the game will run without AI."
        Warn "Install it from https://ollama.com/download, then run this again."
    }
    else {
        # The ollama CLI reads OLLAMA_HOST; point it at the same server as the game.
        $env:OLLAMA_HOST = $url
        if (-not (Test-Ollama)) {
            Say "Starting Ollama..."
            Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
            for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep -Seconds 1 }
        }
        if (Test-Ollama) {
            $have = @((Invoke-RestMethod -Uri "$url/api/tags").models | ForEach-Object {
                if ($_.model) { $_.model } else { $_.name }
            })
            foreach ($m in $models) {
                if ($have -contains $m -or $have -contains "${m}:latest") {
                    Say "Model ready: $m"
                }
                else {
                    Say "Downloading model $m (one-time, can take a while)..."
                    & ollama pull $m
                    if ($LASTEXITCODE -ne 0) { Warn "Could not pull $m; the game will say what's missing." }
                }
            }
        }
        else {
            Warn "Ollama didn't start within 30s; the game will run without AI."
        }
    }
}

if ($Check) { Say "Ready."; exit 0 }
& $py -X utf8 main.py
