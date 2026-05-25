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

# Pre-flight: which synthesis transport is available to this run?
# The pipeline prefers the Claude Code CLI (uses the user's Max
# subscription, no API billing); falls back to ANTHROPIC_API_KEY only
# if the CLI is missing. Logged before the run so a missing-transport
# day can be diagnosed by grep.
$pythonCheck = & $python -c "from ailandscape import synthesis; t = synthesis.transport(); print(t or 'none')" 2>&1
Write-Log ("PREFLIGHT: synthesis transport = " + $pythonCheck.Trim())

# 1. Scrape feeds into the corpus and rebuild the derived databases. The
#    rebuild step also writes today's synthesis sidecar (hype + briefing
#    narrative) to snapshots/syntheses/ when a synthesis transport is
#    available -- a silent no-op otherwise.
Write-Log ((& $python -m ailandscape.cli run 2>&1 | Out-String).TrimEnd())

# Post-run synthesis verification: did today's snapshot land?
# This is the unambiguous signal -- a single grep target -- so a future
# "I expected a snapshot but didn't get one" can be diagnosed without
# parsing the python JSON output. The snapshot filename is the UTC date
# (matching ailandscape.synthesis_cache.snapshot_path).
$snapshotPath = Join-Path $repo ("snapshots\syntheses\" + ([DateTime]::UtcNow.ToString('yyyy-MM-dd')) + '.json')
if (Test-Path $snapshotPath) {
    $info = Get-Item $snapshotPath
    Write-Log "SYNTHESIS: snapshot present at $snapshotPath (size=$($info.Length)B, mtime=$($info.LastWriteTime.ToString('HH:mm:ss')))"
} else {
    if ($pythonCheck.Trim() -ne 'none') {
        Write-Log "SYNTHESIS: WARN -- a transport ($($pythonCheck.Trim())) was available but no snapshot was written at $snapshotPath. Check the python output above for the failure mode (rate-limit, CLI error, timeout, etc.)."
    } else {
        Write-Log "SYNTHESIS: no snapshot written (expected -- no transport available)."
    }
}

# 2. Commit and push: the corpus, any new synthesis snapshot, the
#    archived-doc sidecar (audit-corpus-ai may have moved drops there),
#    and the per-run ingest history line so the operator can audit
#    pipeline health from any clone via `ailandscape history`.
& $git add corpus/documents.jsonl corpus/archived.jsonl snapshots/syntheses snapshots/run-history.jsonl
$changed = & $git status --porcelain corpus/documents.jsonl corpus/archived.jsonl snapshots/syntheses snapshots/run-history.jsonl
if ($changed) {
    $msg = "Daily scrape: corpus + synthesis + history update $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
    Write-Log ((& $git -c user.email='' commit -m $msg 2>&1 | Out-String).TrimEnd())
    Write-Log ((& $git push origin main 2>&1 | Out-String).TrimEnd())
    Write-Log 'committed and pushed corpus + synthesis + history update'
} else {
    Write-Log 'no new articles, snapshots, or history; nothing to commit'
}

Write-Log "=== $(Get-Date -Format 'HH:mm:ss')  done ===`r`n"
