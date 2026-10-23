# Lance Marcus en boucle sur le PC (appele par la tache planifiee "Marcus").
# S'il s'arrete en erreur, Hugo est prevenu sur Telegram et Marcus repart 10 s plus tard.
# Fichier en ASCII : Windows PowerShell 5.1 lit mal les accents des scripts sans BOM.

$projet = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $projet
New-Item -ItemType Directory -Force (Join-Path $projet "logs"), (Join-Path $projet "backups") | Out-Null

$python = Join-Path $projet ".venv\Scripts\python.exe"
$env:LOG_FILE = Join-Path $projet "logs\marcus.log"
$env:BACKUP_DIR = Join-Path $projet "backups"
$env:PYTHONUTF8 = "1"

while ($true) {
    & $python -m app.main
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        & $python -m app.alert arret $code
    }
    Start-Sleep -Seconds 10
}
