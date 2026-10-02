# Full PostgreSQL database backup. No Python, no passwords in this script/config.
[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'database_backup.json'))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$WorkDir = $null
$PendingFile = $null
$LockFile = $null
$LogPath = $null

function Write-BackupLog([string]$Message) {
    $Line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $Line
    if ($LogPath) {
        try { Add-Content -LiteralPath $LogPath -Value $Line -Encoding UTF8 } catch { Write-Warning 'Unable to write local backup log.' }
    }
}

function Find-PgBin([string]$Explicit) {
    if ($Explicit) { return $Explicit }
    $Candidates = @()
    foreach ($Root in @((Join-Path $env:ProgramFiles 'PostgreSQL'), 'D:\PostgreSQL')) {
        if (Test-Path -LiteralPath $Root) {
            $Candidates += @(Get-ChildItem -LiteralPath $Root -Directory |
                ForEach-Object { Join-Path $_.FullName 'bin' })
        }
    }
    # Custom-drive installations are registered here, not necessarily under C:.
    $RegistryRoots = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*')
    foreach ($Entry in @(Get-ItemProperty $RegistryRoots -ErrorAction SilentlyContinue)) {
        $Name = $Entry.PSObject.Properties['DisplayName']
        $Location = $Entry.PSObject.Properties['InstallLocation']
        if ($Name -and $Location -and $Name.Value -match '^PostgreSQL\b' -and $Location.Value) {
            $Candidates += Join-Path $Location.Value 'bin'
        }
    }
    $Command = Get-Command pg_dump.exe -ErrorAction SilentlyContinue
    if ($Command) { $Candidates += Split-Path $Command.Source -Parent }
    $Installed = @(foreach ($Bin in @($Candidates | Select-Object -Unique)) {
        $Dump = Join-Path $Bin 'pg_dump.exe'
        if (Test-Path -LiteralPath $Dump) {
            $Info = (Get-Item -LiteralPath $Dump).VersionInfo
            [pscustomobject]@{Bin=$Bin; Major=$Info.FileMajorPart; Minor=$Info.FileMinorPart}
        }
    })
    if ($Installed.Count) {
        $Newest = @($Installed | Sort-Object Major, Minor -Descending)[0]
        return $Newest.Bin
    }
    throw 'PostgreSQL client tools not found. Install version 17 or newer, or set PgBinDirectory.'
}

function Quote-NativeArgument([string]$Argument) {
    # Windows C-runtime argument escaping, including paths containing spaces.
    return '"' + (($Argument -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Invoke-PgTool([string]$Executable, [string[]]$ToolArguments) {
    $Info = New-Object System.Diagnostics.ProcessStartInfo
    $Info.FileName = $Executable
    $Info.Arguments = (($ToolArguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' ')
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Info.RedirectStandardOutput = $true
    $Info.RedirectStandardError = $true
    $Info.EnvironmentVariables['PGPASSFILE'] = $PasswordFile
    $Info.EnvironmentVariables['PGCONNECT_TIMEOUT'] = '20'
    $Info.EnvironmentVariables.Remove('PGPASSWORD')
    $Process = New-Object System.Diagnostics.Process
    $Process.StartInfo = $Info
    try {
        if (-not $Process.Start()) { throw 'Unable to start PostgreSQL client tool.' }
        $Output = $Process.StandardOutput.ReadToEndAsync()
        $Errors = $Process.StandardError.ReadToEndAsync()
        if (-not $Process.WaitForExit([int]$Config.ToolTimeoutSeconds * 1000)) {
            $Process.Kill()
            $Process.WaitForExit()
            throw 'PostgreSQL tool timed out. Previous finalized backup was retained.'
        }
        $Process.WaitForExit()
        $Stdout = $Output.GetAwaiter().GetResult()
        $Stderr = $Errors.GetAwaiter().GetResult()
        if ($Process.ExitCode -ne 0) {
            if ($Stderr.Length -gt 4000) { $Stderr = $Stderr.Substring(0, 4000) }
            throw ('{0} failed (exit {1}): {2}' -f [IO.Path]::GetFileName($Executable), $Process.ExitCode, $Stderr.Trim())
        }
        if ($Stderr.Trim()) { Write-BackupLog ('Client notice: ' + $Stderr.Trim()) }
        return $Stdout
    } finally { $Process.Dispose() }
}

function Publish-Archive([string]$Source, [string]$Destination) {
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        try {
            if (Test-Path -LiteralPath $Destination) {
                # Atomic same-directory replacement: never delete the previous dump first.
                [IO.File]::Replace($Source, $Destination, [System.Management.Automation.Language.NullString]::Value)
            } else { [IO.File]::Move($Source, $Destination) }
            return
        } catch {
            if ($Attempt -eq 5) { throw }
            Start-Sleep -Seconds 3
        }
    }
}

try {
    $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($Config.Mode -notin @('overwrite', 'dated')) { throw 'Mode must be overwrite or dated.' }
    if ([int]$Config.ToolTimeoutSeconds -lt 1 -or [int]$Config.ToolTimeoutSeconds -gt 7200) { throw 'ToolTimeoutSeconds must be 1..7200.' }
    if ([int]$Config.RetentionDays -lt 1) { throw 'RetentionDays must be positive.' }
    foreach ($Field in @('Host', 'Database', 'Username', 'BackupDirectory', 'StagingDirectory', 'PasswordFile')) {
        if ([string]::IsNullOrWhiteSpace([string]$Config.$Field)) { throw "$Field must not be empty." }
    }
    if ([int]$Config.Port -lt 1 -or [int]$Config.Port -gt 65535) { throw 'Port must be 1..65535.' }
    if ([IO.Path]::GetFileName($Config.Filename) -ne $Config.Filename -or $Config.Filename -notmatch '\.dump$') {
        throw 'Filename must be a plain .dump filename, not a path.'
    }
    $StagingDir = [Environment]::ExpandEnvironmentVariables($Config.StagingDirectory)
    $BackupDir = [Environment]::ExpandEnvironmentVariables($Config.BackupDirectory)
    $PasswordFile = [Environment]::ExpandEnvironmentVariables($Config.PasswordFile)
    New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null
    $LogDir = Join-Path $StagingDir 'logs'
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $LogPath = Join-Path $LogDir ('backup_{0}.log' -f (Get-Date -Format 'yyyy-MM-dd'))
    $LockFile = [IO.File]::Open((Join-Path $StagingDir 'database_backup.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    Write-BackupLog ('START database={0}; mode={1}' -f $Config.Database, $Config.Mode)
    if (-not (Test-Path -LiteralPath $PasswordFile)) { throw 'Password file missing. Run scripts/setup_database_backup_credentials.ps1 as the task account first.' }
    $PgBin = Find-PgBin $Config.PgBinDirectory
    $PgDump = Join-Path $PgBin 'pg_dump.exe'
    $PgRestore = Join-Path $PgBin 'pg_restore.exe'
    $Psql = Join-Path $PgBin 'psql.exe'
    foreach ($Tool in @($PgDump, $PgRestore, $Psql)) {
        if (-not (Test-Path -LiteralPath $Tool)) { throw ('Missing client tool: ' + $Tool) }
    }
    $ConnectionArgs = @('--host', [string]$Config.Host, '--port', [string]$Config.Port, '--username', [string]$Config.Username, '--dbname', [string]$Config.Database, '--no-password')
    $Version = Invoke-PgTool -Executable $PgDump -ToolArguments @('--version')
    if ($Version -notmatch 'PostgreSQL\)\s+(\d+)') { throw 'Cannot identify pg_dump version.' }
    $ClientMajor = [int]$Matches[1]
    $ServerVersion = Invoke-PgTool -Executable $Psql -ToolArguments ($ConnectionArgs + @('--no-psqlrc', '--tuples-only', '--no-align', '--command', 'SHOW server_version_num'))
    $ServerMajor = [int][Math]::Floor([double]($ServerVersion.Trim()) / 10000)
    if ($ClientMajor -lt $ServerMajor) { throw "pg_dump $ClientMajor is older than server $ServerMajor. Install PostgreSQL $ServerMajor client tools or newer. Existing backup was retained." }
    Write-BackupLog ('Client: ' + $Version.Trim())
    $WorkDir = Join-Path $StagingDir ('work_' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $WorkDir | Out-Null
    $Dump = Join-Path $WorkDir 'database.dump'
    # No --table/schema filters: includes every schema/table and all database objects.
    Invoke-PgTool -Executable $PgDump -ToolArguments ($ConnectionArgs + @('--format=custom', '--file', $Dump)) | Out-Null
    if ((Get-Item -LiteralPath $Dump).Length -le 0) { throw 'Empty dump; previous backup retained.' }
    # Read/decompress the entire archive without executing SQL against any database.
    Invoke-PgTool -Executable $PgRestore -ToolArguments @('--file', 'NUL', $Dump) | Out-Null
    $Hash = (Get-FileHash -LiteralPath $Dump -Algorithm SHA256).Hash
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    $Prefix = [IO.Path]::GetFileNameWithoutExtension($Config.Filename) -replace '_latest$', ''
    $Filename = if ($Config.Mode -eq 'overwrite') { $Config.Filename } else { '{0}_{1}.dump' -f $Prefix, (Get-Date -Format 'yyyyMMdd_HHmmss') }
    $Destination = Join-Path $BackupDir $Filename
    $PendingFile = Join-Path $BackupDir ('.pending_' + [guid]::NewGuid().ToString('N') + '.tmp')
    Copy-Item -LiteralPath $Dump -Destination $PendingFile
    if ((Get-FileHash -LiteralPath $PendingFile -Algorithm SHA256).Hash -ne $Hash) { throw 'Destination copy checksum mismatch; previous backup retained.' }
    Publish-Archive $PendingFile $Destination
    $PendingFile = $null
    Write-BackupLog ('SUCCESS file={0}; bytes={1}; SHA256={2}' -f $Destination, (Get-Item -LiteralPath $Destination).Length, $Hash)
    if ($Config.Mode -eq 'dated') {
        # Prune only this script's dated archives, and only after publishing a valid one.
        $Pattern = '^' + [regex]::Escape($Prefix) + '_\d{8}_\d{6}\.dump$'
        Get-ChildItem -LiteralPath $BackupDir -File | Where-Object {
            $_.Name -match $Pattern -and $_.FullName -ne $Destination -and $_.LastWriteTime -lt (Get-Date).AddDays(-[int]$Config.RetentionDays)
        } | ForEach-Object {
            try { Remove-Item -LiteralPath $_.FullName } catch { Write-BackupLog ('Retention warning: ' + $_.Name) }
        }
    }
    exit 0
} catch {
    Write-BackupLog ('FAILED: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($PendingFile -and (Test-Path -LiteralPath $PendingFile)) { Remove-Item -LiteralPath $PendingFile -Force -ErrorAction SilentlyContinue }
    if ($WorkDir -and (Test-Path -LiteralPath $WorkDir)) { Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue }
    if ($LockFile) { $LockFile.Dispose() }
}
