# load_anthropic_key.ps1
#
# Dot-source this script to load ANTHROPIC_API_KEY into the current
# PowerShell session from the DPAPI-encrypted store written by
# setup_anthropic_key.ps1.
#
# Idempotent and safe to source unconditionally:
#   * If $env:ANTHROPIC_API_KEY is already set, this is a no-op.
#   * If the encrypted file doesn't exist, this is a no-op (so a fresh
#     checkout / a different user account fails open -- the daily script's
#     PREFLIGHT line will then explicitly log "no key").
#   * If the file exists but can't be decrypted (different user account,
#     corrupted blob), a warning is emitted but execution continues.
#
# Usage from another script (this is what daily_scrape.ps1 does):
#     . (Join-Path $PSScriptRoot 'load_anthropic_key.ps1')
#
# Usage interactively from the repo root:
#     . .\scripts\load_anthropic_key.ps1
#
# DESIGN NOTE: This script must not throw under any circumstance -- it
# loads a secret if available and is silent otherwise. A throw here would
# break the daily cron's whole run for what is meant to be a best-effort
# upgrade path.

if (-not $env:ANTHROPIC_API_KEY) {
    $file = Join-Path $env:LOCALAPPDATA 'AILandscape\anthropic_api_key.dpapi'
    if (Test-Path $file) {
        try {
            # ConvertTo-SecureString is strict about its hex input -- any
            # trailing newline or whitespace from Set-Content / Out-File
            # provokes "Input string was not in a correct format". Trim
            # so the loader is robust to either line-ending convention.
            $cipher = (Get-Content $file -Raw).Trim()
            $sec    = ConvertTo-SecureString -String $cipher
            # NetworkCredential is the standard PowerShell idiom for
            # extracting the plaintext from a SecureString. The plaintext
            # is in memory only as long as the variable lives below.
            $plain = [System.Net.NetworkCredential]::new('', $sec).Password
            $env:ANTHROPIC_API_KEY = $plain
            Remove-Variable plain, sec, cipher
        } catch {
            Write-Warning ('Could not decrypt ' + $file + ': ' + $_.Exception.Message +
                           ' (was this file written by a different Windows user?)')
        }
    }
}
