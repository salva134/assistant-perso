# Redemarre Marcus proprement (apres une mise a jour du code) : arret complet, puis relance de la tache.
# Usage : powershell -ExecutionPolicy Bypass -File deploy\windows\redemarrer.ps1
# Fichier en ASCII : Windows PowerShell 5.1 lit mal les accents des scripts sans BOM.

& (Join-Path $PSScriptRoot "arreter.ps1")
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName "Marcus"
Write-Output "Marcus relance."
