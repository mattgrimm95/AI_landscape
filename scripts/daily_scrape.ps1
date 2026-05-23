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

# Load the Anthropic key from the DPAPI-encrypted store written by
# scripts\setup_anthropic_key.ps1. Idempotent and silent if the file
# doesn't exist OR if $env:ANTHROPIC_API_KEY is already set by some
# other means (so an operator who prefers `setx` is not forced onto
# the DPAPI flow).
. (Join-Path $PSScriptRoot 'load_anthropic_key.ps1')

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

# Pre-flight: capture whether ANTHROPIC_API_KEY is reaching this process,
# and where it came from (the DPAPI store loaded above, or some prior env
# scope), so a missing-key day can be diagnosed by grep instead of
# squinting at the python output for the "skipping" line.
$dpapiFile = Join-Path $env:LOCALAPPDATA 'AILandscape\anthropic_api_key.dpapi'
if ($env:ANTHROPIC_API_KEY) {
    $source = if (Test-Path $dpapiFile) { 'DPAPI store or pre-existing env' } else { 'pre-existing env' }
    Write-Log "PREFLIGHT: ANTHROPIC_API_KEY present (length=$($env:ANTHROPIC_API_KEY.Length), source=$source); synthesis will be attempted."
} else {
    Write-Log "PREFLIGHT: ANTHROPIC_API_KEY is NOT set (no DPAPI store at $dpapiFile, no env var); synthesis will be skipped."
}

# 1. Scrape feeds into the corpus and rebuild the derived databases. The
#    rebuild step also writes today's synthesis sidecar (hype + briefing
#    narrative) to snapshots/syntheses/ if ANTHROPIC_API_KEY is set on
#    this machine -- a silent no-op otherwise.
Write-Log ((& $python -m ailandscape.cli run 2>&1 | Out-String).TrimEnd())

# Post-run synthesis verification: did today's snapshot land?
# This is the unambiguous signal -- a single grep target -- so a future
# "I set the key but no snapshot appeared" can be diagnosed without
# parsing the python JSON output. The snapshot filename is the UTC date
# (matching ailandscape.synthesis_cache.snapshot_path).
$snapshotPath = Join-Path $repo ("snapshots\syntheses\" + ([DateTime]::UtcNow.ToString('yyyy-MM-dd')) + '.json')
if (Test-Path $snapshotPath) {
    $info = Get-Item $snapshotPath
    Write-Log "SYNTHESIS: snapshot present at $snapshotPath (size=$($info.Length)B, mtime=$($info.LastWriteTime.ToString('HH:mm:ss')))"
} else {
    if ($env:ANTHROPIC_API_KEY) {
        Write-Log "SYNTHESIS: WARN -- key was set but no snapshot was written at $snapshotPath. Check the python output above for the failure mode (rate-limit, network, etc.)."
    } else {
        Write-Log "SYNTHESIS: no snapshot written (expected -- no key)."
    }
}

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
