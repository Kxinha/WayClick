"""Passos que olham a tela e põem o cursor numa posição, sem tocar no desktop.

O ponteiro absoluto é capturado pelo harness no instante em que nasce, como os
outros devices do WayClick: o cursor de verdade nunca se mexe. A tela é falsa
(quadros sintéticos do numpy), então nada pede para compartilhar a tela, e o
webhook vai para um servidor HTTP local, em 127.0.0.1.

    QT_QPA_PLATFORM=offscreen nice -n 19 python3 tests/t_screen_macros.py
"""
import http.server, json, math, os, struct, sys, tempfile, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-screen-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
import numpy as np                                          # noqa: E402
from PySide6.QtCore import QCoreApplication                 # noqa: E402
from PySide6.QtGui import QImage                            # noqa: E402

app = QCoreApplication(sys.argv)
a.LANG = "en_US"
K = a.KEYS
EV_ABS, EV_SYN = 3, 0
LEFT, RIGHT = a.BTN["Left"], a.BTN["Right"]
rng = np.random.default_rng(20261120)
calls = []
a.OPENER = lambda url: calls.append(("open", url))


def scene(H, W, cell=8):
    base = rng.integers(0, 256, (H // cell + 1, W // cell + 1, 3))
    big = np.kron(base, np.ones((cell, cell, 1)))[:H, :W]
    big = big + rng.normal(0, 8, big.shape)
    return np.clip(big, 0, 255).astype(np.uint8)


def save_png(rgb, name):
    path = os.path.join(TMP, name)
    with open(path, "wb") as fh:
        fh.write(a.png_bytes(rgb))
    return path


# ------------------------------------------------------------ busca --
img = scene(1080, 1920)
for (th, tw), why in (((12, 14), "small, full size search"),
                      ((30, 50), "medium, half size first"),
                      ((90, 140), "large, quarter size first")):
    y0, x0 = 517, 1311
    tpl = img[y0:y0 + th, x0:x0 + tw].copy()
    noisy = np.clip(img.astype(np.int16) + rng.integers(-6, 7, img.shape) + 14,
                    0, 255).astype(np.uint8)
    t0 = time.perf_counter()
    hit = a.find_image(noisy, tpl, 0.85)
    ms = (time.perf_counter() - t0) * 1000
    check(f"template {tw}x{th} ({why}) found at the exact spot, with noise "
          "and +14 brightness", hit is not None and hit[:2] == (x0, y0),
          f"{hit and (hit[0], hit[1], round(hit[2], 3))}, {ms:.0f} ms")
other = scene(60, 80)
check("a template that is not on screen is not found",
      a.find_image(img, other, 0.85) is None)

# mesma luminância, outra cor: a correlação em cinza casa, a cor não
tex = rng.uniform(0.6, 1.0, (40, 60, 1))
red = np.clip(tex * [200, 50, 50], 0, 255).astype(np.uint8)
green = np.clip(tex * [50, 126, 50], 0, 255).astype(np.uint8)
bg = scene(400, 600)
bg[100:140, 200:260] = green
check("same shape in another color is rejected (gray would match)",
      a.find_image(bg, red, 0.85) is None)
bg[300:340, 400:460] = red
hit = a.find_image(bg, red, 0.85)
check("and the right color is found next to it",
      hit is not None and hit[:2] == (400, 300), str(hit))
canvas = np.full((400, 600, 3), 128, np.uint8)
canvas[300:340, 400:460] = np.clip(red.astype(np.int16) - 70, 0, 255)
check("a darkened (disabled looking) copy does not pass 85%",
      a.find_image(canvas, red, 0.85) is None)

flat = np.full((20, 30, 3), (30, 160, 90), np.uint8)
bg2 = scene(300, 400)
bg2[50:90, 250:300] = (30, 160, 90)
hit = a.find_image(bg2, flat, 0.9)
check("a flat color patch is found by color",
      hit is not None and 250 <= hit[0] <= 270 and 50 <= hit[1] <= 70,
      str(hit))
check("and a different flat color is not",
      a.find_image(bg2, np.full((20, 30, 3), (160, 30, 90), np.uint8),
                   0.9) is None)

# textura fina numa posição ímpar: a grade da busca reduzida cai noutra
# fase do template, e o encaixe perfeito cairia para 0,4
fine = rng.integers(0, 40, (1440, 2560, 3)).astype(np.uint8)
for (th, tw), (y0, x0) in (((24, 32), (721, 853)), ((100, 120), (333, 1001))):
    tpl = fine[y0:y0 + th, x0:x0 + tw].copy()
    t0 = time.perf_counter()
    hit = a.find_image(fine, tpl, 0.85)
    ms = (time.perf_counter() - t0) * 1000
    check(f"fine texture {tw}x{th} at an odd spot of a 2560x1440 screen is "
          "still found", hit is not None and hit[:2] == (x0, y0),
          f"{hit and (hit[0], hit[1], round(hit[2], 3))}, {ms:.0f} ms")

# o mesmo ícone em 41 cores: em cinza todos dão 1,0, e a cor certa não
# pode perder a vaga de candidato para as outras
icons = np.full((1080, 1920, 3), 20, np.uint8)
mask = rng.random((30, 30)) > 0.5
spots = []
for i, color in enumerate(rng.integers(40, 256, (41, 3))):
    y, x = 60 + (i // 8) * 150, 60 + (i % 8) * 220
    icons[y:y + 30, x:x + 30][mask] = color
    spots.append((x, y))
x0, y0 = spots[37]
hit = a.find_image(icons, icons[y0:y0 + 30, x0:x0 + 30].copy(), 0.85)
check("the same icon in 41 colors: the one of the right color is found",
      hit is not None and hit[:2] == (x0, y0), f"{hit and hit[:2]} vs "
      f"{(x0, y0)}")

# duas cores trocadas, com o mesmo brilho e a mesma cor média: o cinza e a
# média não veem diferença, a correlação nas três cores vê
swap = np.zeros((30, 60, 3), np.uint8)
swap[:, 20:40], swap[:, 40:] = (200, 0, 0), (0, 102, 0)
swapped = np.zeros((30, 60, 3), np.uint8)
swapped[:, 20:40], swapped[:, 40:] = (0, 102, 0), (200, 0, 0)
board = np.full((400, 600, 3), 90, np.uint8)
board[100:130, 100:160] = swapped
check("the same picture with two colors swapped (same brightness, same "
      "average color) is rejected", a.find_image(board, swap, 0.85) is None)
board[250:280, 300:360] = swap
hit = a.find_image(board, swap, 0.85)
check("and the real one next to it is found",
      hit is not None and hit[:2] == (300, 250), str(hit))

# várias telas, com escala: o centro achado vira coordenada lógica
left = a.Frame(scene(1080, 1920), 0, 0)
right_px = scene(2160, 3840)                  # 4K a 200%: 1920x1080 lógico
tpl = right_px[1000:1060, 2000:2100].copy()
right = a.Frame(right_px, 1920, 0, 1920, 1080)
hit = a.locate([left, right], tpl, 0.85)
want = (1920 + (2000 + 50) / 2, (1000 + 30) / 2)
check("found on the second screen, at 200%, in logical coordinates",
      hit is not None and abs(hit[0] - want[0]) < 0.6
      and abs(hit[1] - want[1]) < 0.6, f"{hit and hit[:2]} vs {want}")
twice = scene(500, 800)
piece = twice[100:130, 100:150].copy()
twice[300:330, 600:650] = piece
f2 = a.Frame(twice)
h_all = a.locate([f2], piece, 0.9)
h_reg = a.locate([f2], piece, 0.9, [500, 250, 250, 150])
check("a search area picks the copy inside it",
      h_reg is not None and abs(h_reg[0] - 625) < 1 and abs(h_reg[1] - 315)
      < 1 and h_all is not None, f"{h_all and h_all[:2]}, {h_reg and h_reg[:2]}")
# a 200%, o ponto lógico (10, 20) cobre os pixels 20..21 x 40..41; vale o
# do meio dele, e o seletor guarda a cor do mesmo jeito
check("pixel color is read through the screen layout",
      a.pixel_at([left, right], 1920 + 10, 20) == tuple(int(c) for c in
                                                        right_px[41, 21]))
check("color tolerance", a.color_close((100, 150, 200), (110, 140, 205), 10)
      and not a.color_close((100, 150, 200), (111, 150, 200), 10))
back = a.qimage_rgb(QImage.fromData(a.png_bytes(right_px[:50, :70])))
check("PNG written by WayClick reads back the same pixels",
      back.shape == (50, 70, 3) and np.array_equal(back, right_px[:50, :70]))

# ------------------------------------------------- ponteiro absoluto --
ws = (0, 0, 5760, 1080)                       # três telas lado a lado
ok = all(math.floor(a.AbsolutePointer.axis(x, 0, 5760) * 5760 / 65536) == x
         for x in range(0, 5760))
check("every x of a 5760 px workspace lands on its own pixel", ok)
ok = all(math.floor(a.AbsolutePointer.axis(y, 0, 1080) * 1080 / 65536) == y
         for y in range(0, 1080))
check("every y of 1080 px too", ok)
path = a.human_path((0, 0), (800, 300), 40)
dists = [math.hypot(800 - x, 300 - y) for x, y in path]
check("curved path ends exactly on the target, getting closer",
      path[-1] == (800, 300) and len(path) == 40
      and sum(b <= a_ + 1 for a_, b in zip(dists, dists[1:])) >= 37)
mid = path[19]
off = abs((mid[0] * 300 - mid[1] * 800) / math.hypot(800, 300))
check("and it bends away from the straight line", off > 2, f"{off:.1f} px")


class AbsRecorder(h.Recorder):
    """Como o Recorder, mas guarda os SYN: é neles que o compositor lê a
    posição. O kernel não repete um eixo que não mudou, então o estado dos
    eixos atravessa o clear(), como no compositor."""

    def __init__(self, node):
        super().__init__(node)
        self.state = [0, 0]
        self.base = [0, 0]

    def clear(self):
        self.poll()
        self.events = []
        self.base = list(self.state)

    def poll(self, seconds=0.0):
        if seconds:
            time.sleep(seconds)
        time.sleep(0.01)
        with open(self.path, "rb") as fh:
            fh.seek(self.pos)
            data = fh.read()
        usable = len(data) - len(data) % h.SIZE
        self.pos += usable
        for i in range(0, usable, h.SIZE):
            sec, usec, t, c, v = struct.unpack_from(h.FMT, data, i)
            self.events.append((t, c, v, sec + usec / 1e6))
            if t == EV_ABS and c in (0, 1):
                self.state[c] = v
        return self.events

    def frames(self, ws):
        """(x, y, botões, instante) a cada SYN, como o libinput entregaria."""
        ax, ay = self.base
        out, btn = [], set()
        for t, c, v, ts in self.events:
            if t == EV_ABS:
                ax, ay = (v, ay) if c == 0 else (ax, v)
            elif t == 1:
                (btn.add if v else btn.discard)(c)
            elif t == EV_SYN:
                out.append((ws[0] + math.floor(ax * ws[2] / 65536),
                            ws[1] + math.floor(ay * ws[3] / 65536),
                            frozenset(btn), ts))
        return out


ptr = a.AbsolutePointer()
vk, vm = a.VirtualKeyboard(), a.VirtualMouse()
check("the absolute pointer was captured by the test the moment it was born",
      ptr.node in h.READERS and vk.node in h.READERS and vm.node in h.READERS)
rec, keys = AbsRecorder(ptr.node), h.Recorder(vk.node)


class FakeScreen:
    """A tela que o motor enxerga: uma função do número da chamada."""

    def __init__(self, frames=None, ok=True, error=""):
        self.script = frames or (lambda n: [])
        self.ok, self.error = ok, error
        self.state = "on" if ok else "failed"
        self.users = self.calls = 0

    def ensure(self, timeout=None):
        return self.ok

    def acquire(self):
        self.users += 1

    def release(self):
        self.users -= 1

    def frames(self):
        self.calls += 1
        return self.script(self.calls)


hub = a.HUB()
engine = a.MacroEngine(lambda: (vk, vm), hub=hub)
engine.pointer, engine.workspace = ptr, ws
problems, messages = [], []
engine.problem.connect(problems.append)
engine.message.connect(lambda t, m: messages.append((t, m)))


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def run(steps, screen=None, press_ms=20.0, timeout=5.0):
    engine.screen = screen
    m = a.normalize_macro({"name": "t", "mode": "once", "repeat": False,
                           "press_ms": press_ms, "steps": steps})
    rec.clear()
    keys.clear()
    t0 = time.perf_counter()
    r = engine._start(a.CompiledMacro(m))
    r.join(timeout)
    took = time.perf_counter() - t0
    pump(0.03)
    rec.poll(0.02)
    keys.poll()
    return r, took


def downs(code):
    return [e for e in keys.keys(code, 1)]


r, _t = run([{"op": "point", "x": 1234, "y": 567, "click": "left"}])
fr = rec.frames(ws)
pos = [f for f in fr if not f[2]]
clicks = [f for f in fr if LEFT in f[2]]
check("Click at 1234, 567: the pointer goes there, then presses and releases",
      pos and pos[0][:2] == (1234, 567) and len(clicks) == 1
      and clicks[0][:2] == (1234, 567) and fr[-1][2] == frozenset(),
      f"{[f[:2] for f in fr]}")
hover = clicks[0][3] - pos[0][3] if pos and clicks else 0
check("with a short pause before the press, for hover",
      0.02 <= hover <= 0.08, f"{hover * 1000:.1f} ms")

r, _t = run([{"op": "point", "x": 1234, "y": 567}])
fr = rec.frames(ws)
check("going to the same spot again still makes the cursor move there "
      "(the kernel drops repeated values)",
      fr and fr[-1][:2] == (1234, 567) and len(fr) >= 2, str(fr))

r, took = run([{"op": "point", "x": 4000, "y": 900, "human": True,
                "ms": 250}])
fr = rec.frames(ws)
xs = [f[0] for f in fr]
check("curved movement goes in many small steps and ends on the target",
      len(fr) >= 20 and fr[-1][:2] == (4000, 900) and xs[0] < xs[-1],
      f"{len(fr)} frames, last {fr[-1][:2] if fr else None}")
check("in the time asked", 0.22 <= took <= 0.45, f"{took * 1000:.0f} ms")

r, _t = run([{"op": "point", "x": 100, "y": 100, "click": "double"}])
fr = rec.frames(ws)
presses = sum(1 for p_, c in zip(fr, fr[1:]) if LEFT not in p_[2]
              and LEFT in c[2])
check("double click presses twice", presses == 2, str(presses))

r, _t = run([{"op": "point", "x": 300, "y": 300, "spread": 20}])
fr = rec.frames(ws)
check("random offset stays within ±20 px",
      fr and abs(fr[-1][0] - 300) <= 20 and abs(fr[-1][1] - 300) <= 20,
      str(fr[-1][:2] if fr else None))

# ----------------------------------------------------- passos de imagem --
screen1 = scene(1080, 1920)
button = screen1[700:740, 3000 - 1920:3000 - 1920 + 120].copy()
other_screen = a.Frame(scene(1080, 1920), 0, 0)
with_button = [other_screen, a.Frame(screen1, 1920, 0)]
blank = [other_screen, a.Frame(scene(1080, 1920), 1920, 0)]
tpl_path = save_png(button, "play button.png")
center = (1920 + (3000 - 1920) + 60, 700 + 20)

r, took = run([{"op": "image", "path": tpl_path, "wait": 2000,
                "act": "left"},
               {"op": "tap", "code": K["X"]}],
              FakeScreen(lambda n: with_button))
fr = rec.frames(ws)
clicks = [f for f in fr if LEFT in f[2]]
check("image found on the second screen gets clicked in its center",
      clicks and abs(clicks[0][0] - center[0]) <= 1
      and abs(clicks[0][1] - center[1]) <= 1,
      f"{clicks[0][:2] if clicks else None} vs {center}, {took * 1000:.0f} ms")
check("and the macro goes on to the next step", len(downs(K["X"])) == 1)

screen = FakeScreen(lambda n: blank if n < 4 else with_button)
r, took = run([{"op": "image", "path": tpl_path, "wait": 3000,
                "act": "none"},
               {"op": "tap", "code": K["X"]}], screen)
check("waiting for an image: it keeps looking until it shows up",
      screen.calls == 4 and len(downs(K["X"])) == 1 and 0.25 <= took < 1.0,
      f"{screen.calls} looks, {took * 1000:.0f} ms")
check("and the screen is given back when the macro ends", screen.users == 0)

r, took = run([{"op": "image", "path": tpl_path, "wait": 400,
                "fail": "stop"},
               {"op": "tap", "code": K["X"]}],
              FakeScreen(lambda n: blank))
check("not found in time: Stop the macro ends it there",
      not downs(K["X"]) and 0.38 <= took < 0.8 and r.stopping,
      f"{took * 1000:.0f} ms")

r, _t = run([{"op": "image", "path": tpl_path, "wait": 0, "fail": "jump",
              "fail_to": 3},
             {"op": "tap", "code": K["Y"]},
             {"op": "tap", "code": K["X"]}],
            FakeScreen(lambda n: blank))
check("not found: Go to step 3 skips step 2",
      not downs(K["Y"]) and len(downs(K["X"])) == 1)

r, _t = run([{"op": "image", "path": tpl_path, "wait": 0, "act": "none",
              "ok": "jump", "ok_to": 3},
             {"op": "tap", "code": K["Y"]},
             {"op": "tap", "code": K["X"]}],
            FakeScreen(lambda n: with_button))
check("found: Go to step 3 skips step 2",
      not downs(K["Y"]) and len(downs(K["X"])) == 1)

screen = FakeScreen(lambda n: blank if n < 6 else with_button)
r, _t = run([{"op": "tap", "code": K["Y"]},
             {"op": "image", "path": tpl_path, "wait": 0, "act": "none",
              "fail": "restart"},
             {"op": "tap", "code": K["X"]}], screen)
check("not found: Start over runs from step 1 until it is there",
      len(downs(K["Y"])) == 6 and len(downs(K["X"])) == 1,
      f"Y ×{len(downs(K['Y']))}")

screen = FakeScreen(lambda n: with_button if n < 3 else blank)
r, took = run([{"op": "image", "path": tpl_path, "want": "gone",
                "wait": 3000},
               {"op": "tap", "code": K["X"]}], screen)
check("Wait until it is gone: goes on once the image disappears",
      screen.calls == 3 and len(downs(K["X"])) == 1 and not
      [f for f in rec.frames(ws) if f[2]], f"{screen.calls} looks")

r, _t = run([{"op": "image", "path": tpl_path, "act": "left", "wait": 1000,
              "conf": 0.85, "region": [0, 0, 1920, 1080]},
             {"op": "tap", "code": K["X"]}],
            FakeScreen(lambda n: with_button))
check("outside the search area it is not found (and nothing is clicked)",
      not downs(K["X"]) and not [f for f in rec.frames(ws) if f[2]])

problems.clear()
r, _t = run([{"op": "image", "path": os.path.join(TMP, "missing.png"),
              "wait": 0, "fail": "next"},
             {"op": "tap", "code": K["X"]}],
            FakeScreen(lambda n: with_button))
pump(0.05)
check("a missing image file is reported, and the else path is taken",
      problems and "could not be read" in problems[0]
      and len(downs(K["X"])) == 1, str(problems))

problems.clear()
r, _t = run([{"op": "image", "path": tpl_path, "wait": 0, "fail": "next"},
             {"op": "pixel", "x": 1, "y": 1, "color": "#000000",
              "wait": 0, "fail": "next"},
             {"op": "tap", "code": K["X"]}],
            FakeScreen(ok=False, error="Screen sharing was not allowed."))
pump(0.05)
check("when the screen cannot be seen it says so once, and goes on",
      problems == ["t: Screen sharing was not allowed."]
      and len(downs(K["X"])) == 1, str(problems))

stopper = FakeScreen(lambda n: blank)
engine.screen = stopper
m = a.normalize_macro({"name": "w", "mode": "toggle", "repeat": True,
                       "steps": [{"op": "image", "path": tpl_path,
                                  "wait": 60000}]})
r = engine._start(a.CompiledMacro(m))
time.sleep(0.35)
t0 = time.perf_counter()
r.stop()
r.join(2)
check("stopping a macro that waits for an image is immediate, and lets "
      "the screen go", not r.is_alive() and time.perf_counter() - t0 < 0.3
      and stopper.users == 0, f"{(time.perf_counter() - t0) * 1000:.0f} ms")

# ------------------------------------------------------- cor de pixel --
px_frames = [a.Frame(np.full((100, 100, 3), (200, 40, 40), np.uint8), 0, 0),
             a.Frame(np.full((200, 200, 3), (10, 200, 30), np.uint8), 100, 0,
                     100, 100)]
r, _t = run([{"op": "pixel", "x": 150, "y": 50, "color": "#14c81e",
              "tol": 12, "act": "left"},
             {"op": "tap", "code": K["X"]}],
            FakeScreen(lambda n: px_frames))
clicks = [f for f in rec.frames(ws) if LEFT in f[2]]
check("pixel with the color (within tolerance) is clicked",
      clicks and clicks[0][:2] == (150, 50) and len(downs(K["X"])) == 1)
r, _t = run([{"op": "pixel", "x": 50, "y": 50, "color": "#14c81e",
              "tol": 12, "wait": 0, "fail": "stop"},
             {"op": "tap", "code": K["X"]}],
            FakeScreen(lambda n: px_frames))
check("a different color takes the else path", not downs(K["X"]))
r, _t = run([{"op": "pixel", "x": 50, "y": 50, "color": "#c82828",
              "want": "gone", "wait": 0, "fail": "stop"},
             {"op": "tap", "code": K["X"]}],
            FakeScreen(lambda n: px_frames))
check("'loses the color' fails while the color is still there",
      not downs(K["X"]))

# --------------------------------------------------- avisos e links --
got = []
done = threading.Event()


class Hook(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        got.append((self.path, self.headers.get("Content-Type"),
                    self.headers.get("User-Agent"), body))
        self.send_response(204)
        self.end_headers()
        done.set()

    def log_message(self, *x):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), Hook)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/api/webhooks/1/abc"
r, _t = run([{"op": "notify", "text": "Victory!", "url": url, "shot": True}],
            FakeScreen(lambda n: px_frames))
done.wait(5)
ok = bool(got)
if ok:
    path_, ctype, ua, body = got[0]
    bound = ctype.split("boundary=")[1]
    parts = [p for p in body.split(b"--" + bound.encode()) if b"name=" in p]
    payload = json.loads(parts[0].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
    pngs = [p.split(b"\r\n\r\n", 1)[1][:-2] for p in parts[1:]]
    sizes = [QImage.fromData(p).size() for p in pngs]
    ok = (path_ == "/api/webhooks/1/abc" and payload["content"] == "Victory!"
          and [(s.width(), s.height()) for s in sizes] == [(100, 100),
                                                           (200, 200)]
          and ua.startswith("WayClick/"))
check("webhook gets the message and one screenshot per screen", ok,
      f"{len(got)} posts")
got.clear()
done.clear()
r, _t = run([{"op": "notify", "text": "no shot", "url": url}])
done.wait(5)
check("without a screenshot it is plain JSON",
      got and got[0][1] == "application/json"
      and json.loads(got[0][3])["content"] == "no shot")
srv.shutdown()
problems.clear()
r, _t = run([{"op": "notify", "text": "x", "url": "http://127.0.0.1:9/nope"}])
deadline = time.monotonic() + 5
while not problems and time.monotonic() < deadline:
    pump(0.05)
check("a webhook that does not answer is reported",
      problems and "webhook" in problems[0], str(problems))
messages.clear()
r, _t = run([{"op": "notify", "text": "Wave 40 reached"}])
pump(0.05)
check("without a webhook it becomes a desktop notification",
      messages == [("t", "Wave 40 reached")])
calls.clear()
run([{"op": "open", "url": "roblox://placeId=1"}])
check("Open a link hands it to the default program",
      calls == [("open", "roblox://placeId=1")])

# ------------------------------------------- formato, rótulos, AHK --
norm = a.normalize_step
check("bad steps are refused",
      norm({"op": "image"}) is None and norm({"op": "pixel", "color": "red"})
      is None and norm({"op": "open", "url": "-rf"}) is None
      and norm({"op": "notify"}) is None)
s = norm({"op": "image", "path": "/x.png", "conf": 7, "wait": -5,
          "act": "evil", "ok": "jump", "ok_to": 99999, "region": [1, 2, 0, 5]})
check("image step values are clamped",
      s == {"op": "image", "want": "seen", "wait": 0.0, "path": "/x.png",
            "conf": 1.0, "act": "left", "ok": "jump", "ok_to": a.MAX_STEPS,
            "fail": "stop"}, str(s))
check("a webhook must be http(s)",
      "url" not in norm({"op": "notify", "text": "a", "url": "file:///etc"}))
labels = [a.step_label(norm(x)) for x in (
    {"op": "point", "x": 10, "y": 20, "click": "right", "human": True},
    {"op": "image", "path": "/p/Play.png", "wait": 5000, "fail": "jump",
     "fail_to": 2},
    {"op": "pixel", "x": 1, "y": 2, "color": "#FF0000", "want": "gone",
     "wait": 0, "ok": "restart", "fail": "next"},
    {"op": "notify", "text": "hi", "url": "https://x", "shot": True},
    {"op": "open", "url": "https://example.com"})]
check("step labels read well", labels == [
    "Right click at 10, 20, curved",
    "Find “Play”, up to 5 s, click it; else go to step 2",
    "Pixel 1, 2 is not #ff0000, once, then start over",
    "Notify “hi” + screenshot (webhook)",
    "Open https://example.com"], "\n" + "\n".join(labels))
a.LANG = "pt_BR"
check("and in Portuguese",
      a.step_label(norm({"op": "image", "path": "/p/Play.png",
                         "wait": 5000})) ==
      "Achar “Play”, até 5 s, clicar ali; senão parar",
      a.step_label(norm({"op": "image", "path": "/p/Play.png",
                         "wait": 5000})))
a.LANG = "en_US"

imp = a.AhkImport("F1::\nClick 100, 200\nMouseMove, 50, 60\nreturn\n")
st = imp.macros[0]["steps"] if imp.macros else []
check("AHK Click X, Y and MouseMove X, Y become screen positions",
      [(x["op"], x.get("x"), x.get("y")) for x in st][:3]
      == [("point", 100, 200), ("tap", None, None), ("point", 50, 60)]
      and any("active window" in n[1] for n in imp.notes),
      a.macro_summary(imp.macros[0]) if imp.macros else "none")
imp = a.AhkImport("CoordMode, Mouse, Screen\nF1::\nClick 100, 200\nreturn\n")
check("with CoordMode Mouse, Screen there is nothing to warn about",
      imp.macros and imp.macros[0]["steps"][0]["op"] == "point"
      and not any("active window" in n[1] for n in imp.notes))

engine.close()
ptr.close()
vk.close()
vm.close()
code = h.finish()
import shutil                                               # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(code)
