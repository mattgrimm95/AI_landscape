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

# 1. Scrape feeds into the corpus and rebuild the derived databases.
Write-Log ((& $python -m ailandscape.cli run 2>&1 | Out-String).TrimEnd())

# 2. Generate the daily "hype read" so the web app's Today's Spotlight
#    has a fresh artifact with a current timestamp. No-op when
#    ANTHROPIC_API_KEY is unset; safe to call unconditionally.
Write-Log ((& $python -m ailandscape.cli hype 2>&1 | Out-String).TrimEnd())

# 3. Commit and push corpus changes — the document log and the daily hype
#    are both inside corpus/, so a single `git add corpus/` picks up
#    whichever files changed (one of the two may be unchanged on a quiet
#    day; the no-changes guard below handles that).
& $git add corpus/documents.jsonl corpus/daily_hype.json
$changed = & $git status --porcelain corpus/documents.jsonl corpus/daily_hype.json
if ($changed) {
    $msg = "Daily scrape: corpus update $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
    Write-Log ((& $git -c user.email='' commit -m $msg 2>&1 | Out-String).TrimEnd())
    Write-Log ((& $git push origin main 2>&1 | Out-String).TrimEnd())
    Write-Log 'committed and pushed corpus update'
} else {
    Write-Log 'no new articles or hype changes; nothing to commit'
}

Write-Log "=== $(Get-Date -Format 'HH:mm:ss')  done ===`r`n"
