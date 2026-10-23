# Assistant perso IA

Marcus, assistant personnel sur Telegram, réservé à Hugo : V1 discussion + mémoire, V2 rappels et agenda, V3 appels sortants.
Plan complet : [docs/PLAN.md](docs/PLAN.md).

## Démarrage

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # puis remplir les clés
python scripts/apply_schema.py  # crée les tables (sans risque de le relancer)
python scripts/check_keys.py    # vérifie chaque service
python -m app.main              # lance Marcus
```

## Marcus tourne sur le PC

Une tâche planifiée Windows « Marcus » le lance au démarrage du PC (même sans ouvrir de session), le relance 10 s après un plantage et prévient sur Telegram. Journal : `logs/marcus.log`. Sauvegarde chaque dimanche à 3 h 30 dans `backups/`.

```bash
powershell -ExecutionPolicy Bypass -File deploy\windows\installer_tache.ps1
```

- Arrêter Marcus : `Stop-ScheduledTask -TaskName Marcus`
- Le relancer (après une mise à jour du code) : `Stop-ScheduledTask -TaskName Marcus; Start-ScheduledTask -TaskName Marcus`
- Ne jamais lancer `python -m app.main` en même temps que la tâche : deux copies se gênent (« Conflict »).

Pour passer un jour sur un serveur Linux : `.\deploy\deploy.ps1 -Serveur <IP> -Premiere` (voir `deploy/`).

## Commandes Telegram

- `/souvenirs` : ce que Marcus sait de toi, numéroté
- `/oublie <numéro ou texte>` : efface un souvenir
- `/corrige <numéro ou texte> -> <nouveau texte>` : corrige un souvenir
- `/couts` : dépense du jour et du mois (plafond : `DAILY_BUDGET_USD`, 1 $ par défaut)

## Structure

- `app/main.py` : bot Telegram (liste blanche, messages, commandes)
- `app/llm.py` : appels à Claude et boucle d'outils
- `app/memory.py` : messages, souvenirs, extraction après chaque échange
- `app/usage.py` : journal des coûts et plafond quotidien
- `app/voice.py` : transcription des vocaux (5 minutes au maximum)
- `app/config.py`, `app/db.py`, `app/dates.py` : configuration, base, dates
- `prompts/` : personnalité de Marcus et consignes de mémoire
- `sql/schema.sql` : tables `messages`, `memories`, `reminders`, `usage`
- `scripts/` : vérification des clés, application du schéma, test rapide de la mémoire
- `tests/` : `pytest -q`
