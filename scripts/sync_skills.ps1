# sync_skills.ps1
#
# Mirror user-level Claude Code skills into this repo's .claude/skills/
# so they're version-controlled along with the project.
#
# Source of truth:  %USERPROFILE%\.claude\skills\
#   (canonical per-user location -- skills here are loaded by every
#   Claude Code session on this machine regardless of working directory)
#
# In-repo mirror:   <repo>\.claude\skills\
#   (committed copy -- gives the repo portability across machines;
#   on a fresh clone, the operator can copy these back to the
#   user-level location to bootstrap their environment)
#
# Direction is one-way: USER -> REPO. The user-level copy is authoritative;
# the in-repo copy is a snapshot that lags until this script is re-run.
#
# Run before `git commit` whenever a SKILL.md was edited at user level.
# Safe to re-run; uses robocopy /MIR so deletions also propagate.

$ErrorActionPreference = 'Stop'

$repo   = Split-Path -Parent $PSScriptRoot
$source = Join-Path $env:USERPROFILE '.claude\skills'
$target = Join-Path $repo '.claude\skills'

if (-not (Test-Path $source)) {
    Write-Output "No user-level skills found at $source -- nothing to sync."
    exit 0
}

if (-not (Test-Path $target)) {
    New-Item -ItemType Directory -Path $target -Force | Out-Null
}

# robocopy exit codes: 0 = no copies needed, 1-7 = copies made (success),
# >=8 = actual failure. /MIR mirrors source into target (adds + updates +
# deletes); the /N* flags suppress robocopy's noisy default output.
$null = robocopy $source $target /MIR /NJH /NJS /NDL /NFL /R:1 /W:1
$exit = $LASTEXITCODE
if ($exit -ge 8) {
    Write-Error "robocopy failed (exit code $exit)"
    exit 1
}

Write-Output "Synced skills: $source -> $target"
$skillFiles = Get-ChildItem -Recurse $target -Filter 'SKILL.md' -ErrorAction SilentlyContinue
foreach ($f in $skillFiles) {
    $rel = $f.FullName.Substring($target.Length + 1)
    Write-Output ("  " + $rel + " (" + $f.Length + " bytes)")
}
if (-not $skillFiles) {
    Write-Output "  (no SKILL.md files found in source)"
}
