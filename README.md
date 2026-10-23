# Assistant perso IA

Assistant personnel sur Telegram, réservé à Hugo : V1 discussion + mémoire, V2 rappels et agenda, V3 appels sortants.
Plan complet : [docs/PLAN.md](docs/PLAN.md).

## Démarrage

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # puis remplir les clés
python scripts/check_keys.py
```

Dans Supabase, coller `sql/schema.sql` dans le SQL Editor et l'exécuter.

## Structure

- `scripts/check_keys.py` : vérifie chaque clé API du `.env`
- `sql/schema.sql` : tables `messages`, `memories`, `reminders`
- `.env.example` : modèle des variables (le vrai `.env` n'est jamais committé)
