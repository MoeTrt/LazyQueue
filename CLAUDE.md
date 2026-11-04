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
- Lobby (queueId 420/440/400/490/450), lancer/annuler la file.
- Accepter/refuser, auto-accept.
- Champ select : hover (PATCH actions/{id}), lock (POST .../complete), grille filtrée par pickable/bannable, sorts (PATCH my-selection, swap si doublon, filtrés par gameMode), page de runes (PUT /lol-perks/v1/currentpage).
- Notifications : partie trouvée, ton tour de pick/ban, partie qui démarre. Bannière + vibration dans la page, push via ntfy.sh (JSON publish) si topic configuré.

## État

- Jamais testé contre un vrai client. Seuls la compilation Python, la syntaxe JS et le parsing des args `ps` ont été vérifiés.
- Plateforme cible : macOS (Windows géré en secondaire).

## Limites connues

- Pas de création de pages de runes (choix parmi les pages existantes uniquement).
- ARAM : pas de bench swap.
- Lancement à distance nécessite "Rester connecté" dans le client Riot.
- Le Mac doit rester éveillé (caffeinate -i).

## Conventions

- Code concis, pas d'emojis, pas de commentaires décoratifs.
- Réponses en français, tutoiement.
