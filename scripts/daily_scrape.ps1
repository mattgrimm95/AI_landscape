# daily_scrape.ps1
#
# Scrapes the configured feeds into the corpus, rebuilds the databases, then
# commits and pushes the corpus if any new articles were added.
#
# Run once a day by Windows Task Scheduler. All paths are resolved relative
# to this script's own location (and python/git from PATH), so nothing
# environment-specific is hardcoded. Output is appended to
# data\daily_scrape.log (the data\ directory is gitignored).

$ErrorActionPreference = 'Continue'

$repo = Split-Path -Parent $PSScriptRoot
$log  = Join-Path $repo 'data\daily_scrape.log'

function Write-Log($text) {
    Add-Content -Path $log -Value $text -Encoding utf8
}

Set-Location -LiteralPath $repo
Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')  daily scrape start ==="

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
$git    = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $python -or -not $git) {
    Write-Log 'ERROR: python or git not found on PATH; aborting.'
    Write-Log "=== $(Get-Date -Format 'HH:mm:ss')  done ===`r`n"
    exit 1
}

# 1. Scrape feeds into the corpus and rebuild the derived databases. The
#    rebuild step also writes today's synthesis sidecar (hype + briefing
#    narrative) to snapshots/syntheses/ if ANTHROPIC_API_KEY is set on
#    this machine — a silent no-op otherwise.
Write-Log ((& $python -m ailandscape.cli run 2>&1 | Out-String).TrimEnd())

# 2. Commit and push: the corpus AND any new synthesis snapshot.
#    Staging snapshots/syntheses/ alongside the corpus means a daily
#    commit carries today's read for visitors to pull, but only if
#    something actually changed (git status --porcelain rejects empty
#    diffs so a key-less run that adds nothing produces no commit).
& $git add corpus/documents.jsonl snapshots/syntheses
$changed = & $git status --porcelain corpus/documents.jsonl snapshots/syntheses
if ($changed) {
    $msg = "Daily scrape: corpus + synthesis update $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
    Write-Log ((& $git -c user.email='' commit -m $msg 2>&1 | Out-String).TrimEnd())
    Write-Log ((& $git push origin main 2>&1 | Out-String).TrimEnd())
    Write-Log 'committed and pushed corpus + synthesis update'
} else {
    Write-Log 'no new articles or snapshots; nothing to commit'
}

Write-Log "=== $(Get-Date -Format 'HH:mm:ss')  done ===`r`n"
