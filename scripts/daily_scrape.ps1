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

# Prepend the Claude Code install dir to $env:PATH so `claude` is
# discoverable to anything spawned from this script. Two install shapes:
#
#   (a) Regular Win32: %APPDATA%\Claude\claude-code\<version>\claude.exe
#       Also the reflection seen by *packaged* callers inside Claude
#       Code Desktop's MSIX container.
#
#   (b) MSIX / Microsoft Store:
#       %LOCALAPPDATA%\Packages\Claude_<hash>\LocalCache\Roaming\Claude\
#       claude-code\<version>\claude.exe
#       The canonical location for Store-distributed Claude Code Desktop;
#       the Roaming reflection in (a) is virtualized and invisible to
#       non-packaged processes like an interactive PowerShell or a Task
#       Scheduler job.
#
# We try (a) first, fall back to (b). Whichever wins, we pick the
# highest version subdirectory using a 4-digit-padded sort so 2.10.x
# beats 2.9.x when we get there.
$candidateRoots = @()
$candidateRoots += (Join-Path $env:APPDATA 'Claude\claude-code')
$msixPackages = Join-Path $env:LOCALAPPDATA 'Packages'
if (Test-Path $msixPackages) {
    Get-ChildItem $msixPackages -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue | ForEach-Object {
        $candidateRoots += (Join-Path $_.FullName 'LocalCache\Roaming\Claude\claude-code')
    }
}

$discovered = $null
foreach ($root in $candidateRoots) {
    if (-not (Test-Path $root)) { continue }
    $latest = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
        Sort-Object -Property @{Expression={
            ($_.Name -split '\.' | ForEach-Object { '{0:D4}' -f [int]$_ }) -join '.'
        }} -Descending | Select-Object -First 1
    if ($latest -and (Test-Path (Join-Path $latest.FullName 'claude.exe'))) {
        $env:PATH = $latest.FullName + ';' + $env:PATH
        Write-Log ("PREFLIGHT: claude CLI dir prepended to PATH: " + $latest.FullName)
        $discovered = $latest.FullName
        break
    }
}
if (-not $discovered) {
    Write-Log ("PREFLIGHT: WARN -- no claude.exe found under APPDATA or MSIX Packages. Tried: " + ($candidateRoots -join '; '))
}

# Pre-flight: which synthesis transport is available to this run?
# The pipeline prefers the Claude Code CLI (uses the user's Max
# subscription, no API billing); falls back to ANTHROPIC_API_KEY only
# if the CLI is missing. Logged before the run so a missing-transport
# day can be diagnosed by grep.
$pythonCheck = & $python -c "from ailandscape import synthesis; t = synthesis.transport(); print(t or 'none')" 2>&1
Write-Log ("PREFLIGHT: synthesis transport = " + $pythonCheck.Trim())

# Auth check: even when the binary is discoverable, the CLI's
# session-tied login state is NOT visible to a process spawned outside
# Claude Code's own UI. The non-interactive fix is `claude setup-token`,
# which generates a long-lived token tied to the user's Claude
# subscription. Probe with a trivial prompt; on "Not logged in" emit a
# clear remediation in the log so the failure isn't a silent skip.
#
# Also surface whether the well-known token env vars are set so a
# "token went missing" failure mode (uninstalled Claude Code, evicted
# from credential manager, etc.) is grep-able. We do NOT print the
# token itself.
if ($pythonCheck.Trim() -eq 'claude-code-cli') {
    $tokenVars = @('CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_OAUTH_TOKEN', 'CLAUDE_AUTH_TOKEN')
    $tokenSet  = $tokenVars | Where-Object { [Environment]::GetEnvironmentVariable($_, 'Process') }
    if ($tokenSet) {
        Write-Log ("PREFLIGHT: token env var present: " + ($tokenSet -join ', '))
    } else {
        Write-Log "PREFLIGHT: no known token env var set (CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_OAUTH_TOKEN / CLAUDE_AUTH_TOKEN). Cron auth will rely on whatever ``claude setup-token`` configured."
    }

    $authProbe = & claude --print --output-format text 'ping' 2>&1
    $authExit = $LASTEXITCODE
    if ($authExit -eq 0) {
        Write-Log "PREFLIGHT: claude --print auth probe OK (cron synthesis enabled)"
    } else {
        # Filter out the harmless "no stdin data received in 3s" warning
        # so the surfaced error line is the actual failure cause (e.g.
        # "Not logged in · Please run /login"). Also strip any
        # token-shaped output as defense-in-depth -- claude.exe should
        # never echo the token, but if a future bug ever did, we don't
        # want the log to capture it.
        $signal = $authProbe | Where-Object {
            $_ -is [string] -and
            $_ -notmatch 'no stdin data received' -and
            $_ -notmatch '^\s*$' -and
            $_ -notmatch '^At line:' -and
            $_ -notmatch '^\s*\+' -and
            $_ -notmatch 'sk-ant-'
        } | Select-Object -First 1
        $signalStr = ($signal -as [string]).Trim()
        Write-Log ("PREFLIGHT: WARN -- claude --print failed (exit $authExit): $signalStr")
        if ($authProbe -match 'Not logged in') {
            Write-Log "PREFLIGHT:   remediation: run ``claude setup-token`` interactively once to generate a long-lived token; set the resulting token as a system env var so Task Scheduler inherits it."
        }
    }
}

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

# 2. Refresh LLM_INDEX.md so the autogenerated public-function index
#    matches the latest code on every push from the cron. Idempotent --
#    no diff lands if no public signatures changed since the last run.
Write-Log ((& $python scripts/build_llm_index.py 2>&1 | Out-String).TrimEnd())

# 3. Commit and push: the corpus, any new synthesis snapshot, the
#    archived-doc sidecar (audit-corpus-ai may have moved drops there),
#    the per-run ingest history line, and the refreshed LLM_INDEX so
#    the operator can audit pipeline health (and any reader / LLM can
#    navigate the codebase) from any clone.
& $git add corpus/documents.jsonl corpus/archived.jsonl snapshots/syntheses snapshots/run-history.jsonl LLM_INDEX.md
$changed = & $git status --porcelain corpus/documents.jsonl corpus/archived.jsonl snapshots/syntheses snapshots/run-history.jsonl LLM_INDEX.md
if ($changed) {
    $msg = "Daily scrape: corpus + synthesis + history + index update $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')"
    Write-Log ((& $git -c user.email='' commit -m $msg 2>&1 | Out-String).TrimEnd())
    Write-Log ((& $git push origin main 2>&1 | Out-String).TrimEnd())
    Write-Log 'committed and pushed corpus + synthesis + history + index update'
} else {
    Write-Log 'no new articles, snapshots, history, or index changes; nothing to commit'
}

Write-Log "=== $(Get-Date -Format 'HH:mm:ss')  done ===`r`n"
