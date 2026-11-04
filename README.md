# LazyQueue

Pilote ton client League of Legends depuis ton téléphone : lancer une partie, accepter, choisir et bannir ton champion, changer tes sorts et tes runes. Le téléphone et le PC doivent être sur le même Wi-Fi.

## Installation sur le PC

### Windows

1. Télécharge `LazyQueue.exe` et lance-le.
2. Windows affiche « Windows a protégé votre ordinateur » (l'app n'est pas signée) : clique sur **Informations complémentaires**, puis **Exécuter quand même**.
3. Le pare-feu demande d'autoriser l'accès réseau : coche **Réseaux privés** et valide. Sans ça, le téléphone ne peut pas joindre le PC.

### macOS (Apple Silicon : M1 et plus récents)

1. Télécharge `LazyQueue-macOS.zip`, ouvre-le, et glisse **LazyQueue** dans **Applications**.
2. Premier lancement : fais **clic droit → Ouvrir**, puis **Ouvrir**. Si macOS refuse, va dans **Réglages Système → Confidentialité et sécurité** et clique sur **Ouvrir quand même**.

Une fenêtre s'ouvre avec un QR code. Garde-la ouverte pendant que tu joues : tant qu'elle est ouverte, le PC ne se met pas en veille.

## Installation sur le téléphone

1. Scanne le QR code de la fenêtre avec l'appareil photo, et ouvre le lien.
2. Pour l'avoir comme une app :
   - **iPhone** : dans Safari, bouton **Partager → Sur l'écran d'accueil**.
   - **Android** : dans Chrome, menu **⋮ → Ajouter à l'écran d'accueil**.
3. À la première ouverture depuis l'icône, entre le **code à 6 chiffres** affiché dans la fenêtre du PC.

## Bon à savoir

- Pour lancer le client LoL depuis le téléphone, coche **Rester connecté** dans le client Riot.
- Tes réglages (appairage, favoris) sont dans `%APPDATA%\LazyQueue` (Windows) ou `~/Library/Application Support/LazyQueue` (macOS). Le journal de l'app est dans `lazyqueue.log`, au même endroit.

## Développement

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python app.py      # fenêtre avec QR code
    .venv/bin/python agent.py    # version terminal (QR en texte)

Options de `agent.py` : `--reset` régénère le token (désappaire les téléphones), `--ntfy` active les notifications push via l'app ntfy, `--no-ntfy` les désactive.

### Construire l'application

    .venv/bin/pip install -r requirements-build.txt
    .venv/bin/python build.py

Résultat dans `dist/` : `LazyQueue-macOS.zip` sur macOS, `LazyQueue.exe` sur Windows (sous Windows, remplace `.venv/bin/` par `.venv\Scripts\`). PyInstaller construit pour l'OS sur lequel il tourne : pour Windows, construis sur un PC Windows, ou lance le workflow GitHub Actions `Build` (onglet Actions du dépôt GitHub), qui produit les deux versions.
