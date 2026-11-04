# LazyQueue

Agent Python sur le Mac + page web servie en local pour piloter le client League of Legends depuis le téléphone (même Wi-Fi).

## Installation (macOS)

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    caffeinate -i python agent.py          # affiche le QR code, empêche la mise en veille
    caffeinate -i python agent.py --ntfy   # active les notifications push (génère un topic ntfy)
    python agent.py --reset                # régénère le token (invalide les téléphones appairés)

Scanne le QR code avec le téléphone. Le token est mémorisé par le navigateur et dans config.json.

Premier lancement : macOS demande d'accepter les connexions entrantes pour Python. Accepte, sinon le téléphone ne joint pas l'agent.
Si Python vient de l'installeur python.org et que ntfy renvoie une erreur SSL, lance une fois `/Applications/Python 3.x/Install Certificates.command`.

## Détection du client

1. Lockfile : `/Applications/League of Legends.app/Contents/LoL/lockfile` (ou variable LOL_LOCKFILE).
2. Sinon, arguments du process LeagueClientUx (`--app-port`, `--remoting-auth-token`) via `ps`.

Lancement du client : chemin lu dans `/Users/Shared/Riot Games/RiotClientInstalls.json`, sinon variable RIOT_CLIENT.
Coche « Rester connecté » dans le client Riot, sinon le lancement à distance s'arrête sur l'écran de login.

## Notifications

- Page ouverte : bannière (+ vibration sur Android, pas sur iOS).
- Page fermée / téléphone verrouillé : app ntfy (iOS/Android), abonnement au topic affiché au démarrage. Le topic est le seul secret.

## Lancement auto à la connexion (launchd)

Après avoir appairé le téléphone une fois, crée `~/Library/LaunchAgents/com.moe.lazyqueue.plist` :

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0"><dict>
      <key>Label</key><string>com.moe.lazyqueue</string>
      <key>ProgramArguments</key><array>
        <string>/usr/bin/caffeinate</string><string>-i</string>
        <string>/CHEMIN/LazyQueue/.venv/bin/python</string>
        <string>/CHEMIN/LazyQueue/agent.py</string>
      </array>
      <key>RunAtLoad</key><true/>
      <key>KeepAlive</key><true/>
      <key>StandardOutPath</key><string>/tmp/lazyqueue.log</string>
      <key>StandardErrorPath</key><string>/tmp/lazyqueue.log</string>
    </dict></plist>

Puis `launchctl load ~/Library/LaunchAgents/com.moe.lazyqueue.plist`. Le QR et l'URL sont dans /tmp/lazyqueue.log.
