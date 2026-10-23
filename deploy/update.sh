#!/usr/bin/env bash
# Met Marcus à jour après l'arrivée d'un nouveau code (lancé par deploy.ps1, en root).
set -euo pipefail

APP_USER=marcus
APP_DIR=/home/$APP_USER/assistant-perso

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"
chmod +x "$APP_DIR"/deploy/*.sh
install -d -o "$APP_USER" -g "$APP_USER" -m 700 "/home/$APP_USER/backups"

# Environnement Python et dépendances
[ -d "$APP_DIR/.venv" ] || sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# Services systemd : le bot et sa sauvegarde hebdomadaire
install -m 644 "$APP_DIR"/deploy/assistant.service /etc/systemd/system/
install -m 644 "$APP_DIR"/deploy/assistant-backup.service /etc/systemd/system/
install -m 644 "$APP_DIR"/deploy/assistant-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet assistant.service assistant-backup.timer
systemctl start assistant-backup.timer
systemctl restart assistant.service

sleep 5
systemctl --no-pager --lines=5 status assistant.service
