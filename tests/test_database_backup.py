import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


@unittest.skipUnless(os.name == 'nt' and POWERSHELL.exists(), 'Windows PowerShell tests')
class DatabaseBackupTests(unittest.TestCase):
    def run_ps(self, arguments, env=None):
        return subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', *arguments],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40, env=env)

    def test_all_powershell_scripts_parse(self):
        env = os.environ.copy()
        env['MES_BACKUP_TEST_ROOT'] = str(ROOT / 'scripts')
        result = self.run_ps(['-Command', '''
$files = @('backup_database.ps1','setup_database_backup_credentials.ps1','install_database_backup_task.ps1')
foreach ($file in $files) {
  $tokens = $null; $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $env:MES_BACKUP_TEST_ROOT $file), [ref]$tokens, [ref]$errors)
  if ($errors.Count) { $errors | Out-String | Write-Output; exit 1 }
}
exit 0
'''], env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_failed_preflight_preserves_existing_archive_and_logs_failure(self):
        config = json.loads((ROOT / 'scripts/database_backup.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(prefix='backup test ') as folder:
            base = Path(folder)
            output = base / 'output with spaces'
            output.mkdir()
            destination = output / 'postgres_latest.dump'
            destination.write_bytes(b'previous backup sentinel')
            config.update(BackupDirectory=str(output), StagingDirectory=str(base / 'staging'),
                          PasswordFile=str(base / 'nonexistent.pgpass'))
            config_path = base / 'config.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')
            result = self.run_ps(['-File', str(ROOT / 'scripts/backup_database.ps1'), '-ConfigPath', str(config_path)])
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(destination.read_bytes(), b'previous backup sentinel')
            self.assertEqual(list(output.iterdir()), [destination])
            logs = list((base / 'staging/logs').glob('*.log'))
            self.assertEqual(len(logs), 1)
            self.assertIn('Password file missing', logs[0].read_text(encoding='utf-8-sig'))

    def test_native_client_invocation_with_program_files_path(self):
        env = os.environ.copy()
        env['MES_BACKUP_TEST_SCRIPT'] = str(ROOT / 'scripts/backup_database.ps1')
        result = self.run_ps(['-Command', '''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:MES_BACKUP_TEST_SCRIPT,[ref]$tokens,[ref]$errors)
foreach ($name in @('Write-BackupLog','Quote-NativeArgument','Invoke-PgTool','Find-PgBin')) {
  $function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
  Invoke-Expression $function.Extent.Text
}
$Config = [pscustomobject]@{ToolTimeoutSeconds=15}
$PasswordFile = 'nonexistent-test.pgpass'
$LogPath = $null
try { $bin = Find-PgBin '' } catch { exit 77 }
$output = Invoke-PgTool -Executable (Join-Path $bin 'pg_dump.exe') -ToolArguments @('--version')
if ($output -notmatch 'PostgreSQL') { exit 1 }
exit 0
'''], env)
        if result.returncode == 77:
            self.skipTest('PostgreSQL client tools unavailable')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_atomic_publish_handles_spaces_without_deleting_previous_first(self):
        env = os.environ.copy()
        env['MES_BACKUP_TEST_SCRIPT'] = str(ROOT / 'scripts/backup_database.ps1')
        with tempfile.TemporaryDirectory(prefix='backup publish ') as folder:
            env['MES_BACKUP_TEST_DIR'] = folder
            result = self.run_ps(['-Command', '''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:MES_BACKUP_TEST_SCRIPT,[ref]$tokens,[ref]$errors)
$function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Publish-Archive' }, $true)
Invoke-Expression $function.Extent.Text
$source = Join-Path $env:MES_BACKUP_TEST_DIR 'new backup.tmp'
$target = Join-Path $env:MES_BACKUP_TEST_DIR 'latest backup.dump'
[IO.File]::WriteAllText($source, 'new archive')
[IO.File]::WriteAllText($target, 'old archive')
Publish-Archive $source $target
if ([IO.File]::ReadAllText($target) -ne 'new archive' -or (Test-Path $source)) { exit 1 }
exit 0
'''], env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
