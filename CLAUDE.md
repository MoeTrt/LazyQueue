# LazyQueue

Companion perso pour piloter le client League of Legends depuis le téléphone, uniquement en Wi-Fi local. Projet séparé de l'outil type op.gg.

## Architecture

    Téléphone (page web)  <--WebSocket + token-->  agent.py (PC)  <--HTTPS local-->  LCU API

- app.py : fenêtre Tk (QR, code d'appairage, état client/téléphones, copier le lien, quitter). Lance agent.main(gui=True) dans un thread, empêche la veille (caffeinate -w sur macOS, SetThreadExecutionState sur Windows). Point d'entrée de l'app distribuée.
- agent.py : serveur aiohttp sur 0.0.0.0:8765. Sert index.html, /ws (auth par token), /asset/* (proxy + cache mémoire des icônes LCU, limité à /lol-game-data/assets/), /static/*, /pair. Lançable seul en terminal (QR en texte).
- Fichiers : ressources lues dans RES_DIR (sys._MEIPASS une fois emballé), données dans le dossier utilisateur (~/Library/Application Support/LazyQueue, %APPDATA%\LazyQueue) : config.json (token, favoris, topic ntfy ; migré depuis l'ancien config.json du projet), champselect.log, lazyqueue.log (version emballée).
- Connexion LCU : LOL_LOCKFILE seul s'il est défini (aucun repli, sécurité des tests), sinon lockfiles déduits de RiotClientInstalls.json + chemins par défaut (ignorés si le pid n'existe plus), sinon arguments de LeagueClientUx via psutil. Reconnexion toutes les 2 s.
- Distribution : build.py (PyInstaller, --windowed ; .app + zip sur macOS, .exe --onefile sur Windows), .github/workflows/build.yml pour construire les deux OS. App non signée (adhoc sur macOS) : Gatekeeper/SmartScreen à passer au premier lancement.
- Événements LCU souscrits (opcode 5) : gameflow-phase, champ-select session, lol-perks pages.

## Fonctions

- Lancer le client : RiotClientServices --launch-product=league_of_legends --launch-patchline=live (chemin via /Users/Shared/Riot Games/RiotClientInstalls.json ou RIOT_CLIENT).
- Lobby (files disponibles lues depuis le client, hors TFT et personnalisées), lancer/annuler la file.
- Appairage : token dans l'URL du QR, puis cookie (1 an) et localStorage. Ou code à 6 chiffres (régénéré à chaque démarrage, POST /pair, 5 essais/min) pour l'app ajoutée à l'écran d'accueil iOS, qui ne partage pas le stockage de Safari. Favoris de champions dans config.json.
- Page : app web installable (static/ : manifest, icônes, logo). Un écran par phase (connexion, client fermé, accueil, partie trouvée, champ select avec onglets Champion / Sorts & runes, en jeu, runes). Action "play" = crée le lobby si besoin puis lance la recherche.
- Design sobre : fond sombre uni, marbre vert à peine visible dans les panneaux (static/marble-green.jpg, généré par tools/make_marble.py, raccordable, 512px CSS), couleur principale #253D2C, or en dégradé (#F6E7B8 → #D4B26A → #A9843B) pour le bouton principal, les sélections et les liserés. Police système, coins arrondis, flou d'arrière-plan sur l'en-tête et les barres. Tokens CSS dans :root de index.html.
- Accepter/refuser, auto-accept.
- Champ select : hover (PATCH actions/{id}), lock (PATCH actions/{id} avec completed: true), grille filtrée par pickable/bannable, sorts (PATCH my-selection, swap si doublon, filtrés par gameMode), page de runes (PUT /lol-perks/v1/currentpage).
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

## Limites connues

- Pas de création de pages de runes (choix parmi les pages existantes uniquement).
- ARAM : pas de bench swap.
- Lancement à distance nécessite "Rester connecté" dans le client Riot.
- Windows : code prévu (lockfile, psutil, lancement, données, DPI) mais jamais testé sur un vrai PC.
- macOS : build arm64 uniquement (pas de Mac Intel).

## Tests

- Toujours contre un mock LCU avec LOL_LOCKFILE pointant vers son lockfile, jamais contre le vrai client : des tests ont déjà lancé une recherche de partie réelle.

## Conventions

- Code concis, pas d'emojis, pas de commentaires décoratifs.
- Réponses en français, tutoiement.
