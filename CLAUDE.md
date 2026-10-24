# Assistant perso d'Hugo

## Le projet
Bot Telegram personnel (un seul utilisateur) : mémoire, vocaux, photos, rappels (répétés),
Google Agenda, recherche web, appels passés et reçus via Vapi + un numéro Twilio.
Plan complet : @docs/PLAN.md. On avance phase par phase, jamais deux à la fois.

## Stack
- Python 3.12+ (3.13 sur le PC), python-telegram-bot (polling), anthropic, openai, psycopg (Supabase,
  pgvector côté base), aiohttp + tunnel cloudflared pour la ligne directe pendant un appel.
- Discussion : claude-haiku-5-5. Appels (dans Vapi) : claude-sonnet-5, voix Cartesia « Henri »,
  écoute Deepgram nova-3 en français. Fuseau horaire : Europe/Paris partout.
- Versions figées : requirements.txt (dépendances directes) et requirements-lock.txt (tout).
- GitHub Actions (.github/workflows/tests.yml) : ruff + pytest en Python 3.12 et 3.13 à chaque envoi.

## Règles
- IMPORTANT : ne jamais afficher, journaliser ni committer le contenu de .env.
- Toute nouvelle variable va aussi dans .env.example, avec une valeur vide.
- Ne jamais lancer de vrai appel téléphonique sans un accord explicite d'Hugo dans la conversation.
- Après chaque changement : lancer `pytest -q` et montrer le résultat ; `ruff check app scripts tests` doit passer.
- Fin de phase : commit avec le message « phase N : … ».
- Réponses, commentaires et messages de commit en français.

## Commandes
- Marcus tourne en permanence via la tâche planifiée Windows « Marcus » : la redémarrer
  après un changement de code (`powershell -ExecutionPolicy Bypass -File deploy\windows\redemarrer.ps1`),
  et ne jamais lancer `python -m app.main` en parallèle. Journal : `logs/marcus.log`.
- Arrêter Marcus : `deploy\windows\arreter.ps1` (même commande). `Stop-ScheduledTask` seul ne ferme pas
  Python : l'ancienne copie reste et bloque Telegram (« Conflict ») et la ligne directe.
- Lancer le bot à la main (après `arreter.ps1`) : `python -m app.main`
- Tests : `pytest -q` (inclut des tests sur la vraie base dans un schéma temporaire ; `SKIP_DB_TESTS=1` pour les sauter)
- Vérifier les clés : `python scripts/check_keys.py`
