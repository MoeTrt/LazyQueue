# LazyQueue

Companion perso pour piloter le client League of Legends depuis le téléphone, uniquement en Wi-Fi local. Projet séparé de l'outil type op.gg.

## Architecture

    Téléphone (page web)  <--WebSocket + token-->  agent.py (Mac)  <--HTTPS local-->  LCU API

- agent.py : serveur aiohttp sur 0.0.0.0:8765. Sert index.html, /ws (auth par token en query), /asset/* (proxy des icônes LCU, limité à /lol-game-data/assets/, refuse ".." et non-images).
- Au démarrage : affiche un QR code de l'URL `http://<ip>:8765/?token=...`. Token + topic ntfy stockés dans config.json (--reset, --ntfy, --no-ntfy).
- Connexion LCU : lockfile `/Applications/League of Legends.app/Contents/LoL/lockfile` (ou LOL_LOCKFILE), sinon parsing de `ps` (--app-port, --remoting-auth-token de LeagueClientUx). Reconnexion toutes les 2 s.
- Événements LCU souscrits (opcode 5) : gameflow-phase, champ-select session, lol-perks pages.

## Fonctions

- Lancer le client : RiotClientServices --launch-product=league_of_legends --launch-patchline=live (chemin via /Users/Shared/Riot Games/RiotClientInstalls.json ou RIOT_CLIENT).
- Lobby (files disponibles lues depuis le client, hors TFT et personnalisées), lancer/annuler la file.
- Appairage : token dans l'URL du QR, puis cookie (1 an) et localStorage. Ou code à 6 chiffres (régénéré à chaque démarrage, POST /pair, 5 essais/min) pour l'app ajoutée à l'écran d'accueil iOS, qui ne partage pas le stockage de Safari. Favoris de champions dans config.json.
- Page : app web installable (static/ : manifest, icônes, logo). Un écran par phase (connexion, client fermé, accueil, partie trouvée, champ select avec onglets Champion / Sorts & runes, en jeu, runes). Action "play" = crée le lobby si besoin puis lance la recherche.
- Design (inspiré du client LoL, sans ses assets) : marbre vert (static/marble-*.jpg, générés par tools/make_marble.py, raccordables, 512px CSS), couleur principale #253D2C, détails dorés (#F4E7BE → #C8AA6E → #8A6A2F), titres en Cinzel (OFL, embarquée dans static/), boutons à coins coupés avec cadre doré (clip-path). Tokens CSS dans :root de index.html.
- Accepter/refuser, auto-accept.
- Champ select : hover (PATCH actions/{id}), lock (POST .../complete), grille filtrée par pickable/bannable, sorts (PATCH my-selection, swap si doublon, filtrés par gameMode), page de runes (PUT /lol-perks/v1/currentpage).
- Éditeur de pages de runes (hors champ select aussi) : arbres, runes, fragments, nom. Données depuis /lol-perks/v1/styles et /perks, validation côté agent, enregistrement par PUT /lol-perks/v1/pages/{id} (page complète relue puis modifiée).
- Runes recommandées (champ select) : GET /lol-perks/v1/recommended-pages/champion/{id}/position/{POSTE}/map/{mapId}, rechargées quand le champion (intention, survol, lock) change. Un tap charge la recommandation dans l'éditeur sur la page sélectionnée, l'utilisateur enregistre.
- Notifications : partie trouvée, ton tour de pick/ban, partie qui démarre. Bannière + vibration dans la page, push via ntfy.sh (JSON publish) si topic configuré.

## État

- Validé sur un vrai client (macOS, novembre 2026) : lobby, file, accept, pré-sélection, ban, lock, sorts, runes.
- champselect.log (ignoré par git) : journal du dernier champ select (actions du téléphone, requêtes LCU, états de session). Remis à zéro à chaque champ select.

## Particularités LCU constatées

- POST /lol-champ-select/v1/session/actions/{id}/complete répond 204 sans rien verrouiller. Verrouiller via PATCH actions/{id} avec {"championId": X, "completed": true}.
- Pendant PLANNING, l'action de ban est déjà isInProgress. La pré-sélection = PATCH sur l'action de pick (non en cours), pas sur l'action en cours.
- bannable-champion-ids renvoie [-1] pendant PLANNING : recharger pickable/bannable à chaque changement d'action.
- Les ids d'action peuvent valoir 0 (ne pas tester leur vérité).
- -1 comme championId = aucun champion.
- La file 490 (Partie rapide) est désactivée, remplacée par 480 (Partie accélérée). Les files sont lues depuis /lol-game-queues/v1/queues.
- champion-summary.json contient des doublons (variantes Jade_*, ids >= 60000).
- Un ws_connect sur une session aiohttp qui a déjà servi à une requête HTTP peut renvoyer 404 : ouvrir le WebSocket en premier.
- Plateforme cible : macOS (Windows géré en secondaire).

## Limites connues

- Pas de création de pages de runes (choix parmi les pages existantes uniquement).
- ARAM : pas de bench swap.
- Lancement à distance nécessite "Rester connecté" dans le client Riot.
- Le Mac doit rester éveillé (caffeinate -i).

## Conventions

- Code concis, pas d'emojis, pas de commentaires décoratifs.
- Réponses en français, tutoiement.
