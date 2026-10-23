# Cree (ou recree) la tache planifiee "Marcus" : demarrage avec le PC, sans attendre
# l'ouverture de session, sans limite de duree, une seule copie a la fois.
# Usage : powershell -ExecutionPolicy Bypass -File deploy\windows\installer_tache.ps1
# Pour la retirer : Unregister-ScheduledTask -TaskName Marcus -Confirm:$false

$script = Join-Path $PSScriptRoot "marcus.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$declencheur = New-ScheduledTaskTrigger -AtStartup
# S4U : tourne avec ton compte sans enregistrer ton mot de passe
$compte = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U
$reglages = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "Marcus" -Description "Marcus, assistant personnel sur Telegram" `
    -Action $action -Trigger $declencheur -Principal $compte -Settings $reglages -Force | Out-Null
Write-Output "Tache Marcus creee."
