# setup_anthropic_key.ps1
#
# One-time setup: store the Anthropic API key in a DPAPI-encrypted file
# under %LOCALAPPDATA%\AILandscape\ so it never sits in a plaintext
# environment variable or the registry.
#
# What DPAPI gives you:
#   * The blob is encrypted to YOUR Windows user account on THIS machine.
#     An Administrator on this box cannot read it without logging in as
#     you. Another user account cannot read it at all.
#   * Even if the file is backed up to OneDrive / Time Machine / etc. and
#     leaks, it is useless without the originating user account.
#   * No external dependencies -- DPAPI is built into Windows; PowerShell's
#     ConvertFrom-SecureString uses it by default.
#
# What it does NOT protect against:
#   * Malware running as you (it inherits your decryption rights). This
#     is true of any per-user secret store. Mitigate at the Anthropic
#     console with a monthly spend cap.
#   * Loss of the user profile -- re-run this script to re-store the key.
#
# Usage:
#     .\scripts\setup_anthropic_key.ps1
#
# To rotate:  re-run this script (overwrites the file).
# To remove:  delete the file printed by this script.

$ErrorActionPreference = 'Stop'

$dir  = Join-Path $env:LOCALAPPDATA 'AILandscape'
$file = Join-Path $dir 'anthropic_api_key.dpapi'

New-Item -ItemType Directory -Path $dir -Force | Out-Null

Write-Host ''
Write-Host 'AI Landscape - Anthropic key setup' -ForegroundColor Cyan
Write-Host ('Storing DPAPI-encrypted key at: ' + $file)
Write-Host 'Only this Windows user account on this machine can decrypt it.'
Write-Host ''

# Read-Host -AsSecureString prevents the key from echoing to the terminal
# and never puts it on a process command line (vs the `setx` alternative,
# which leaks via Task Manager and PSReadLine history).
$sec = Read-Host 'Paste your Anthropic API key' -AsSecureString
if ($sec.Length -eq 0) {
    Write-Error 'No key entered; aborting.'
    exit 1
}

# ConvertFrom-SecureString uses DPAPI by default (no -Key / -KeyAsPlainText
# arguments), producing a hex string that only the same user account on
# the same machine can decrypt.
$encrypted = ConvertFrom-SecureString -SecureString $sec
Set-Content -Path $file -Value $encrypted -Encoding ASCII

# Defense in depth: tighten the file ACL so only the current user has
# any access. The DPAPI ciphertext is already useless to anyone else,
# but a strict ACL eliminates a noisy attack surface (no easy way for
# another process running as a different user to even read the bytes).
$acl = Get-Acl -Path $file
$acl.SetAccessRuleProtection($true, $false)  # break inheritance, no copy
$acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $currentUser, 'FullControl', 'Allow'
)
$acl.AddAccessRule($rule)
Set-Acl -Path $file -AclObject $acl

# Free the plaintext copy in this script's memory as quickly as possible.
Remove-Variable sec, encrypted

Write-Host ''
Write-Host 'Stored.' -ForegroundColor Green
Write-Host ''
Write-Host 'Verify with:'
Write-Host '  . .\scripts\load_anthropic_key.ps1'
Write-Host '  python -c "import os; print(bool(os.environ.get(''ANTHROPIC_API_KEY'')))"'
Write-Host ''
Write-Host 'The daily scrape task picks the key up automatically -- no'
Write-Host 'further action needed for tonight''s 7pm run.'
