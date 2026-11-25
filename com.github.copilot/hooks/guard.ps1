param(
    [Parameter(Mandatory)]
    [ValidateSet('sessionStart', 'sessionEnd', 'preToolUse', 'leadPreToolUse', 'postToolUse', 'postToolUseFailure')]
    [string] $Event
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    if (-not $env:COPILOT_PLUGIN_ROOT -or -not $env:COPILOT_PLUGIN_DATA) {
        throw 'Host plugin environment is missing.'
    }
    $payload = [Console]::In.ReadToEnd()
    $python = Join-Path $env:COPILOT_PLUGIN_ROOT '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
    $output = $payload | & $python -I -B (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts\hook_entry.py') $Event
    if ($LASTEXITCODE -ne 0) {
        throw 'Hook validation or audit failed.'
    }
    $parsed = $output | ConvertFrom-Json -AsHashtable
    if ($parsed -isnot [System.Collections.IDictionary]) {
        throw 'Invalid hook response.'
    }
    $output
}
catch {
    @{
        permissionDecision = 'deny'
        permissionDecisionReason = 'S2F hook failed closed; check installation and bounded local state.'
    } | ConvertTo-Json -Compress
    if ($Event -notin @('preToolUse', 'leadPreToolUse')) { exit 1 }
}
