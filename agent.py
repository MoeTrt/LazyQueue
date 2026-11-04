import asyncio
import json
import os
import re
import secrets
import socket
import subprocess
import sys
from pathlib import Path

import aiohttp
import qrcode
from aiohttp import web

HTTP_PORT = 8765
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
LOCKFILES = [
    Path(p)
    for p in (
        os.environ.get("LOL_LOCKFILE"),
        "C:/Riot Games/League of Legends/lockfile",
        "/Applications/League of Legends.app/Contents/LoL/lockfile",
    )
    if p
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
    "auto_accept": False,
}
all_spells: list[dict] = []
clients: set[web.WebSocketResponse] = set()
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
            auth=aiohttp.BasicAuth("riot", password),
            connector=aiohttp.TCPConnector(ssl=False),
        )

    async def request(self, method: str, path: str, body=None):
        async with self.session.request(method, self.base + path, json=body) as r:
            text = await r.text()
            return r.status, json.loads(text) if text else None

    async def get_list(self, path: str) -> list:
        status, data = await self.request("GET", path)
        return data if status == 200 and isinstance(data, list) else []

    async def close(self):
        await self.session.close()


def load_config(args: list[str]) -> dict:
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
    if os.name == "nt":
        return None
    try:
        out = subprocess.run(["ps", "-A", "-ww", "-o", "args="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "LeagueClientUx" in line:
            port, token = UX_PORT_RE.search(line), UX_TOKEN_RE.search(line)
            if port and token:
                return int(port.group(1)), token.group(1)
    return None


def read_lockfile():
    for path in LOCKFILES:
        if path.is_file():
            try:
                _, _, port, password, _ = path.read_text().split(":")
                return int(port), password
            except (OSError, ValueError):
                break
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
    current = next(
        (
            a
            for group in session["actions"]
            for a in group
            if a["actorCellId"] == me and a["isInProgress"] and not a["completed"]
        ),
        None,
    )
    mine = next((p for p in session["myTeam"] if p["cellId"] == me), {})
    return {
        "action": {"id": current["id"], "type": current["type"]} if current else None,
        "championId": mine.get("championId") or mine.get("championPickIntent") or 0,
        "spells": [mine.get("spell1Id", 0), mine.get("spell2Id", 0)],
        "timer": session["timer"]["adjustedTimeLeftInPhase"],
    }


async def ensure_static():
    global all_spells
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


def rune_view(pages: list) -> list:
    return [{"id": p["id"], "name": p["name"], "current": p.get("current", False)} for p in pages]


async def refresh_rune_pages():
    state["rune_pages"] = rune_view(await lcu.get_list(PERKS_URI))
    await broadcast({"type": "rune_pages", "pages": state["rune_pages"]})


async def load_champselect():
    await ensure_static()
    _, flow = await lcu.request("GET", "/lol-gameflow/v1/session")
    mode = ((flow or {}).get("gameData") or {}).get("queue", {}).get("gameMode", "CLASSIC")
    state["pickable"] = await lcu.get_list("/lol-champ-select/v1/pickable-champion-ids")
    state["bannable"] = await lcu.get_list("/lol-champ-select/v1/bannable-champion-ids")
    state["spells"] = [s for s in all_spells if mode in s["modes"]]
    await broadcast(
        {
            "type": "cs_data",
            "pickable": state["pickable"],
            "bannable": state["bannable"],
            "spells": state["spells"],
        }
    )
    await refresh_rune_pages()


async def on_phase(phase: str):
    if phase == state["phase"]:
        return
    state["phase"] = phase
    await broadcast({"type": "phase", "phase": phase})
    await ensure_static()
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


async def on_session(event_type: str, data):
    global notified_action
    cs = simplify(data) if event_type != "Delete" and data else None
    state["champselect"] = cs
    await broadcast({"type": "champselect", "data": cs})
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
        creds = read_lockfile()
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
                    opcode, _, payload = json.loads(msg.data)
                    if opcode != 8:
                        continue
                    try:
                        await on_event(payload["uri"], payload["eventType"], payload["data"])
                    except (KeyError, TypeError, aiohttp.ClientError) as e:
                        print(f"Événement ignoré ({payload.get('uri')}): {e!r}")
        except (aiohttp.ClientError, OSError):
            pass
        finally:
            await lcu.close()
            lcu = None
            state.update(phase="Offline", champselect=None, pickable=[], bannable=[], rune_pages=[])
            await broadcast({"type": "phase", "phase": "Offline"})
        await asyncio.sleep(2)


async def handle_action(msg: dict):
    kind = msg.get("action")
    if kind == "launch":
        return launch_client()
    if kind == "auto_accept":
        state["auto_accept"] = bool(msg["value"])
        await broadcast({"type": "auto_accept", "value": state["auto_accept"]})
        return None
    if lcu is None:
        return "Client LoL non détecté"
    if kind in SIMPLE_ACTIONS:
        status, data = await lcu.request(*SIMPLE_ACTIONS[kind])
    elif kind == "lobby":
        status, data = await lcu.request("POST", "/lol-lobby/v2/lobby", {"queueId": int(msg["queueId"])})
    elif kind == "select":
        cs = state["champselect"]
        if not cs or not cs["action"]:
            return "Aucune action en cours"
        path = f"{SESSION_URI}/actions/{cs['action']['id']}"
        status, data = await lcu.request("PATCH", path, {"championId": int(msg["championId"])})
        if status < 400 and msg.get("lock"):
            status, data = await lcu.request("POST", path + "/complete")
    elif kind == "spells":
        body = {"spell1Id": int(msg["spell1Id"]), "spell2Id": int(msg["spell2Id"])}
        status, data = await lcu.request("PATCH", f"{SESSION_URI}/my-selection", body)
    elif kind == "runes":
        status, data = await lcu.request("PUT", "/lol-perks/v1/currentpage", int(msg["pageId"]))
        if status < 400:
            await refresh_rune_pages()
    else:
        return "Action inconnue"
    if status >= 400:
        return (data or {}).get("message", f"HTTP {status}") if isinstance(data, dict) else f"HTTP {status}"
    return None


async def index(_request):
    return web.FileResponse(BASE_DIR / "index.html")


async def asset(request):
    tail = request.match_info["tail"]
    if ".." in tail or not tail.lower().endswith((".png", ".jpg", ".webp")):
        raise web.HTTPNotFound()
    if lcu is None:
        raise web.HTTPServiceUnavailable()
    async with lcu.session.get(f"{lcu.base}/lol-game-data/assets/{tail}") as r:
        if r.status != 200:
            raise web.HTTPNotFound()
        return web.Response(
            body=await r.read(),
            content_type=r.content_type,
            headers={"Cache-Control": "max-age=86400"},
        )


async def ws_handler(request):
    if not secrets.compare_digest(request.query.get("token", ""), config["token"]):
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
                error = await handle_action(json.loads(msg.data))
            except (KeyError, ValueError, TypeError, aiohttp.ClientError, OSError) as e:
                error = repr(e)
            if error:
                await ws.send_json({"type": "error", "message": error})
    finally:
        clients.discard(ws)
    return ws


async def main():
    global config, http, page_url
    config = load_config(sys.argv[1:])
    http = aiohttp.ClientSession()
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/asset/{tail:.+}", asset)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HTTP_PORT).start()

    page_url = f"http://{local_ip()}:{HTTP_PORT}/"
    url = f"{page_url}?token={config['token']}"
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)
    print(url)
    if config.get("ntfy_topic"):
        print(f"Notifications ntfy : abonne-toi au topic {config['ntfy_topic']}")
    try:
        await lcu_loop()
    finally:
        await http.close()


if __name__ == "__main__":
    asyncio.run(main())
