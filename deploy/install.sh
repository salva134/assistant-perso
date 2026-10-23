#!/usr/bin/env bash
# Préparation d'un serveur Ubuntu 24.04 neuf pour Marcus. À lancer une fois, en root.
# Sans risque de le relancer. Le code doit déjà être dans /home/marcus/assistant-perso.
set -euo pipefail

APP_USER=marcus

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq python3 python3-venv ufw unattended-upgrades curl

# Mises à jour de sécurité automatiques
dpkg-reconfigure -f noninteractive unattended-upgrades

# Pare-feu : seul SSH peut entrer (Marcus va chercher ses messages, rien ne vient à lui)
ufw allow OpenSSH
ufw --force enable

# Connexion au serveur par clé uniquement, jamais par mot de passe
cat > /etc/ssh/sshd_config.d/10-marcus.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
systemctl reload ssh

timedatectl set-timezone Europe/Paris

# Utilisateur dédié, sans droits d'administrateur ni connexion possible
id -u "$APP_USER" > /dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin "$APP_USER"

"$(dirname "$0")/update.sh"
