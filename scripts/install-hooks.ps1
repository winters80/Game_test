# Install local git hooks for System Breaker (Windows / PowerShell).
#
# Run from the repo root:
#   .\scripts\install-hooks.ps1
#
# Copies scripts/hooks/* into .git/hooks/, overwriting any existing hook of
# the same name. Hooks are local-only — git doesn't sync them automatically
# — so each collaborator runs this once after cloning.

$ErrorActionPreference = "Stop"

$repoRoot = (git rev-parse --show-toplevel 2>$null)
if (-not $repoRoot) {
    Write-Error "Not inside a git repository."
    exit 1
}

$src = Join-Path $repoRoot "scripts/hooks"
# --git-path resolves correctly from worktrees (where .git is a file) and
# honours core.hooksPath.
$dest = (git -C $repoRoot rev-parse --path-format=absolute --git-path hooks)

if (-not (Test-Path $src)) {
    Write-Error "$src does not exist."
    exit 1
}

if (-not (Test-Path $dest)) {
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
}

Get-ChildItem $src -File | ForEach-Object {
    $target = Join-Path $dest $_.Name
    Copy-Item $_.FullName $target -Force
    Write-Host "  installed: $($_.Name)"
}

Write-Host ""
Write-Host "Hooks installed. The pre-push hook will now run test_characters.py"
Write-Host "and test_quests.py before every push, and abort on failure."
