# Arrete Marcus pour de bon : la tache planifiee, puis les copies de Python qu'elle laisse tourner.
# Stop-ScheduledTask seul ne ferme que la fenetre PowerShell de la tache : Python continue, garde
# Telegram ("Conflict") et le port de la ligne directe.
# Usage : powershell -ExecutionPolicy Bypass -File deploy\windows\arreter.ps1
# -CopiesSeulement : ferme les copies restantes sans toucher a la tache (utilise par marcus.ps1).
# Fichier en ASCII : Windows PowerShell 5.1 lit mal les accents des scripts sans BOM.

param([switch]$CopiesSeulement)

$projet = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$venv = [WildcardPattern]::Escape((Join-Path $projet ".venv"))

if (-not $CopiesSeulement) {
    Stop-ScheduledTask -TaskName "Marcus" -ErrorAction SilentlyContinue
}

# Le python.exe du .venv en lance un second (le vrai interpreteur) : on ferme les deux.
$pythons = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'")
$marcus = @($pythons | Where-Object {
    $_.CommandLine -match '-m\s+app\.main' -and
    ($_.CommandLine -like "*$venv*" -or $_.ExecutablePath -like "$venv*")
})
$ids = @($marcus | ForEach-Object { $_.ProcessId })
$enfants = @($pythons | Where-Object { $ids -contains $_.ParentProcessId })
$copies = @($marcus + $enfants | Sort-Object ProcessId -Unique)

foreach ($copie in $copies) {
    Stop-Process -Id $copie.ProcessId -Force -ErrorAction SilentlyContinue
}
# Le tunnel de la ligne directe, reste ouvert apres un arret brutal
Get-Process -Name cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Output "Processus de Marcus fermes : $($copies.Count)"
