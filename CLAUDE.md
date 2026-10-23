# Assistant perso d'Hugo

## Le projet
Bot Telegram personnel (un seul utilisateur) : mémoire, vocaux, rappels, Google Agenda,
appels via Vapi et une ligne OVHcloud. Plan complet : @docs/PLAN.md.
On avance phase par phase, jamais deux à la fois.

## Stack
- Python 3.12+ (3.13 sur le PC), python-telegram-bot (polling), anthropic, openai, psycopg + pgvector.
- Modèle par défaut : claude-haiku-5-5. Fuseau horaire : Europe/Paris partout.

## Règles
- IMPORTANT : ne jamais afficher, journaliser ni committer le contenu de .env.
- Toute nouvelle variable va aussi dans .env.example, avec une valeur vide.
- Ne jamais lancer de vrai appel téléphonique sans un accord explicite d'Hugo dans la conversation.
- Après chaque changement : lancer `pytest -q` et montrer le résultat.
- Fin de phase : commit avec le message « phase N : … ».
- Réponses, commentaires et messages de commit en français.

## Commandes
- Marcus tourne en permanence via la tâche planifiée Windows « Marcus » : la redémarrer
  après un changement de code (`Stop-ScheduledTask -TaskName Marcus; Start-ScheduledTask -TaskName Marcus`),
  et ne jamais lancer `python -m app.main` en parallèle. Journal : `logs/marcus.log`.
- Lancer le bot à la main (tâche arrêtée) : `python -m app.main`
- Tests : `pytest -q`
- Vérifier les clés : `python scripts/check_keys.py`
