"""Génère static/marble-*.jpg avec un générateur de bruit en JS exécuté dans Chrome headless.

Usage : .venv/bin/python tools/make_marble.py static
"""
import asyncio
import base64
import json
import subprocess
import sys
import tempfile

import aiohttp

OUT = sys.argv[1]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

GENERATOR = r"""
function makeNoise(seed) {
  const p = new Uint8Array(512);
  let s = seed * 9301 + 49297;
  const rnd = () => (s = (s * 9301 + 49297) % 233280) / 233280;
  const perm = [...Array(256).keys()];
  for (let i = 255; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [perm[i], perm[j]] = [perm[j], perm[i]]; }
  for (let i = 0; i < 512; i++) p[i] = perm[i & 255];
  const fade = t => t * t * t * (t * (t * 6 - 15) + 10);
  const grad = (h, x, y) => ((h & 1) ? -x : x) + ((h & 2) ? -y : y);
  // bruit de Perlin 2D périodique (période `per`) pour une texture raccordable
  return (x, y, per) => {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
    const X0 = ((xi % per) + per) % per & 255, Y0 = ((yi % per) + per) % per & 255;
    const X1 = ((xi + 1) % per + per) % per & 255, Y1 = ((yi + 1) % per + per) % per & 255;
    const u = fade(xf), v = fade(yf);
    const a = grad(p[p[X0] + Y0], xf, yf), b = grad(p[p[X1] + Y0], xf - 1, yf);
    const c = grad(p[p[X0] + Y1], xf, yf - 1), d = grad(p[p[X1] + Y1], xf - 1, yf - 1);
    return (a + u * (b - a)) + v * ((c + u * (d - c)) - (a + u * (b - a)));
  };
}
function fbm(n, x, y, per, oct) {
  let sum = 0, amp = 0.5, f = 1;
  for (let o = 0; o < oct; o++) { sum += amp * n(x * f, y * f, per * f); amp *= 0.5; f *= 2; }
  return sum;
}
function render(cfg) {
  const S = cfg.size, c = document.createElement("canvas");
  c.width = c.height = S;
  const ctx = c.getContext("2d"), img = ctx.createImageData(S, S), d = img.data;
  const nA = makeNoise(cfg.seed), nB = makeNoise(cfg.seed + 7), nC = makeNoise(cfg.seed + 19);
  const P = cfg.period;
  for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
    const u = x / S * P, v = y / S * P;
    const cloud = fbm(nA, u, v, P, 5);
    // veines vertes larges et douces
    const t1 = fbm(nB, u * 1.0, v * 1.0, P, 6);
    const g = Math.abs(Math.sin((u * cfg.dir[0] + v * cfg.dir[1]) * Math.PI / P * 2 * cfg.waves + t1 * cfg.turb));
    const green = Math.pow(1 - g, cfg.greenSharp);
    // veines dorées fines
    const t2 = fbm(nC, u * 1.25, v * 1.25, P * 1.25, cfg.goldOct);
    const k = Math.abs(Math.sin((u * cfg.dir[1] - v * cfg.dir[0]) * Math.PI / P * 2 * cfg.goldWaves + t2 * cfg.turb * 0.75));
    const gold = Math.pow(1 - k, cfg.goldSharp);
    let r = cfg.base[0] + cloud * cfg.cloud[0], gg = cfg.base[1] + cloud * cfg.cloud[1], b = cfg.base[2] + cloud * cfg.cloud[2];
    r += green * cfg.vein[0]; gg += green * cfg.vein[1]; b += green * cfg.vein[2];
    const ga = gold * cfg.goldAlpha;
    r = r * (1 - ga) + 217 * ga; gg = gg * (1 - ga) + 184 * ga; b = b * (1 - ga) + 104 * ga;
    const i = (y * S + x) * 4;
    d[i] = r; d[i + 1] = gg; d[i + 2] = b; d[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c.toDataURL("image/jpeg", 0.84).split(",")[1];
}
"""

TEXTURES = {
    "marble-dark.jpg": dict(size=1024, seed=3, period=4, dir=[1, 0.5], waves=2, goldWaves=2, turb=5.5,
                            base=[9, 18, 13], cloud=[18, 34, 24], vein=[16, 34, 23], greenSharp=6,
                            goldSharp=34, goldAlpha=0.5, goldOct=5),
    "marble-green.jpg": dict(size=1024, seed=11, period=4, dir=[1, 0.5], waves=2, goldWaves=2, turb=6,
                             base=[33, 56, 41], cloud=[22, 36, 26], vein=[30, 52, 38], greenSharp=5,
                             goldSharp=32, goldAlpha=0.7, goldOct=5),
}


async def main():
    proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9335", f"--user-data-dir={tempfile.mkdtemp()}",
                             "--no-first-run", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with aiohttp.ClientSession() as s:
            for _ in range(50):
                try:
                    async with s.get("http://127.0.0.1:9335/json") as r:
                        page = next(t for t in await r.json() if t["type"] == "page")
                    break
                except Exception:
                    await asyncio.sleep(0.2)
            cdp = await s.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0)
            n = 0

            async def call(m, **p):
                nonlocal n
                n += 1
                i = n
                await cdp.send_json({"id": i, "method": m, "params": p})
                while True:
                    r = await cdp.receive_json()
                    if r.get("id") == i:
                        return r.get("result", {})

            for name, cfg in TEXTURES.items():
                r = await call("Runtime.evaluate", expression=GENERATOR + f"render({json.dumps(cfg)})", returnByValue=True)
                open(f"{OUT}/{name}", "wb").write(base64.b64decode(r["result"]["value"]))
                print(name, "ok")
    finally:
        proc.terminate()


asyncio.run(main())
