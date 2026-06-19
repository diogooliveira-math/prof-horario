<#
.SYNOPSIS
    Full Vault setup for prof-horario.

.DESCRIPTION
    Runs all 9 setup steps in sequence, skipping any that already completed.
    Safe to re-run at any time (idempotent).

    Step 1  Create the shared Docker network (prof-net)
    Step 2  Start the Vault container (docker-compose.vault.yml)
    Step 3  Wait for Vault to be reachable
    Step 4  Initialize Vault — saves keys + root token to scripts\.vault-init.json
    Step 5  Unseal Vault using 3 of the 5 keys from the init file
    Step 6  Bootstrap: KV engine, inovar policy, AppRole
    Step 7  Prompt for Inovar credentials and write them into Vault
    Step 8  Write VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID to .env
    Step 9  Run the Vault integration tests to verify everything works

.PARAMETER UnsealOnly
    Skip straight to steps 2+3+5 — just unseal after a reboot.

.EXAMPLE
    .\scripts\vault_setup.ps1
    .\scripts\vault_setup.ps1 -UnsealOnly

.NOTES
    Requirements:
      - Docker Desktop running
      - docker compose v2
      - vault CLI on PATH  OR  auto-routed through docker exec
      - bash on PATH (Git Bash / MSYS2) — needed for vault\bootstrap.sh
#>
[CmdletBinding()]
param(
    [switch]$UnsealOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Paths ─────────────────────────────────────────────────────────────────────
$ScriptDir  = $PSScriptRoot
$RepoRoot   = Split-Path $ScriptDir -Parent
$InitOutput = Join-Path $ScriptDir '.vault-init.json'
$EnvFile    = Join-Path $RepoRoot '.env'

# ── Config ────────────────────────────────────────────────────────────────────
$VaultContainer = 'vault-server'
$VaultAddr      = if ($env:VAULT_ADDR) { $env:VAULT_ADDR } else { 'http://localhost:8200' }
$NetworkName    = 'prof-net'

# ── Output helpers ────────────────────────────────────────────────────────────
function Write-Info    ($Msg) { Write-Host "==> $Msg" -ForegroundColor Cyan }
function Write-Success ($Msg) { Write-Host "OK  $Msg" -ForegroundColor Green }
function Write-Warn    ($Msg) { Write-Host "WARN $Msg" -ForegroundColor Yellow }
function Write-Die     ($Msg) { Write-Host "ERR  $Msg" -ForegroundColor Red; throw $Msg }

# ── Vault CLI wrapper ─────────────────────────────────────────────────────────
# Prefers a host-installed vault binary; falls back to docker exec so the
# script works even if vault is not installed on the host.
function Invoke-VaultCmd {
    param([string[]]$VaultArgs)
    $env:VAULT_ADDR = $VaultAddr
    if (Get-Command vault -ErrorAction SilentlyContinue) {
        & vault @VaultArgs
    }
    else {
        & docker exec -e "VAULT_ADDR=http://0.0.0.0:8200" $VaultContainer vault @VaultArgs
    }
}

# Run a vault command with a specific root/service token, then restore the
# previous value so callers don't leak tokens into child processes.
function Invoke-VaultWithToken {
    param([string]$Token, [string[]]$VaultArgs)
    $prev = $env:VAULT_TOKEN
    $env:VAULT_TOKEN = $Token
    try   { Invoke-VaultCmd -VaultArgs $VaultArgs }
    finally {
        if ($prev) { $env:VAULT_TOKEN = $prev }
        else { Remove-Item Env:VAULT_TOKEN -ErrorAction SilentlyContinue }
    }
}

# ── Step 1: Docker network ────────────────────────────────────────────────────
function New-DockerNetwork {
    Write-Info "Step 1: Ensure Docker network '$NetworkName' exists"
    docker network inspect $NetworkName 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Network '$NetworkName' already exists"
    }
    else {
        docker network create $NetworkName | Out-Null
        Write-Success "Network '$NetworkName' created"
    }
}

# ── Step 2: Start Vault container ─────────────────────────────────────────────
function Start-VaultContainer {
    Write-Info 'Step 2: Start Vault container'
    Push-Location $RepoRoot
    try {
        $running = docker ps --format '{{.Names}}' | Where-Object { $_ -eq $VaultContainer }
        if ($running) {
            Write-Success 'Vault container already running'
        }
        else {
            docker compose -f docker-compose.vault.yml up -d
            if ($LASTEXITCODE -ne 0) { Write-Die 'docker compose failed. Is Docker Desktop running?' }
            Write-Success 'Vault container started'
        }
    }
    finally { Pop-Location }
}

# ── Step 3: Wait for Vault HTTP ───────────────────────────────────────────────
function Wait-ForVault {
    Write-Info "Step 3: Wait for Vault to be reachable at $VaultAddr"
    $retries  = 20
    $reachable = $false
    while ($retries -gt 0) {
        # exit codes: 0 = ok, 1 = uninitialised, 2 = sealed — all mean reachable
        Invoke-VaultCmd -VaultArgs @('status') 2>$null | Out-Null
        if ($LASTEXITCODE -in 0, 1, 2) { $reachable = $true; break }
        Start-Sleep -Seconds 2
        $retries--
        Write-Host '.' -NoNewline
    }
    Write-Host ''
    if (-not $reachable) {
        Write-Die "Vault did not become reachable. Run: docker logs $VaultContainer"
    }
    Write-Success 'Vault is reachable'
}

# ── Step 4: Initialize ────────────────────────────────────────────────────────
function Initialize-Vault {
    Write-Info 'Step 4: Initialize Vault'

    # vault status -format=json exits with code 2 when sealed — ignore the error
    $statusJson = (Invoke-VaultCmd -VaultArgs @('status', '-format=json') 2>$null) -join '' |
                  ConvertFrom-Json -ErrorAction SilentlyContinue

    if ($statusJson -and $statusJson.initialized) {
        Write-Success 'Vault already initialized'
        if (-not (Test-Path $InitOutput)) {
            Write-Warn "Vault is initialized but $InitOutput is missing."
            Write-Die "Restore $InitOutput from your backup (password manager) and re-run."
        }
        return
    }

    Write-Host ''
    Write-Warn 'About to initialize Vault. This generates unseal keys and a root token.'
    Write-Warn "Output will be saved to: $InitOutput"
    Write-Warn 'BACK THIS FILE UP to a password manager immediately after this step.'
    Write-Host ''
    Read-Host 'Press ENTER to continue or Ctrl+C to abort'

    $initResult = Invoke-VaultCmd -VaultArgs @('operator', 'init',
        '-key-shares=5', '-key-threshold=3', '-format=json')

    if ($LASTEXITCODE -ne 0) {
        Write-Die "vault operator init failed (exit $LASTEXITCODE). Is the container actually running? Run: docker logs $VaultContainer"
    }

    # $initResult may be a string array (one line per element) — join before parsing
    $initJson = ($initResult -join '') | ConvertFrom-Json -ErrorAction SilentlyContinue
    if (-not $initJson -or -not $initJson.root_token) {
        Write-Die "vault operator init returned unexpected output. Container may be restarting. Raw output:`n$($initResult -join `"`n`")"
    }

    # Write the raw output (already valid JSON) so nothing is lost
    $initResult | Set-Content -Path $InitOutput -Encoding UTF8

    if (-not (Test-Path $InitOutput) -or (Get-Item $InitOutput).Length -eq 0) {
        Write-Die "Failed to write $InitOutput — file missing or empty after init."
    }

    Write-Success "Vault initialized. Keys saved to $InitOutput"
    Write-Host ''
    Write-Warn 'IMPORTANT: Back up that file to a password manager RIGHT NOW.'
    Write-Warn 'Losing the unseal keys = permanently losing all stored secrets.'
    Write-Host ''
}

# ── Step 5: Unseal ────────────────────────────────────────────────────────────
function Invoke-UnsealVault {
    Write-Info 'Step 5: Unseal Vault'

    $statusJson = (Invoke-VaultCmd -VaultArgs @('status', '-format=json') 2>$null) -join '' |
                  ConvertFrom-Json -ErrorAction SilentlyContinue

    if ($statusJson -and -not $statusJson.sealed) {
        Write-Success 'Vault is already unsealed'
        return
    }

    if (-not (Test-Path $InitOutput)) {
        Write-Die "$InitOutput not found. Cannot unseal without keys. Restore from backup."
    }

    $init = Get-Content $InitOutput -Raw | ConvertFrom-Json
    $k1   = $init.unseal_keys_b64[0]
    $k2   = $init.unseal_keys_b64[1]
    $k3   = $init.unseal_keys_b64[2]

    Invoke-VaultCmd -VaultArgs @('operator', 'unseal', $k1) | Out-Null
    Invoke-VaultCmd -VaultArgs @('operator', 'unseal', $k2) | Out-Null
    Invoke-VaultCmd -VaultArgs @('operator', 'unseal', $k3) | Out-Null

    $statusJson = (Invoke-VaultCmd -VaultArgs @('status', '-format=json') 2>$null) -join '' |
                  ConvertFrom-Json -ErrorAction SilentlyContinue

    if ($statusJson -and -not $statusJson.sealed) {
        Write-Success 'Vault unsealed successfully'
    }
    else {
        Write-Die "Vault is still sealed after 3 keys. Check: docker logs $VaultContainer"
    }
}

# ── Step 6: Bootstrap (KV engine, policy, AppRole) ───────────────────────────
function Invoke-Bootstrap {
    Write-Info 'Step 6: Bootstrap KV engine, policy, AppRole'

    $init = Get-Content $InitOutput -Raw | ConvertFrom-Json
    $tok  = $init.root_token

    # If AppRole is already listed, bootstrap already ran — skip safely
    $authJson = (Invoke-VaultWithToken -Token $tok -VaultArgs @('auth', 'list', '-format=json') 2>$null) -join '' |
                ConvertFrom-Json -ErrorAction SilentlyContinue

    if ($authJson -and $authJson.'approle/') {
        Write-Success 'Bootstrap already applied (AppRole already enabled)'
        return
    }

    # bootstrap.sh is a bash script — requires Git Bash / MSYS2 on Windows
    if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
        Write-Die 'bash not found on PATH. Install Git for Windows (includes Git Bash) and retry.'
    }

    $bootstrapScript = Join-Path $RepoRoot 'vault\bootstrap.sh'
    $env:VAULT_TOKEN = $tok
    try {
        & bash $bootstrapScript $tok
        if ($LASTEXITCODE -ne 0) { Write-Die 'bootstrap.sh failed — check output above.' }
    }
    finally {
        Remove-Item Env:VAULT_TOKEN -ErrorAction SilentlyContinue
    }

    Write-Success 'Bootstrap complete'
}

# ── Step 7: Write Inovar credentials ─────────────────────────────────────────
function Write-InovarCredentials {
    Write-Info 'Step 7: Write Inovar credentials to Vault'

    $init = Get-Content $InitOutput -Raw | ConvertFrom-Json
    $tok  = $init.root_token

    # Skip if credentials already exist
    Invoke-VaultWithToken -Token $tok -VaultArgs @('kv', 'get', '-format=json',
        'secret/inovar/credentials') 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Success 'Inovar credentials already present in Vault'
        Write-Warn 'To update: vault kv put secret/inovar/credentials inovar_username=X inovar_password=Y'
        return
    }

    Write-Host ''
    Write-Host 'Enter your Inovar credentials. They are stored in Vault only — never written to any file.'
    Write-Host ''
    $inovarUser   = Read-Host '  Inovar username'
    $securePass   = Read-Host '  Inovar password' -AsSecureString

    # Convert SecureString -> plain text only for the duration of the Vault API call
    $bstr      = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePass)
    $inovarPass = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

    try {
        Invoke-VaultWithToken -Token $tok -VaultArgs @(
            'kv', 'put', 'secret/inovar/credentials',
            "inovar_username=$inovarUser",
            "inovar_password=$inovarPass"
        ) | Out-Null
    }
    finally {
        # Null out the plain-text variable immediately after use
        $inovarPass = $null
    }

    Write-Success 'Credentials written to secret/inovar/credentials'
}

# ── Step 8: Write .env ────────────────────────────────────────────────────────
function Write-EnvFile {
    Write-Info 'Step 8: Write .env with Vault connection details'

    $init = Get-Content $InitOutput -Raw | ConvertFrom-Json
    $tok  = $init.root_token

    $roleId = (Invoke-VaultWithToken -Token $tok -VaultArgs @(
        'read', '-field=role_id', 'auth/approle/role/inovar-role/role-id'
    )).Trim()

    $sid = (Invoke-VaultWithToken -Token $tok -VaultArgs @(
        'write', '-force', '-field=secret_id', 'auth/approle/role/inovar-role/secret-id'
    )).Trim()

    $target = $EnvFile
    if (Test-Path $target) {
        Write-Warn '.env already exists — writing to .env.vault instead to avoid overwriting'
        $target = Join-Path $RepoRoot '.env.vault'
    }

    # Here-string: variables expand, which is exactly what we want for .env values
    $envContent = @"
# Generated by scripts\vault_setup.ps1 — do not commit.
# Vault mode: credentials are fetched from Vault at startup.

VAULT_ADDR=$VaultAddr
VAULT_ROLE_ID=$roleId
VAULT_SECRET_ID=$sid

# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/prof_db

# Inovar env-only fallback (leave blank when Vault is active)
INOVAR_USERNAME=
INOVAR_PASSWORD=
INOVAR_URL=https://epralima.inovarmais.com/alunos/Inicial.wgx
"@

    $envContent | Set-Content -Path $target -Encoding UTF8
    Write-Success ".env written to $target"

    # Clear the secret-id from memory
    $sid = $null
}

# ── Step 9: Integration tests ─────────────────────────────────────────────────
function Invoke-IntegrationTests {
    Write-Info 'Step 9: Run Vault integration tests'
    Push-Location $RepoRoot
    try {
        $pyBin = '.venv\Scripts\python.exe'
        if (-not (Test-Path $pyBin)) { $pyBin = 'python' }

        $env:VAULT_INTEGRATION_TEST = '1'
        $env:VAULT_ADDR = $VaultAddr
        try {
            & $pyBin -m pytest tests/test_vault_integration.py --asyncio-mode=auto -v
            if ($LASTEXITCODE -eq 0) {
                Write-Success 'All integration tests passed'
            }
            else {
                Write-Warn 'Some tests failed — check output above.'
                Write-Warn 'The app may still work; tests just verify the Vault wire-up.'
            }
        }
        finally {
            Remove-Item Env:VAULT_INTEGRATION_TEST -ErrorAction SilentlyContinue
        }
    }
    finally { Pop-Location }
}

# ── Summary ───────────────────────────────────────────────────────────────────
function Write-SetupSummary {
    Write-Host ''
    Write-Host '================================================================' -ForegroundColor Green
    Write-Host ' Vault setup complete!' -ForegroundColor Green
    Write-Host '================================================================' -ForegroundColor Green
    Write-Host ''
    Write-Host '  Next steps:'
    Write-Host '    1. Start the app:  docker compose up -d'
    Write-Host '    2. Sync schedule:  Invoke-RestMethod -Method Post http://localhost:8000/api/v1/horarios/sync'
    Write-Host '    3. Health check:   Invoke-RestMethod http://localhost:8000/health'
    Write-Host ''
    Write-Host '  After any reboot Vault wakes SEALED. To unseal:'
    Write-Host '    .\scripts\vault_setup.ps1 -UnsealOnly'
    Write-Host ''
}

# ── Entrypoint ────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host 'prof-horario — Vault Setup' -ForegroundColor White
Write-Host "Vault address: $VaultAddr"
Write-Host ''

if ($UnsealOnly) {
    Start-VaultContainer
    Wait-ForVault
    Invoke-UnsealVault
    Write-Success 'Vault unsealed. Start the app with: docker compose up -d'
    exit 0
}

New-DockerNetwork
Start-VaultContainer
Wait-ForVault
Initialize-Vault
Invoke-UnsealVault
Invoke-Bootstrap
Write-InovarCredentials
Write-EnvFile
Invoke-IntegrationTests
Write-SetupSummary
