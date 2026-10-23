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
- `app/config.py`, `app/db.py`, `app/dates.py` : configuration, base, dates
- `prompts/` : personnalité de Marcus et consignes de mémoire
- `sql/schema.sql` : tables `messages`, `memories`, `reminders`, `usage`
- `scripts/` : vérification des clés, application du schéma, test rapide de la mémoire
- `tests/` : `pytest -q`
