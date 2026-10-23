# Assistant perso IA — plan de projet (version de travail pour Claude Code)

Ce fichier regroupe le plan et le guide pas à pas. Claude Code s'y réfère à chaque phase.
On avance phase par phase, jamais deux à la fois.

## Révision du 23/10/2026 (après les phases 0 et 1)

Hugo a donné carte blanche pour améliorer le plan. Changements, et pourquoi :

1. **Nouvel ordre : V1 complète, puis serveur, puis le reste.** Le plan disait à la fois « tester le téléphone juste après la phase 1 » et « finir la V1 et s'en servir 2 semaines avant de toucher aux appels ». Or pour s'en servir, le bot doit tourner PC éteint, et les rappels de la phase 4 ne sonnent pas si le PC dort. Ordre retenu : mémoire → vocaux → serveur → rappels et agenda → test téléphonie → appels.
2. **Mémoire plus fiable.** Tant qu'il y a peu de souvenirs (moins de 300 environ, soit quelques mois d'usage), tous partent dans le prompt : aucun risque que la recherche par similarité rate un souvenir utile, pour moins de 0,001 $ par message avec le cache de prompt. La recherche vectorielle ne prend le relais qu'au-delà. L'extraction voit les souvenirs existants et décide d'ajouter, de modifier ou de supprimer : le seuil de similarité 0,90 ne savait pas gérer « j'ai déménagé à Paris » face à « habite à Lyon ».
3. **Journal des coûts et plafond quotidien dès la phase 2.** Chaque appel aux API est compté en base ; au-delà de 1 $ par jour, Marcus prévient Hugo et s'arrête de répondre jusqu'au lendemain. `/couts` affiche la dépense du jour et du mois.
4. **Phrase d'ouverture des appels corrigée** (phase 5) : elle doit dire « assistant IA » et annoncer l'enregistrement, comme l'exige le cadre légal.
5. **Petits ajouts** : date et heure de Paris données au modèle à chaque message ; une requête quotidienne à la base pour qu'elle ne passe pas en pause en cas de silence ; Python 3.12 ou plus récent (le PC a la 3.13) ; Vapi n'est plus exigé pour finir la phase 0.

## Objectif et périmètre

Construire un assistant personnel, réservé à Hugo, qui se souvient de lui et agit à sa place : d'abord par messages, puis par téléphone. Chaque version doit fonctionner seule avant de passer à la suivante.

| Version | Ce que l'assistant sait faire | Phases |
| --- | --- | --- |
| V1 | Discuter sur Telegram (texte et vocal), retenir ce qu'on lui dit, le ressortir au bon moment | 0 à 3 |
| V2 | Créer des rappels, lire et remplir l'agenda, envoyer un point le matin | 4 |
| V3 | Appeler un commerce à la place d'Hugo (réservation, commande à emporter) et faire un compte rendu ; envoyer le lien de réservation en ligne quand il existe | 5 |
| Socle | Tourner 24 h/24 sur un serveur, avec sauvegardes | 6 |

Hors périmètre : partager l'assistant avec d'autres personnes, payer à la place d'Hugo, appeler des particuliers.

## Architecture

Tout passe par le bot (Python) : il reçoit le message, transcrit les vocaux, cherche les souvenirs utiles, appelle le modèle, exécute les actions demandées (rappels, agenda, appels), puis range ce qu'il a appris.

- Hugo ↔ bot : Telegram (texte ou vocal).
- Bot ↔ mémoire : Supabase (Postgres + pgvector).
- Bot ↔ modèle : Claude Haiku 5.5 par API, avec des outils (tool use).
- Bot ↔ transcription : OpenAI gpt-4o-mini-transcribe.
- Bot → outils : rappels et Google Agenda ; appels sortants via Vapi + ligne SIP OVHcloud.

## Stack

| Brique | Choix | Pourquoi | Alternative |
| --- | --- | --- | --- |
| Langage | Python 3.12 ou plus récent | Écosystème IA le plus riche, librairies Telegram mûres | Node.js (TypeScript) |
| Interface | Bot Telegram (python-telegram-bot, polling) | Gratuit, vocaux natifs | Interface web perso |
| Modèle IA | claude-haiku-5-5 au quotidien, claude-sonnet-5-5 pour les tâches lourdes ; actions déclarées comme outils (tool use) | Haiku : 0,10 $ par million de tokens en entrée | GPT, Mistral |
| Mémoire | Supabase (Postgres + extension pgvector), connexion par psycopg + pgvector | Données et recherche par similarité au même endroit | SQLite + sqlite-vec |
| Embeddings | OpenAI text-embedding-3-small (1 536 dimensions) | 0,02 $ par million de tokens | Mistral, Voyage AI |
| Transcription | OpenAI gpt-4o-mini-transcribe | Accepte les .ogg de Telegram | Whisper, Deepgram |
| Rappels | JobQueue de python-telegram-bot + table reminders | Rappels rechargés après chaque redémarrage | APScheduler |
| Appels | Vapi (région EU) + ligne SIP OVHcloud (numéro français), déclarée en BYO SIP trunk | Appel émis par un opérateur français : numéro affiché normalement | Plivo ou didlogic via trunk SIP |
| Hébergement | Petit VPS Linux Ubuntu (ex. Hetzner CX23) | Le bot doit tourner 24 h/24 | Netcup, OVHcloud |

## Conception de la mémoire

La mémoire vit dans Supabase, jamais dans le modèle : à chaque message, le bot y cherche les souvenirs utiles et les ajoute au prompt.

| Type de souvenir | Exemple | Quand il est lu |
| --- | --- | --- |
| Profil | Prénom, ville, travail | À chaque message |
| Préférences de réponse | « Réponses courtes, en français » | À chaque message |
| Faits durables | « Préfère réserver vers 20 h » | À chaque message tant que la mémoire est petite, puis quand la recherche le juge pertinent |
| Événements | « Table réservée chez X le 21/10 » | Idem |
| Conversation en cours | Les 20 derniers messages | À chaque message |

Schéma :

```sql
create extension if not exists vector;

create table messages (
  id bigserial primary key,
  role text not null,          -- 'user' ou 'assistant'
  content text not null,
  created_at timestamptz default now()
);

create table memories (
  id bigserial primary key,
  type text not null,          -- profil, preference, fait, evenement
  content text not null,
  embedding vector(1536),      -- text-embedding-3-small
  source_message_id bigint references messages(id),
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index on memories using hnsw (embedding vector_cosine_ops);

create table reminders (
  id bigserial primary key,
  text text not null,
  due_at timestamptz not null, -- stocké en UTC, affiché en heure de Paris
  sent boolean default false,
  created_at timestamptz default now()
);
```

Écriture, après chaque échange (en arrière-plan, sans retarder la réponse) :

1. Un appel court à Haiku reçoit l'échange et la liste des souvenirs existants (avec leurs numéros).
2. Il renvoie, en JSON validé, de 0 à 3 opérations : ajouter, modifier le souvenir n°X, supprimer le souvenir n°X.
3. Chaque souvenir ajouté ou modifié reçoit son embedding (pour la recherche future).
4. Filtre strict, dans la consigne et dans le code : jamais de mot de passe, code, numéro de carte ou IBAN.

Lecture, avant chaque réponse :

1. Moins de 300 souvenirs : tous vont dans le prompt, rangés par type, dans un bloc mis en cache.
2. Au-delà : profil et préférences toujours, plus les 8 souvenirs les plus proches du message (embeddings).
3. Prompt final = consignes + souvenirs + date et heure de Paris + 20 derniers messages + message.

Commandes Telegram : `/souvenirs` (liste), `/oublie <texte>` (supprime), `/corrige <ancien> -> <nouveau>` (remplace).

## Sécurité

- Liste blanche : tout message venant d'un autre identifiant Telegram est ignoré.
- Clés API et identifiants dans `.env` uniquement, jamais dans le code ni sur GitHub ; `.env.example` vide sert de modèle.
- Filtre mémoire : mots de passe, codes, numéros de carte et IBAN ne sont jamais stockés.
- Plafond de dépense sur chaque compte API, et plafond d'appels par jour dans le code.
- Confirmation avant chaque appel : numéro, objectif et informations partagées affichés, puis « oui » explicite.
- Transcriptions d'appels supprimées automatiquement après 7 jours.
- Sauvegarde hebdomadaire de la base, stockée hors du serveur.

## Ordre des phases

Ordre révisé le 23/10/2026 (voir la révision en haut) ; les numéros de phase ne changent pas.

1. Phase 0 — Comptes et clés ✅
2. Phase 1 — Bot qui répond ✅
3. Phase 2 — Mémoire, journal des coûts ✅
4. Phase 3 — Vocaux → la V1 est complète ✅
5. Phase 6 — Mise en production sur le VPS (le bot tourne PC éteint ; Hugo s'en sert au quotidien)
6. Phase 4 — Rappels, Google Agenda, point du matin
7. Test téléphonie — OVHcloud branché sur Vapi, après 2 semaines d'usage de la V1
8. Phase 5 — Appels sortants

---

# Guide pas à pas

## Arborescence visée

```text
assistant-perso/
├── CLAUDE.md             # consignes permanentes pour Claude Code
├── .env                  # clés et mots de passe, jamais sur GitHub
├── .env.example          # même liste, valeurs vides
├── .gitignore
├── requirements.txt      # dépendances Python
├── docs/
│   └── PLAN.md           # ce fichier
├── prompts/
│   └── system.md         # personnalité et règles de l'assistant
├── sql/
│   └── schema.sql        # tables Supabase
├── app/
│   ├── main.py           # démarrage du bot
│   ├── config.py         # lecture du .env
│   ├── llm.py            # appels à Claude et outils
│   ├── memory.py         # écriture et recherche des souvenirs
│   ├── voice.py          # transcription des vocaux
│   ├── reminders.py      # rappels et point du matin
│   ├── calendar_google.py
│   └── calls.py          # appels via Vapi
├── scripts/              # vérification des clés, connexion Google
├── tests/
└── deploy/
    └── assistant.service # service systemd pour le VPS
```

Variables du fichier `.env` :

```text
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_ID=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
DATABASE_URL=
VAPI_API_KEY=
VAPI_PHONE_NUMBER_ID=
OVH_SIP_USERNAME=
OVH_SIP_PASSWORD=
OVH_SIP_PROXY=
OVH_PHONE_NUMBER=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
TIMEZONE=Europe/Paris
```

## Phase 0 — Créer les comptes et les clés

Chaque étape donne une valeur à coller dans `.env`, et nulle part ailleurs.

1. Telegram : @BotFather → `/newbot` → token dans `TELEGRAM_BOT_TOKEN`. @userinfobot → « Id » dans `TELEGRAM_ALLOWED_USER_ID`.
2. Claude (API) : platform.claude.com → crédit + clé API dans `ANTHROPIC_API_KEY`, limite de dépense basse. L'abonnement Pro/Max paie Claude Code ; l'API paie le bot.
3. OpenAI : platform.openai.com → clé API dans `OPENAI_API_KEY`, limite de dépense basse.
4. Supabase : projet en région Europe ; SQL Editor → `create extension if not exists vector;` ; bouton Connect → chaîne « Session pooler » (IPv4) dans `DATABASE_URL` avec le mot de passe.
5. OVHcloud : VoIP Découverte, option « ligne sans téléphone » ; identifiant SIP (0033…) dans `OVH_SIP_USERNAME`, proxy sortant dans `OVH_SIP_PROXY`, numéro dans `OVH_PHONE_NUMBER`, mot de passe SIP fort dans `OVH_SIP_PASSWORD` ; renvoi d'appel vers le portable d'Hugo.
6. Vapi : compte (région EU si proposée), crédit, clé privée dans `VAPI_API_KEY`.
7. Poste : Git, Python 3.12, Claude Code ; dépôt GitHub privé `assistant-perso`.

Consigne Claude Code de fin de phase :

```text
Crée scripts/check_keys.py qui lit le .env et teste chaque service :
Telegram (getMe), Claude (un message court avec claude-haiku-5-5), OpenAI (liste des modèles),
Supabase (SELECT 1 et présence de l'extension vector), Vapi (liste des assistants).
Affiche OK ou l'erreur exacte pour chacun, sans jamais afficher les clés. Lance-le.
```

Terminé quand : Telegram, Claude, OpenAI et Supabase affichent OK et `.env` est dans `.gitignore` (Vapi passe à OK au test téléphonie).

## Phase 1 — Le bot qui répond

```text
Lis CLAUDE.md. Phase 1 du plan : bot Telegram en Python 3.12 avec python-telegram-bot, en mode polling.
- app/config.py lit le .env et s'arrête avec un message clair si une variable manque.
- Le bot ignore tout message dont l'ID n'est pas TELEGRAM_ALLOWED_USER_ID.
- Chaque message texte part à l'API Claude (modèle claude-haiku-5-5) avec le prompt système
  de prompts/system.md ; la réponse revient sur Telegram.
- Garde en mémoire vive les 20 derniers messages pour le contexte.
- Prépare dans app/llm.py un mécanisme d'outils (tool use) vide, prêt à en recevoir.
- Si l'API échoue : le bot répond « Petit souci, réessaie dans un instant » et journalise l'erreur.
Écris des tests pour le filtre d'ID et la construction du prompt, lance-les, puis démarre le bot.
```

Prompt système de départ (`prompts/system.md`) :

```text
Tu es l'assistant personnel d'Hugo. Tu réponds en français, court et direct, et tu le tutoies.
Tu ne lances jamais d'action (rappel, événement, appel) qu'il n'a pas demandée.
Avant tout appel téléphonique, tu récapitules numéro, objectif et informations transmises,
puis tu attends son « oui ».
Quand tu ne sais pas, tu le dis.
```

Terminé quand : « Salut » reçoit une réponse en moins de 5 secondes ; un autre compte Telegram n'a pas de réponse ; dix messages gardent le fil ; commit « phase 1 : bot qui répond ».

## Test téléphonie — OVHcloud branché sur Vapi

1. Si la restriction par IP est activée sur la ligne OVHcloud, y ajouter les IP de Vapi : 63.182.83.170 (EU), 44.229.228.186 et 44.238.177.138 (US).
2. Consigne :

```text
Avec l'API Vapi (clé VAPI_API_KEY du .env), crée dans l'ordre :
1) une credential de type byo-sip-trunk nommée « OVH » : gateway = OVH_SIP_PROXY,
   outboundAuthenticationPlan avec authUsername = OVH_SIP_USERNAME et authPassword = OVH_SIP_PASSWORD ;
2) un phone-number de type byo-phone-number avec OVH_PHONE_NUMBER, lié à cette credential,
   numberE164CheckEnabled à false ;
3) un assistant de test en français (voix française, transcription en français) qui dit
   « Bonjour, ceci est un test de l'assistant d'Hugo » puis raccroche.
Écris l'ID du numéro dans VAPI_PHONE_NUMBER_ID. Montre-moi chaque réponse de l'API,
mais jamais les mots de passe.
```

3. Appel de test vers le portable d'Hugo.

Terminé quand : le portable sonne, le numéro OVHcloud s'affiche (pas « numéro masqué »), la phrase est audible, rappeler le numéro OVHcloud fait sonner le portable d'Hugo.
En cas d'échec : motif de fin d'appel dans Vapi, authentifications refusées côté OVHcloud, identifiant au format 0033… puis +33… ; en dernier recours Plivo ou didlogic.

## Phase 2 — La mémoire

```text
Lis CLAUDE.md et la section « Conception de la mémoire » de docs/PLAN.md (version révisée).
1) sql/schema.sql (tables messages, memories, reminders) est déjà appliqué ; ajoute-y une table
   usage (jour, modèle, tokens d'entrée, de sortie et de cache, coût estimé) et applique-la via DATABASE_URL.
2) app/db.py : connexion psycopg (pool async) à DATABASE_URL.
3) app/memory.py :
   - enregistre chaque message en base ; les 20 derniers sont relus depuis la base au démarrage ;
   - après chaque échange, en arrière-plan, un appel à claude-haiku-5-5 (sortie JSON validée)
     reçoit l'échange et les souvenirs existants, et renvoie 0 à 3 opérations
     ajouter / modifier n° / supprimer n° ; jamais de mot de passe, code, numéro de carte ou IBAN
     (consigne + filtre dans le code) ;
   - chaque souvenir ajouté ou modifié reçoit son embedding text-embedding-3-small ;
   - lecture : tous les souvenirs sous 300, sinon profil + préférences + 8 plus proches.
4) Le prompt devient : système + bloc souvenirs (mis en cache) + date et heure de Paris
   + 20 derniers messages + message.
5) app/usage.py : chaque appel Claude/OpenAI est compté ; au-delà de 1 $ par jour, Marcus
   prévient et ne répond plus jusqu'au lendemain.
6) Commandes Telegram : /souvenirs, /oublie <texte>, /corrige <ancien> -> <nouveau>, /couts.
7) Une requête quotidienne à la base évite la mise en pause de Supabase.
Tests : opérations d'extraction (ajout, modification, suppression, rien), filtre des données
sensibles, construction du prompt avec souvenirs, plafond de coût. Lance-les.
```

Terminé quand : un fait donné lundi ressort jeudi après un redémarrage ; un fait contredit est mis à jour, pas dupliqué ; `/oublie` le supprime ; `/couts` affiche la dépense ; commit « phase 2 : mémoire ».

## Phase 3 — Les messages vocaux

```text
Phase 3 : quand Hugo envoie un message vocal Telegram, télécharge le fichier .ogg,
transcris-le avec gpt-4o-mini-transcribe (langue fr), puis traite le texte exactement comme
un message écrit (mémoire comprise). Pendant la transcription, affiche « en train d'écrire ».
Refuse poliment les vocaux de plus de 5 minutes. Ajoute un test avec un court fichier audio.
```

Terminé quand : un vocal de 30 secondes est compris sans erreur, même avec la télé en fond ; commit « phase 3 : vocaux ».

## Phase 4 — Rappels, Google Agenda, point du matin

Préparation Google (à faire soi-même) : projet sur console.cloud.google.com, activer Google Calendar API ; écran de consentement OAuth Externe, s'ajouter comme utilisateur test puis publier « en production » (sinon l'accès expire au bout de 7 jours) ; identifiant OAuth « Application de bureau » → `GOOGLE_CLIENT_ID` et `GOOGLE_CLIENT_SECRET`.

```text
Phase 4.
1) Outils Claude (tool use) : create_reminder(texte, date_heure), list_reminders(),
   cancel_reminder(id), list_events(jour), add_event(titre, début, fin).
2) Rappels stockés dans la table reminders (en UTC) et planifiés avec la JobQueue de
   python-telegram-bot ; au démarrage, recharge tous les rappels non envoyés.
   Les messages de rappel sont en français, avec au moins 5 emojis bien visibles.
3) Google Agenda via l'API Calendar et GOOGLE_REFRESH_TOKEN ; écris scripts/google_auth.py
   pour obtenir ce token une seule fois.
4) Point du matin à 7 h 30, heure de Paris : agenda du jour + rappels en attente.
5) Toutes les dates sont interprétées en Europe/Paris, y compris « demain » et « dans 2 h ».
Tests : interprétation des dates, rechargement des rappels après redémarrage. Lance-les.
```

Terminé quand : « rappelle-moi demain 9 h d'appeler le garage » sonne à 9 h même après un redémarrage ; « qu'est-ce que j'ai demain ? » et « ajoute dentiste jeudi 14 h » marchent ; le point du matin arrive à 7 h 30 ; commit « phase 4 : outils du quotidien ».

## Phase 5 — Les appels sortants (mode Manual dans Claude Code)

```text
Phase 5. Ajoute l'outil call_business(numéro, objectif, détails).
1) Avant tout appel, le bot envoie à Hugo un récapitulatif : numéro, objectif, informations
   données (prénom, nombre de personnes, jour et heure, sur place ou à emporter), et attend « oui ».
   Sans « oui » sous 10 minutes, rien ne part.
2) Si le restaurant a une réservation en ligne connue, propose d'abord le lien.
3) Lance l'appel avec l'API Vapi de création d'appel, depuis VAPI_PHONE_NUMBER_ID, avec un
   assistant défini dans la requête : français, première phrase « Bonjour, je suis Marcus,
   l'assistant IA d'Hugo, j'appelle de sa part pour … Cet appel est enregistré ; dites-le-moi
   si vous préférez qu'il ne le soit pas. » (si refus : pas d'enregistrement, ou fin d'appel
   et Hugo prévenu), objectif et détails, fin d'appel polie, et extraction structurée
   du résultat (réservé oui/non, heure confirmée, nom donné, remarques).
4) Lis l'appel par son ID toutes les 15 secondes jusqu'à la fin, puis envoie le compte rendu à Hugo.
5) Limites : 5 appels par jour, un seul à la fois.
Tests : récapitulatif, refus sans « oui », lecture du résultat. Aucun vrai appel sans mon accord.
```

Tests réels, dans l'ordre : vers le portable d'Hugo qui joue le restaurant ; avec bruit de fond et interruptions ; vers un ami ; puis un vrai restaurant en semaine, l'après-midi.
Terminé quand : 10 appels de test réussis d'affilée et des comptes rendus fidèles ; commit « phase 5 : appels ».

## Phase 6 — Mise en production sur le VPS (mode Manual)

```text
Phase 6. Prépare le déploiement sur un VPS Ubuntu :
1) deploy/assistant.service (systemd) : démarrage au boot, redémarrage en cas de crash,
   utilisateur dédié sans droits d'administrateur.
2) deploy/install.sh : installe Python 3.12, crée l'environnement, installe requirements.txt.
3) Alerte Telegram à Hugo si le bot plante ou si une API échoue 3 fois de suite.
4) Sauvegarde hebdomadaire de la base Supabase dans un fichier daté, gardé 8 semaines.
5) README.md : déployer, redémarrer, lire les journaux.
Explique chaque commande que je devrai lancer sur le serveur.
```

Copier le `.env` sur le serveur à la main (jamais par GitHub), lancer le service, arrêter le bot sur le PC.
Terminé quand : le bot répond PC éteint, repart seul après un redémarrage du VPS, tient 7 jours ; tag « v1 ».

## Si ça bloque

| Symptôme | Cause probable | Quoi faire |
| --- | --- | --- |
| Le bot ne répond pas | Token faux, bot arrêté, ID non autorisé | Relancer `scripts/check_keys.py`, vérifier `TELEGRAM_ALLOWED_USER_ID` |
| « Conflict » dans les journaux Telegram | Deux copies du bot tournent | En arrêter une |
| Connexion à Supabase impossible | Chaîne directe en IPv6 | Prendre la chaîne « Session pooler » |
| Supabase en pause | 7 jours sans activité | Réactiver dans le tableau de bord |
| Google « invalid_grant » au bout d'une semaine | Application OAuth en mode test | La publier en production, relancer `scripts/google_auth.py` |
| L'appel Vapi échoue tout de suite | Identifiants SIP ou proxy faux, restriction IP OVHcloud | Lire les authentifications refusées côté OVHcloud, ajouter les IP de Vapi |
| « Numéro masqué » chez le destinataire | L'appel n'est pas parti par la ligne OVHcloud | Vérifier `VAPI_PHONE_NUMBER_ID` |
| La facture grimpe | Boucle d'appels API | Journaux de coûts, baisser les plafonds |
