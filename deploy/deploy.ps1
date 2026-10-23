# Envoie la version committée de Marcus sur le serveur et le relance.
# Usage (depuis le dossier du projet) : .\deploy\deploy.ps1 -Serveur 1.2.3.4 [-Premiere]
#   -Premiere : première installation (prépare aussi le serveur avec install.sh)
param(
    [Parameter(Mandatory = $true)][string]$Serveur,
    [switch]$Premiere
)
$ErrorActionPreference = "Stop"

$cle = Join-Path $env:USERPROFILE ".ssh\marcus_ed25519"
$ssh = @("-i", $cle, "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes")
$cible = "root@$Serveur"
$archive = Join-Path $env:TEMP "marcus.tar"

# Seul le code committé part : rien de local ni de secret par erreur
git archive --format=tar -o $archive HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive a échoué" }

scp @ssh $archive "${cible}:/tmp/marcus.tar"
scp @ssh .env "${cible}:/tmp/marcus.env"
Remove-Item $archive

# On remplace le code mais on garde l'environnement Python et le .env
$remplacer = "set -e; d=/home/marcus/assistant-perso; mkdir -p `$d; " +
    "find `$d -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {} +; " +
    "tar -xf /tmp/marcus.tar -C `$d; mv /tmp/marcus.env `$d/.env; rm /tmp/marcus.tar"
ssh @ssh $cible $remplacer

if ($Premiere) {
    ssh @ssh $cible "bash /home/marcus/assistant-perso/deploy/install.sh"
} else {
    ssh @ssh $cible "bash /home/marcus/assistant-perso/deploy/update.sh"
}
