# Run manually as the same Windows account used to set up the pgpass file.
# Re-run after editing DailyTime, TaskName, or moving the project/config.
[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'database_backup.json'))
$ErrorActionPreference = 'Stop'
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$Time = [datetime]::ParseExact($Config.DailyTime, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
$PasswordFile = [Environment]::ExpandEnvironmentVariables($Config.PasswordFile)
if (-not (Test-Path -LiteralPath $PasswordFile)) { throw 'Run setup_database_backup_credentials.ps1 first.' }
$Script = Join-Path $PSScriptRoot 'backup_database.ps1'
$PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ConfigPath "{1}"' -f $Script, $ConfigPath
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $PSScriptRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 15) -ExecutionTimeLimit (New-TimeSpan -Hours 8)
# Do not register a known-broken schedule: validate a full backup first.
Write-Host 'Running a full backup before registering the daily task...'
& $PowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Script -ConfigPath $ConfigPath
if ($LASTEXITCODE -ne 0) { throw 'Initial backup failed. Task was not registered/updated. Resolve the backup error first.' }
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$WindowsCredential = Get-Credential -UserName $Identity.Name -Message 'Windows sign-in password for unattended Task Scheduler execution (not the PostgreSQL password).'
if (-not $WindowsCredential) { throw 'Task setup cancelled.' }
$CredentialSid = (New-Object Security.Principal.NTAccount($WindowsCredential.UserName)).Translate([Security.Principal.SecurityIdentifier])
if ($CredentialSid.Value -ne $Identity.User.Value) { throw 'Use the same Windows account that owns the pgpass file.' }
Register-ScheduledTask -TaskName $Config.TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description 'Validated full PostgreSQL database backup, configured through database_backup.json.' `
    -User $WindowsCredential.UserName -Password $WindowsCredential.GetNetworkCredential().Password -Force | Out-Null
Write-Host "Registered: $($Config.TaskName) daily at $($Config.DailyTime)."
Write-Host 'The task runs whether or not you are logged on; Windows stores its credential securely.'
$Logs = Join-Path ([Environment]::ExpandEnvironmentVariables($Config.StagingDirectory)) 'logs'
Write-Host "Check Task Scheduler Last Run Result (0 = success) and $Logs."
