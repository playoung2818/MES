# Store the PostgreSQL password in an ACL-protected, local pgpass file.
[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'database_backup.json'))
$ErrorActionPreference = 'Stop'
$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$PasswordFile = [Environment]::ExpandEnvironmentVariables($Config.PasswordFile)
$Directory = Split-Path $PasswordFile -Parent
New-Item -ItemType Directory -Force -Path $Directory | Out-Null
$Secret = Read-Host "PostgreSQL password for $($Config.Username) at $($Config.Host) (not your Windows password)" -AsSecureString
$Pointer = [IntPtr]::Zero
try {
    # Restrict access before writing the password. PostgreSQL requires pgpass in
    # plaintext; it is kept outside OneDrive, readable only by this user/SYSTEM.
    if (-not (Test-Path -LiteralPath $PasswordFile)) { New-Item -ItemType File -Path $PasswordFile | Out-Null }
    $UserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $Acl = New-Object Security.AccessControl.FileSecurity
    $Acl.SetOwner($UserSid)
    $Acl.SetAccessRuleProtection($true, $false)
    $Acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($UserSid, 'FullControl', 'Allow')))
    $Acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM', 'FullControl', 'Allow')))
    Set-Acl -LiteralPath $PasswordFile -AclObject $Acl
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    $Plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    if ($Plain -match '[\r\n]') { throw 'Password cannot contain a newline in a pgpass file.' }
    function Escape-Pgpass([string]$Value) { return $Value.Replace('\', '\\').Replace(':', '\:') }
    $Line = (@($Config.Host, [string]$Config.Port, $Config.Database, $Config.Username, $Plain) |
             ForEach-Object { Escape-Pgpass ([string]$_) }) -join ':'
    [IO.File]::WriteAllText($PasswordFile, $Line + "`n", (New-Object Text.UTF8Encoding($false)))
    Write-Host "Password file configured for $([Security.Principal.WindowsIdentity]::GetCurrent().Name)."
    Write-Host 'Run backup_database.ps1 once successfully before registering the daily task.'
} finally {
    if ($Pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer) }
    $Plain = $null
    $Line = $null
    if ($Secret) { $Secret.Dispose() }
}
