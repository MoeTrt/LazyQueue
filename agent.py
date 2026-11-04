import asyncio
import base64
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import certifi
import psutil
import qrcode
from aiohttp import web


def user_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = base / "LazyQueue"
    path.mkdir(parents=True, exist_ok=True)
    return path


HTTP_PORT = 8765
BASE_DIR = Path(__file__).resolve().parent
RES_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
DATA_DIR = user_data_dir()
CONFIG_FILE = DATA_DIR / "config.json"
DEBUG_FILE = DATA_DIR / "champselect.log"
LOG_FILE = DATA_DIR / "lazyqueue.log"
DEFAULT_LOCKFILES = [
    "C:/Riot Games/League of Legends/lockfile",
    "/Applications/League of Legends.app/Contents/LoL/lockfile",
]
RIOT_INSTALLS = (
    Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Riot Games/RiotClientInstalls.json"
    if os.name == "nt"
    else Path("/Users/Shared/Riot Games/RiotClientInstalls.json")
)
RIOT_CLIENT_FALLBACKS = [
    os.environ.get("RIOT_CLIENT"),
    "/Users/Shared/Riot Games/Riot Client.app/Contents/MacOS/RiotClientServices",
    "C:/Riot Games/Riot Client/RiotClientServices.exe",
]
UX_PORT_RE = re.compile(r"--app-port=(\d+)")
UX_TOKEN_RE = re.compile(r"--remoting-auth-token=([\w-]+)")
NTFY_URL = "https://ntfy.sh/"

PHASE_URI = "/lol-gameflow/v1/gameflow-phase"
SESSION_URI = "/lol-champ-select/v1/session"
PERKS_URI = "/lol-perks/v1/pages"
SIMPLE_ACTIONS = {
    "accept": ("POST", "/lol-matchmaking/v1/ready-check/accept"),
    "decline": ("POST", "/lol-matchmaking/v1/ready-check/decline"),
    "search": ("POST", "/lol-lobby/v2/lobby/matchmaking/search"),
    "cancel": ("DELETE", "/lol-lobby/v2/lobby/matchmaking/search"),
}

state = {
    "phase": "Offline",
    "champselect": None,
    "champions": [],
    "spells": [],
    "pickable": [],
    "bannable": [],
    "rune_pages": [],
    "rune_styles": [],
    "perks": {},
    "recommended": {"championId": 0, "pages": []},
    "reco_key": None,
    "cs_map": 11,
    "auto_accept": False,
    "queues": [],
    "favorites": [],
    "lobby": {"queueId": 0, "inQueue": None, "estimated": None},
}
STATIC_DIR = RES_DIR / "static"
STATIC_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".webmanifest": "application/manifest+json",
}
pair_code = f"{secrets.randbelow(10**6):06d}"
pair_failures: list[float] = []
PREFERRED_QUEUES = [420, 440, 400, 480, 450]
all_spells: list[dict] = []
clients: set[web.WebSocketResponse] = set()
asset_cache: dict[str, tuple[bytes, str]] = {}
background: set[asyncio.Task] = set()
lcu = None
config: dict = {}
http: aiohttp.ClientSession | None = None
page_url = ""
notified_action = None


class LCU:
    def __init__(self, port: int, password: str):
        self.base = f"https://127.0.0.1:{port}"
        self.session = aiohttp.ClientSession(
            headers={"Authorization": "Basic " + base64.b64encode(f"riot:{password}".encode()).decode()},
            connector=aiohttp.TCPConnector(ssl=False),
        )

    async def request(self, method: str, path: str, body=None):
        kwargs = {} if body is None else {"json": body}
        async with self.session.request(method, self.base + path, **kwargs) as r:
            text = await r.text()
            try:
                data = json.loads(text) if text else None
            except ValueError:
                data = None
        if method != "GET" and "/lol-champ-select/" in path:
            debug_log("lcu", req=f"{method} {path}", body=body, status=r.status, resp=data if r.status >= 400 else None)
        return r.status, data

    async def get_list(self, path: str) -> list:
        status, data = await self.request("GET", path)
        return data if status == 200 and isinstance(data, list) else []

    async def close(self):
        await self.session.close()


def debug_log(kind: str, **fields):
    try:
        with DEBUG_FILE.open("a") as f:
            f.write(json.dumps({"t": time.strftime("%H:%M:%S"), "kind": kind, **fields}) + "\n")
    except OSError:
        pass


def load_config(args: list[str]) -> dict:
    legacy = BASE_DIR / "config.json"
    if not CONFIG_FILE.exists() and legacy.exists() and legacy != CONFIG_FILE:
        CONFIG_FILE.write_text(legacy.read_text())
    cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    if "--reset" in args or "token" not in cfg:
        cfg["token"] = secrets.token_urlsafe(24)
    if "--ntfy" in args and not cfg.get("ntfy_topic"):
        cfg["ntfy_topic"] = "lazyqueue-" + secrets.token_hex(8)
    if "--no-ntfy" in args:
        cfg.pop("ntfy_topic", None)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    return cfg


def local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def read_process_args():
    for proc in psutil.process_iter(["name", "cmdline"]):
        if "LeagueClientUx" not in (proc.info.get("name") or ""):
            continue
        args = " ".join(proc.info.get("cmdline") or [])
        port, token = UX_PORT_RE.search(args), UX_TOKEN_RE.search(args)
        if port and token:
            return int(port.group(1)), token.group(1)
    return None


def lockfile_candidates() -> list[Path]:
    paths = []
    try:
        for client in json.loads(RIOT_INSTALLS.read_text()).get("associated_client", {}):
            client = Path(client)
            paths += [client / "lockfile", client.parent / "lockfile"]
    except (OSError, ValueError, AttributeError):
        pass
    return [Path(p) for p in paths + DEFAULT_LOCKFILES if p]


def read_lockfile():
    forced = os.environ.get("LOL_LOCKFILE")
    if forced:
        try:
            _, _, port, password, _ = Path(forced).read_text().split(":")
            return int(port), password
        except (OSError, ValueError):
            return None
    for path in lockfile_candidates():
        if not path.is_file():
            continue
        try:
            _, pid, port, password, _ = path.read_text().split(":")
        except (OSError, ValueError):
            continue
        if psutil.pid_exists(int(pid)):
            return int(port), password
    return read_process_args()


def riot_client_path() -> Path | None:
    try:
        installs = json.loads(RIOT_INSTALLS.read_text())
        for key in ("rc_default", "rc_live"):
            if installs.get(key) and Path(installs[key]).is_file():
                return Path(installs[key])
    except (OSError, ValueError):
        pass
    return next((Path(p) for p in RIOT_CLIENT_FALLBACKS if p and Path(p).is_file()), None)


def launch_client():
    if lcu is not None:
        return "Client déjà lancé"
    exe = riot_client_path()
    if exe is None:
        return "RiotClientServices introuvable (définis RIOT_CLIENT)"
    subprocess.Popen(
        [str(exe), "--launch-product=league_of_legends", "--launch-patchline=live"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    return None


def fire(coro):
    task = asyncio.create_task(coro)
    background.add(task)
    task.add_done_callback(background.discard)


async def broadcast(msg: dict):
    data = json.dumps(msg)
    for ws in list(clients):
        try:
            await ws.send_str(data)
        except (ConnectionResetError, RuntimeError):
            clients.discard(ws)


async def push_ntfy(title: str, message: str, tag: str):
    try:
        async with http.post(
            NTFY_URL,
            json={
                "topic": config["ntfy_topic"],
                "title": title,
                "message": message,
                "priority": 4,
                "tags": [tag],
                "click": page_url,
            },
        ) as r:
            if r.status >= 400:
                print(f"ntfy: HTTP {r.status}")
    except aiohttp.ClientError as e:
        print(f"ntfy: {e!r}")


async def notify(title: str, message: str, tag: str):
    await broadcast({"type": "notify", "title": title, "message": message})
    if config.get("ntfy_topic"):
        fire(push_ntfy(title, message, tag))


def simplify(session: dict) -> dict:
    me = session["localPlayerCellId"]
    mine_actions = [a for group in session["actions"] for a in group if a.get("actorCellId") == me]
    timer = session.get("timer") or {}
    planning = timer.get("phase") == "PLANNING"
    current = None if planning else next((a for a in mine_actions if a.get("isInProgress") and not a.get("completed")), None)
    next_pick = next((a for a in mine_actions if a.get("type") == "pick" and not a.get("completed")), None)
    mine = next((p for p in session["myTeam"] if p["cellId"] == me), {})
    return {
        "action": {"id": current["id"], "type": current["type"], "championId": current.get("championId", 0)}
        if current
        else None,
        "pickActionId": next_pick["id"] if next_pick else None,
        "championId": mine.get("championId") or mine.get("championPickIntent") or 0,
        "spells": [mine.get("spell1Id", 0), mine.get("spell2Id", 0)],
        "timer": timer.get("adjustedTimeLeftInPhase", 0),
        "total": timer.get("totalTimeInPhase", 0),
        "phase": timer.get("phase", ""),
        "position": (mine.get("assignedPosition") or "").upper(),
    }


RECO_ATTRS = {
    "kCooldown": "Délais de récupération",
    "kBurstDamage": "Dégâts explosifs",
    "kDamage": "Dégâts",
    "kGold": "Or",
    "kMana": "Mana",
    "kHealing": "Soins",
    "kSustain": "Survie",
    "kTankiness": "Résistance",
    "kDurability": "Résistance",
    "kMobility": "Mobilité",
    "kUtility": "Utilité",
    "kAttackSpeed": "Vitesse d'attaque",
    "kPoke": "Harcèlement",
    "kCrowdControl": "Contrôle",
}


def reco_label(attr: str | None) -> str:
    if not attr:
        return ""
    return RECO_ATTRS.get(attr, re.sub(r"(?<!^)(?=[A-Z])", " ", attr.removeprefix("k")))


def reco_champion(cs: dict) -> int:
    action = cs["action"]
    if action and action["type"] == "pick" and action["championId"] > 0:
        return action["championId"]
    return cs["championId"] if cs["championId"] > 0 else 0


async def refresh_recommended(cs: dict | None):
    champ = reco_champion(cs) if cs else 0
    position = (cs["position"] if cs else "") or "NONE"
    key = (champ, position, state["cs_map"])
    if key == state["reco_key"]:
        return
    state["reco_key"] = key
    pages = []
    if champ:
        data = await lcu.get_list(f"/lol-perks/v1/recommended-pages/champion/{champ}/position/{position}/map/{state['cs_map']}")
        pages = [
            {
                "primary": p["primaryPerkStyleId"],
                "sub": p["secondaryPerkStyleId"],
                "perks": [x["id"] for x in p.get("perks", [])],
                "keystone": (p.get("keystone") or {}).get("name", ""),
                "label": " · ".join(
                    filter(None, (reco_label(p.get("primaryRecommendationAttribute")), reco_label(p.get("secondaryRecommendationAttribute"))))
                ),
                "position": p.get("position", ""),
            }
            for p in data
            if len(p.get("perks", [])) == 9
        ]
    state["recommended"] = {"championId": champ, "pages": pages}
    await broadcast({"type": "recommended", **state["recommended"]})


def queue_view(queues: list) -> list:
    out = []
    for q in queues:
        if (
            q.get("queueAvailability") != "Available"
            or not q.get("isVisible")
            or q.get("category") not in ("PvP", "VersusAi")
            or q.get("gameMode") == "TFT"
        ):
            continue
        label = q.get("description") or q.get("name") or str(q["id"])
        if q.get("category") == "VersusAi" and "IA" not in label:
            label = f"Coop vs IA : {label}"
        out.append({"id": q["id"], "name": label})
    rank = {qid: i for i, qid in enumerate(PREFERRED_QUEUES)}
    return sorted(out, key=lambda q: (rank.get(q["id"], len(rank)), q["id"]))


async def ensure_static():
    global all_spells
    if not state["queues"]:
        state["queues"] = queue_view(await lcu.get_list("/lol-game-queues/v1/queues"))
        if state["queues"]:
            await broadcast({"type": "queues", "queues": state["queues"]})
    if not state["champions"]:
        data = await lcu.get_list("/lol-game-data/assets/v1/champion-summary.json")
        state["champions"] = sorted(
            ({"id": c["id"], "name": c["name"]} for c in data if c["id"] > 0),
            key=lambda c: c["name"],
        )
        if state["champions"]:
            await broadcast({"type": "champions", "champions": state["champions"]})
    if not all_spells:
        data = await lcu.get_list("/lol-game-data/assets/v1/summoner-spells.json")
        all_spells = [
            {
                "id": s["id"],
                "name": s["name"],
                "icon": s["iconPath"].removeprefix("/lol-game-data/assets/"),
                "modes": s.get("gameModes", []),
            }
            for s in data
            if s["id"] > 0 and s.get("iconPath")
        ]
    if not state["rune_styles"]:
        styles = await lcu.get_list("/lol-perks/v1/styles")
        perks = await lcu.get_list("/lol-perks/v1/perks")
        if styles and perks:
            state["rune_styles"] = [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "icon": asset_path(s.get("iconPath", "")),
                    "subStyles": s.get("allowedSubStyles", []),
                    "slots": [{"type": sl["type"], "perks": sl["perks"]} for sl in s["slots"]],
                }
                for s in sorted(styles, key=lambda s: s["id"])
            ]
            state["perks"] = {
                p["id"]: {"name": p["name"], "icon": asset_path(p.get("iconPath", "")), "desc": strip_tags(p.get("shortDesc", ""))}
                for p in perks
            }
            await broadcast({"type": "rune_data", "styles": state["rune_styles"], "perks": state["perks"]})
    if not state["rune_pages"]:
        await refresh_rune_pages()


TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text).strip()


def asset_path(path: str) -> str:
    return path.removeprefix("/lol-game-data/assets/")


def rune_view(pages: list) -> list:
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "current": p.get("current", False),
            "editable": p.get("isEditable", False),
            "primary": p.get("primaryStyleId", 0),
            "sub": p.get("subStyleId", 0),
            "perks": p.get("selectedPerkIds", []),
        }
        for p in pages
    ]


def valid_rune_page(primary: int, sub: int, perk_ids: list[int]) -> str | None:
    styles = {s["id"]: s for s in state["rune_styles"]}
    if primary not in styles or sub not in styles[primary]["subStyles"]:
        return "Arbres de runes invalides"
    if len(perk_ids) != 9:
        return "Il faut 9 runes"
    rows = [sl["perks"] for sl in styles[primary]["slots"] if sl["type"] != "kStatMod"]
    for row, perk in zip(rows, perk_ids[:4]):
        if perk not in row:
            return "Runes principales invalides"
    sub_rows = [sl["perks"] for sl in styles[sub]["slots"] if sl["type"] == "kMixedRegularSplashable"]
    used = [next((i for i, row in enumerate(sub_rows) if perk in row), None) for perk in perk_ids[4:6]]
    if None in used or used[0] == used[1]:
        return "Runes secondaires invalides"
    shard_rows = [sl["perks"] for sl in styles[primary]["slots"] if sl["type"] == "kStatMod"]
    for row, perk in zip(shard_rows, perk_ids[6:]):
        if perk not in row:
            return "Fragments invalides"
    return None


async def save_rune_page(msg: dict):
    page_id = int(msg["pageId"])
    status, page = await lcu.request("GET", f"{PERKS_URI}/{page_id}")
    if status != 200 or not isinstance(page, dict):
        return "Page introuvable"
    if not page.get("isEditable"):
        return "Cette page n'est pas modifiable"
    primary, sub = int(msg["primary"]), int(msg["sub"])
    perk_ids = [int(p) for p in msg["perks"]]
    error = valid_rune_page(primary, sub, perk_ids)
    if error:
        return error
    name = str(msg.get("name") or page["name"]).strip()[:25] or page["name"]
    page.update(name=name, primaryStyleId=primary, subStyleId=sub, selectedPerkIds=perk_ids)
    status, data = await lcu.request("PUT", f"{PERKS_URI}/{page_id}", page)
    if status >= 400:
        return (data or {}).get("message", f"HTTP {status}") if isinstance(data, dict) else f"HTTP {status}"
    await refresh_rune_pages()
    await broadcast({"type": "rune_saved", "pageId": page_id})
    return None


async def refresh_rune_pages():
    state["rune_pages"] = rune_view(await lcu.get_list(PERKS_URI))
    await broadcast({"type": "rune_pages", "pages": state["rune_pages"]})


async def load_champselect():
    DEBUG_FILE.write_text("")
    await ensure_static()
    _, flow = await lcu.request("GET", "/lol-gameflow/v1/session")
    queue = ((flow or {}).get("gameData") or {}).get("queue", {})
    mode = queue.get("gameMode", "CLASSIC")
    state["cs_map"] = queue.get("mapId") or 11
    state["spells"] = [s for s in all_spells if mode in s["modes"]]
    await refresh_champ_lists()
    await refresh_rune_pages()


async def refresh_champ_lists():
    state["pickable"] = [c for c in await lcu.get_list("/lol-champ-select/v1/pickable-champion-ids") if c > 0]
    state["bannable"] = [c for c in await lcu.get_list("/lol-champ-select/v1/bannable-champion-ids") if c > 0]
    await broadcast(
        {
            "type": "cs_data",
            "pickable": state["pickable"],
            "bannable": state["bannable"],
            "spells": state["spells"],
        }
    )


async def on_phase(phase):
    if not isinstance(phase, str) or phase == state["phase"]:
        return
    state["phase"] = phase
    await broadcast({"type": "phase", "phase": phase})
    await ensure_static()
    if phase in ("None", "Lobby", "Matchmaking"):
        await refresh_lobby()
    if phase == "ReadyCheck":
        if state["auto_accept"]:
            await lcu.request(*SIMPLE_ACTIONS["accept"])
            await notify("Partie trouvée", "Acceptée automatiquement", "white_check_mark")
        else:
            await notify("Partie trouvée", "Accepte depuis ton téléphone", "rotating_light")
    elif phase == "ChampSelect":
        await load_champselect()
    elif phase == "InProgress":
        await notify("La partie démarre", "Retourne sur ton PC", "video_game")


async def refresh_lobby():
    info = {"queueId": 0, "inQueue": None, "estimated": None}
    status, lobby = await lcu.request("GET", "/lol-lobby/v2/lobby")
    if status == 200 and isinstance(lobby, dict):
        info["queueId"] = (lobby.get("gameConfig") or {}).get("queueId", 0)
    if state["phase"] == "Matchmaking":
        status, search = await lcu.request("GET", "/lol-matchmaking/v1/search")
        if status == 200 and isinstance(search, dict):
            info["inQueue"] = search.get("timeInQueue")
            info["estimated"] = search.get("estimatedQueueTime")
    state["lobby"] = info
    await broadcast({"type": "lobby", **info})


async def play(queue_id: int):
    if state["phase"] == "Matchmaking":
        return None
    status, lobby = await lcu.request("GET", "/lol-lobby/v2/lobby")
    current = (lobby.get("gameConfig") or {}).get("queueId") if status == 200 and isinstance(lobby, dict) else None
    if current != queue_id:
        status, data = await lcu.request("POST", "/lol-lobby/v2/lobby", {"queueId": queue_id})
        if status >= 400:
            return error_message(status, data)
    for attempt in range(3):
        status, data = await lcu.request(*SIMPLE_ACTIONS["search"])
        if status < 400:
            return None
        await asyncio.sleep(0.3)
    return error_message(status, data)


def error_message(status: int, data) -> str:
    return (data or {}).get("message", f"HTTP {status}") if isinstance(data, dict) else f"HTTP {status}"


async def on_session(event_type: str, data):
    global notified_action
    cs = simplify(data) if event_type != "Delete" and isinstance(data, dict) and "actions" in data else None
    if cs:
        me = data["localPlayerCellId"]
        debug_log(
            "session",
            phase=cs["phase"],
            actions=[
                {k: a.get(k) for k in ("id", "type", "championId", "isInProgress", "completed")}
                for g in data["actions"]
                for a in g
                if a.get("actorCellId") == me
            ],
            me={k: v for p in data["myTeam"] if p["cellId"] == me for k, v in p.items() if k in ("championId", "championPickIntent", "spell1Id", "spell2Id")},
        )
    previous = state["champselect"]
    state["champselect"] = cs
    await broadcast({"type": "champselect", "data": cs})
    action_id = lambda c: (c["action"] or {}).get("id") if c else "none"
    if cs and action_id(previous) != action_id(cs):
        await refresh_champ_lists()
    await refresh_recommended(cs)
    action = cs and cs["action"]
    if action and action["id"] != notified_action:
        notified_action = action["id"]
        verb = "bannir" if action["type"] == "ban" else "choisir"
        await notify(f"À toi de {verb}", f"{cs['timer'] // 1000} s restantes", "crossed_swords")


async def on_event(uri: str, event_type: str, data):
    if uri == PHASE_URI:
        await on_phase(data)
    elif uri == SESSION_URI:
        await on_session(event_type, data)
    elif uri == PERKS_URI and isinstance(data, list):
        state["rune_pages"] = rune_view(data)
        await broadcast({"type": "rune_pages", "pages": state["rune_pages"]})


async def lcu_loop():
    global lcu
    while True:
        creds = await asyncio.to_thread(read_lockfile)
        if not creds:
            await asyncio.sleep(2)
            continue
        lcu = LCU(*creds)
        try:
            async with lcu.session.ws_connect(lcu.base.replace("https", "wss", 1)) as ws:
                for uri in (PHASE_URI, SESSION_URI, PERKS_URI):
                    await ws.send_json([5, "OnJsonApiEvent" + uri.replace("/", "_")])
                _, phase = await lcu.request("GET", PHASE_URI)
                await on_phase(phase)
                status, session = await lcu.request("GET", SESSION_URI)
                if status == 200:
                    await on_session("Update", session)
                print("Client LoL connecté")
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT or not msg.data:
                        continue
                    try:
                        frame = json.loads(msg.data)
                        if not (isinstance(frame, list) and len(frame) == 3 and frame[0] == 8):
                            continue
                        payload = frame[2]
                        await on_event(payload["uri"], payload["eventType"], payload["data"])
                    except (KeyError, TypeError, ValueError, aiohttp.ClientError) as e:
                        print(f"Événement ignoré : {e!r}")
                print("Client LoL déconnecté")
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError):
            pass
        except Exception as e:
            print(f"Erreur LCU : {e!r}")
        finally:
            await lcu.close()
            lcu = None
            state.update(
                phase="Offline", champselect=None, pickable=[], bannable=[], rune_pages=[],
                recommended={"championId": 0, "pages": []}, reco_key=None,
            )
            await broadcast({"type": "phase", "phase": "Offline"})
        await asyncio.sleep(2)


async def handle_action(msg: dict):
    kind = msg.get("action")
    if kind in ("select", "spells", "runes"):
        debug_log("phone", msg=msg)
    if kind == "launch":
        return launch_client()
    if kind == "auto_accept":
        state["auto_accept"] = bool(msg["value"])
        await broadcast({"type": "auto_accept", "value": state["auto_accept"]})
        return None
    if kind == "favorite":
        cid = int(msg["championId"])
        favs = state["favorites"]
        state["favorites"] = [f for f in favs if f != cid] if cid in favs else favs + [cid]
        config["favorites"] = state["favorites"]
        CONFIG_FILE.write_text(json.dumps(config, indent=2))
        await broadcast({"type": "favorites", "favorites": state["favorites"]})
        return None
    if lcu is None:
        return "Client LoL non détecté"
    if kind in SIMPLE_ACTIONS:
        status, data = await lcu.request(*SIMPLE_ACTIONS[kind])
    elif kind == "play":
        return await play(int(msg["queueId"]))
    elif kind == "lobby":
        status, data = await lcu.request("POST", "/lol-lobby/v2/lobby", {"queueId": int(msg["queueId"])})
        if status < 400:
            await refresh_lobby()
    elif kind == "select":
        cs = state["champselect"]
        if not cs:
            return "Pas de sélection en cours"
        if int(msg["championId"]) <= 0:
            return "Choisis un champion"
        if cs["action"]:
            action_id = cs["action"]["id"]
        elif cs["pickActionId"] is not None and not msg.get("lock"):
            action_id = cs["pickActionId"]
        else:
            return "Pas encore ton tour"
        path = f"{SESSION_URI}/actions/{action_id}"
        body = {"championId": int(msg["championId"])}
        if msg.get("lock") and cs["action"]:
            body["completed"] = True
        status, data = await lcu.request("PATCH", path, body)
    elif kind == "spells":
        cs = state["champselect"]
        if not cs:
            return "Pas de sélection en cours"
        body = {
            key: int(msg.get(key) or cs["spells"][i])
            for i, key in enumerate(("spell1Id", "spell2Id"))
            if msg.get(key) or cs["spells"][i]
        }
        status, data = await lcu.request("PATCH", f"{SESSION_URI}/my-selection", body)
    elif kind == "runes":
        status, data = await lcu.request("PUT", "/lol-perks/v1/currentpage", int(msg["pageId"]))
        if status < 400:
            await refresh_rune_pages()
    elif kind == "save_runes":
        return await save_rune_page(msg)
    else:
        return "Action inconnue"
    if status >= 400:
        return error_message(status, data)
    return None


def valid_token(value: str | None) -> bool:
    return bool(value) and secrets.compare_digest(value, config["token"])


async def index(request):
    resp = web.FileResponse(RES_DIR / "index.html", headers={"Cache-Control": "no-store"})
    if valid_token(request.query.get("token")):
        set_token_cookie(resp)
    return resp


def set_token_cookie(resp: web.StreamResponse):
    resp.set_cookie("token", config["token"], max_age=365 * 86400, httponly=True, samesite="Strict")


async def pair(request):
    now = time.monotonic()
    pair_failures[:] = [t for t in pair_failures if now - t < 60]
    if len(pair_failures) >= 5:
        return web.json_response({"error": "Trop d'essais, réessaie dans une minute"}, status=429)
    try:
        code = str((await request.json()).get("code", "")).strip()
    except (ValueError, AttributeError):
        code = ""
    if not secrets.compare_digest(code, pair_code):
        pair_failures.append(now)
        return web.json_response({"error": "Code incorrect"}, status=403)
    print(f"Téléphone appairé par code depuis {request.remote}")
    resp = web.json_response({"token": config["token"]})
    set_token_cookie(resp)
    return resp


def serve_static(name: str):
    path = STATIC_DIR / name
    if not re.fullmatch(r"[\w.-]+", name) or path.suffix not in STATIC_TYPES or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": STATIC_TYPES[path.suffix], "Cache-Control": "public, max-age=86400"})


async def static_file(request):
    return serve_static(request.match_info["name"])


async def manifest(_request):
    return serve_static("manifest.webmanifest")


async def asset(request):
    tail = request.match_info["tail"]
    if ".." in tail or not tail.lower().endswith((".png", ".jpg", ".webp", ".svg")):
        raise web.HTTPNotFound()
    cached = asset_cache.get(tail)
    if cached is None:
        if lcu is None:
            raise web.HTTPServiceUnavailable()
        async with lcu.session.get(f"{lcu.base}/lol-game-data/assets/{tail}") as r:
            if r.status != 200:
                raise web.HTTPNotFound()
            svg = tail.lower().endswith(".svg")
            cached = (await r.read(), "image/svg+xml" if svg else r.content_type)
        asset_cache[tail] = cached
    return web.Response(
        body=cached[0],
        content_type=cached[1],
        headers={
            "Cache-Control": "public, max-age=2592000, immutable",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )


async def ws_handler(request):
    query, cookie = request.query.get("token"), request.cookies.get("token")
    if not (valid_token(query) or valid_token(cookie)):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            print(f"Connexion refusée depuis {request.remote} : token {'invalide' if query or cookie else 'absent'}")
        raise web.HTTPForbidden()
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    clients.add(ws)
    await ws.send_json({"type": "init", **state})
    try:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
                if data.get("action") == "ping":
                    await ws.send_json({"type": "pong", "t": data.get("t")})
                    continue
                error = await handle_action(data)
            except (KeyError, ValueError, TypeError, aiohttp.ClientError, OSError) as e:
                error = repr(e)
            if error:
                await ws.send_json({"type": "error", "message": error})
    finally:
        clients.discard(ws)
    return ws


def setup_output():
    if getattr(sys, "frozen", False) or sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(LOG_FILE, "w", buffering=1, encoding="utf-8")
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass


def pairing_url() -> str:
    return f"http://{local_ip()}:{HTTP_PORT}/?token={config.get('token', '')}"


def status() -> dict:
    return {
        "url": pairing_url() if config else "",
        "code": pair_code,
        "client": lcu is not None,
        "phase": state["phase"],
        "phones": len(clients),
    }


async def main(gui: bool = False):
    global config, http, page_url
    setup_output()
    config = load_config(sys.argv[1:])
    state["favorites"] = config.get("favorites", [])
    http = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())))
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/asset/{tail:.+}", asset)
    app.router.add_post("/pair", pair)
    app.router.add_get("/static/{name}", static_file)
    app.router.add_get("/manifest.webmanifest", manifest)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HTTP_PORT).start()

    page_url = f"http://{local_ip()}:{HTTP_PORT}/"
    url = pairing_url()
    if not gui:
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    print(url)
    print(f"Code d'appairage : {pair_code}")
    if config.get("ntfy_topic"):
        print(f"Notifications ntfy : abonne-toi au topic {config['ntfy_topic']}")
    try:
        await lcu_loop()
    finally:
        await http.close()


if __name__ == "__main__":
    asyncio.run(main())
