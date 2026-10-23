#!/usr/bin/env bash
# Appelé par systemd quand Marcus s'arrête. Un arrêt voulu (systemctl stop/restart)
# donne SERVICE_RESULT=success : rien à signaler. Sinon, on prévient Hugo sur Telegram.
[ "${SERVICE_RESULT:-success}" = "success" ] && exit 0

ENV_FILE="$(dirname "$0")/../.env"
token=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
chat_id=$(grep -E '^TELEGRAM_ALLOWED_USER_ID=' "$ENV_FILE" | cut -d= -f2-)

curl -s --max-time 10 "https://api.telegram.org/bot${token}/sendMessage" \
  -d chat_id="$chat_id" \
  --data-urlencode text="⚠️ Marcus s'est arrêté (${SERVICE_RESULT}, code ${EXIT_STATUS:-?}). Il redémarre tout seul dans 10 secondes." \
  > /dev/null || true
