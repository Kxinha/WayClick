"""Passos de farm: vigiar a tela por baixo dos passos, contar, janelas,
limite de rodadas, campos nos avisos e o gravador. Isolado como os outros
(ver harness.py): a tela é falsa, as janelas são falsas, o webhook vai para um
servidor em 127.0.0.1 e os devices do WayClick são capturados ao nascer.

    env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen nice -n 19 \\
        dbus-run-session -- python3 -B tests/t_farm_macros.py
"""
import http.server, json, math, os, struct, sys, tempfile, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-farm-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
import numpy as np                                          # noqa: E402
from PySide6.QtCore import QCoreApplication                 # noqa: E402

app = QCoreApplication(sys.argv)
a.LANG = "en_US"
K = a.KEYS
EV_ABS, EV_SYN, EV_KEY = 3, 0, 1
LEFT = a.BTN["Left"]
rng = np.random.default_rng(20261121)
calls = []
a.OPENER = lambda url: calls.append(("open", url))


def scene(H, W, cell=8):
    base = rng.integers(0, 256, (H // cell + 1, W // cell + 1, 3))
    big = np.kron(base, np.ones((cell, cell, 1)))[:H, :W]
    return np.clip(big + rng.normal(0, 8, big.shape), 0, 255).astype(np.uint8)


def save_png(rgb, name):
    path = os.path.join(TMP, name)
    with open(path, "wb") as fh:
        fh.write(a.png_bytes(rgb))
    return path


class AbsRecorder(h.Recorder):
    """O ponteiro absoluto como o compositor o lê (ver t_screen_macros)."""

    def __init__(self, node):
        super().__init__(node)
        self.state, self.base = [0, 0], [0, 0]

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

    def clicks(self, ws):
        """(x, y, instante) de cada aperto do botão esquerdo."""
        ax, ay = self.base
        out = []
        for t, c, v, ts in self.events:
            if t == EV_ABS:
                ax, ay = (v, ay) if c == 0 else (ax, v)
            elif t == EV_KEY and c == LEFT and v == 1:
                out.append((ws[0] + math.floor(ax * ws[2] / 65536),
                            ws[1] + math.floor(ay * ws[3] / 65536), ts))
        return out


class FakeScreen:
    """A tela que o motor vê: uma função do número da chamada."""

    def __init__(self, script):
        self.script, self.calls, self.users = script, 0, 0
        self.ok, self.error, self.state = True, "", "on"

    def ensure(self, timeout=None):
        return True

    def acquire(self):
        self.users += 1

    def release(self):
        self.users -= 1

    def frames(self):
        self.calls += 1
        return self.script(self.calls)


class FakeWindows:
    """O KWin dos testes: find(texto, focar) segue um roteiro."""

    def __init__(self, script):
        self.script, self.asked = script, []

    def find(self, match, focus=False):
        self.asked.append((match, focus))
        return self.script(len(self.asked))


ws = (0, 0, 3840, 1080)
ptr = a.AbsolutePointer()
vk, vm = a.VirtualKeyboard(), a.VirtualMouse()
rec, keys = AbsRecorder(ptr.node), h.Recorder(vk.node)
mouse_out = h.Recorder(vm.node)
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


def run(steps, screen=None, windows=None, timeout=8.0, **macro):
    engine.screen, engine.windows = screen, windows
    spec = {"name": "t", "mode": "once", "repeat": False, "press_ms": 20.0,
            "steps": steps}
    spec.update(macro)
    m = a.normalize_macro(spec)
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
    return keys.keys(code, 1)


# ---------------------------------------------------------------- telas --
screen1 = scene(1080, 1920)
button = screen1[600:640, 900:1020].copy()
center = (1920 + 900 + 60, 600 + 20)
left_screen = a.Frame(scene(1080, 1920), 0, 0)
with_button = [left_screen, a.Frame(screen1, 1920, 0)]
blank = [left_screen, a.Frame(scene(1080, 1920), 1920, 0)]
tpl = save_png(button, "Skip.png")

# ------------------------------------------------------------- vigiar --
scr = FakeScreen(lambda n: with_button if n in (3, 4) else blank)
r, took = run([{"op": "watch", "path": tpl, "every": 100, "act": "left"},
               {"op": "wait", "ms": 1200},
               {"op": "tap", "code": K["X"]}], scr)
clicks = rec.clicks(ws)
check("a watch clicks the image when it shows up in the middle of a wait",
      len(clicks) >= 1 and abs(clicks[0][0] - center[0]) <= 1
      and abs(clicks[0][1] - center[1]) <= 1,
      f"{[c[:2] for c in clicks]} vs {center}, {scr.calls} looks")
check("and the wait still ends on time, then the next step runs",
      1.15 <= took <= 1.6 and len(downs(K["X"])) == 1,
      f"{took * 1000:.0f} ms")
check("it checked on its own interval, not in a tight loop",
      8 <= scr.calls <= 16, f"{scr.calls} looks in {took:.2f} s")
check("and gave the screen back at the end", scr.users == 0)

scr = FakeScreen(lambda n: with_button if n >= 3 else blank)
r, took = run([{"op": "watch", "path": tpl, "every": 100, "act": "none",
                "ok": "jump", "ok_to": 4},
               {"op": "wait", "ms": 5000},
               {"op": "tap", "code": K["Y"]},
               {"op": "tap", "code": K["X"]}], scr)
check("a watch that goes to step 4 cuts the wait short and skips step 3",
      took < 1.0 and not downs(K["Y"]) and len(downs(K["X"])) == 1
      and not rec.clicks(ws), f"{took * 1000:.0f} ms")

scr = FakeScreen(lambda n: with_button if n >= 2 else blank)
r, took = run([{"op": "down", "code": K["W"]},
               {"op": "watch", "path": tpl, "every": 100, "act": "none",
                "ok": "jump", "ok_to": 5},
               {"op": "wait", "ms": 5000},
               {"op": "tap", "code": K["Y"]},
               {"op": "tap", "code": K["X"]}], scr)
w_up = keys.keys(K["W"], 0)
x_down = downs(K["X"])
check("what the sequence held is let go when a watch takes it elsewhere",
      w_up and x_down and w_up[0][3] <= x_down[0][3] and took < 1.0,
      f"W up {bool(w_up)}, X {len(x_down)}")

scr = FakeScreen(lambda n: with_button if n >= 2 else blank)
r, took = run([{"op": "watch", "path": tpl, "every": 100, "act": "none",
                "ok": "stop"},
               {"op": "wait", "ms": 5000},
               {"op": "tap", "code": K["X"]}], scr)
check("a watch can stop the macro", r.stopping and took < 1.0
      and not downs(K["X"]))

scr = FakeScreen(lambda n: blank if n >= 3 else with_button)
r, took = run([{"op": "watch", "path": tpl, "want": "gone", "every": 100,
                "ok": "jump", "ok_to": 3},
               {"op": "wait", "ms": 5000},
               {"op": "tap", "code": K["X"]}], scr)
check("a watch for something to go away fires when it is gone",
      took < 1.0 and len(downs(K["X"])) == 1, f"{took * 1000:.0f} ms")

# o fim da partida continua na tela depois do salto: o vigia não pode
# disparar de novo e prender o macro num laço
scr = FakeScreen(lambda n: with_button if n >= 2 else blank)
r, took = run([{"op": "watch", "path": tpl, "every": 50, "act": "none",
                "ok": "jump", "ok_to": 3},
               {"op": "wait", "ms": 5000},
               {"op": "wait", "ms": 500},
               {"op": "tap", "code": K["X"]}], scr)
check("after a watch jumps, it is off: the screen that made it fire does "
      "not make it fire again", 0.45 <= took < 1.3
      and len(downs(K["X"])) == 1, f"{took * 1000:.0f} ms")

# nunca no meio de um aperto: a tecla fica o tempo todo dela
scr = FakeScreen(lambda n: with_button if n >= 2 else blank)
r, took = run([{"op": "watch", "path": tpl, "every": 50, "act": "none",
                "ok": "jump", "ok_to": 4},
               {"op": "tap", "code": K["Y"]},
               {"op": "wait", "ms": 3000},
               {"op": "tap", "code": K["X"]}], scr, press_ms=300.0)
yd, yu = downs(K["Y"]), keys.keys(K["Y"], 0)
held = yu[0][3] - yd[0][3] if yd and yu else 0
check("a watch never cuts a key press short", 0.28 <= held <= 0.4
      and len(downs(K["X"])) == 1, f"held {held * 1000:.0f} ms")

problems.clear()
r, took = run([{"op": "watch", "path": os.path.join(TMP, "nope.png"),
                "every": 100},
               {"op": "wait", "ms": 400},
               {"op": "tap", "code": K["X"]}], FakeScreen(lambda n: blank))
pump(0.05)
check("a watch with a missing image is reported once and switched off",
      len(problems) == 1 and "could not be read" in problems[0]
      and len(downs(K["X"])) == 1, str(problems))

px = [a.Frame(np.full((100, 100, 3), (10, 200, 30), np.uint8), 0, 0)]
r, took = run([{"op": "watch", "kind": "pixel", "x": 50, "y": 50,
                "color": "#0ac81e", "every": 100, "act": "left",
                "ok": "jump", "ok_to": 3},
               {"op": "wait", "ms": 3000},
               {"op": "tap", "code": K["X"]}],
              FakeScreen(lambda n: px))
clicks = rec.clicks(ws)
check("a pixel watch clicks the point and goes on",
      clicks and clicks[0][:2] == (50, 50) and took < 1.0
      and len(downs(K["X"])) == 1, str(clicks))

# ----------------------------------------------------- contar e campos --
got, done = [], threading.Event()


class Hook(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        got.append(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(204)
        self.end_headers()
        done.set()

    def log_message(self, *x):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), Hook)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/api/webhooks/1/abc"
r, _t = run([{"op": "count", "name": "Wins"},
             {"op": "count", "name": "wins"},
             {"op": "wait", "ms": 50},
             {"op": "notify", "url": url,
              "text": "won {wins}, lost {losses}, run {run}, {time}, {nope}"},
             {"op": "count", "name": "losses", "every": 0}])
done.wait(5)
body = json.loads(got[0])["content"] if got else ""
check("counters and {run}, {time} are filled in the webhook text",
      body == "won 2, lost 0, run 1, 0:00, {nope}", body)
srv.shutdown()

messages.clear()
r, _t = run([{"op": "count", "name": "m"},
             {"op": "notify", "text": "match {m} of run {run}"}],
            mode="toggle", repeat=True, period_ms=0, limit=3)
pump(0.1)
check("a repeating macro with a limit stops by itself after that many runs",
      not r.is_alive() and r.count == 3
      and [t for _x, t in messages] == ["match 1 of run 1", "match 2 of run 2",
                                        "match 3 of run 3"], str(messages))

r, _t = run([{"op": "count", "name": "c", "every": 3, "ok": "stop"},
             {"op": "tap", "code": K["X"]}],
            mode="toggle", repeat=True, period_ms=0)
check("count every 3, then stop: two runs press X, the third stops",
      not r.is_alive() and len(downs(K["X"])) == 2, str(len(downs(K["X"]))))
check("time reads like a clock", (a.clock_text(5), a.clock_text(125),
                                  a.clock_text(3725)) == ("0:05", "2:05",
                                                          "1:02:05"))

# -------------------------------------------------------------- janelas --
fw = FakeWindows(lambda n: {"found": True, "title": "Sober"})
r, took = run([{"op": "window", "match": "Sober", "act": "focus"},
               {"op": "tap", "code": K["X"]}], windows=fw)
check("Focus the window asks KWin to bring it to the front, and goes on",
      fw.asked == [("Sober", True)] and len(downs(K["X"])) == 1)

fw = FakeWindows(lambda n: {"found": n >= 3})
r, took = run([{"op": "window", "match": "sober", "wait": 5000},
               {"op": "tap", "code": K["X"]}], windows=fw)
check("waiting for a window to open asks about once a second",
      len(fw.asked) == 3 and 1.8 <= took <= 2.6
      and len(downs(K["X"])) == 1, f"{len(fw.asked)} asks, {took:.2f} s")

fw = FakeWindows(lambda n: {"found": False})
r, took = run([{"op": "window", "match": "sober", "wait": 1500,
                "fail": "jump", "fail_to": 3},
               {"op": "tap", "code": K["Y"]},
               {"op": "tap", "code": K["X"]}], windows=fw)
check("a window that never opens takes the else path in time",
      1.4 <= took <= 2.0 and not downs(K["Y"]) and len(downs(K["X"])) == 1,
      f"{took:.2f} s")

fw = FakeWindows(lambda n: {"found": n < 3})
r, took = run([{"op": "window", "match": "sober", "want": "gone",
                "wait": 5000},
               {"op": "tap", "code": K["X"]}], windows=fw)
check("and it can wait for a window to close",
      len(fw.asked) == 3 and len(downs(K["X"])) == 1)

problems.clear()
r, took = run([{"op": "window", "match": "sober", "wait": 3000,
                "fail": "next"},
               {"op": "window", "match": "sober", "fail": "next"},
               {"op": "tap", "code": K["X"]}], windows=None)
pump(0.05)
check("without KWin the window step says so once, and takes the else path",
      len(problems) == 1 and "KWin" in problems[0] and took < 0.5
      and len(downs(K["X"])) == 1, str(problems))

fw = FakeWindows(lambda n: {"found": n < 2})
r, took = run([{"op": "watch", "kind": "window", "match": "sober",
                "want": "gone", "every": 100, "ok": "jump", "ok_to": 3},
               {"op": "wait", "ms": 6000},
               {"op": "tap", "code": K["X"]}], windows=fw)
check("a window watch notices the game closed (once a second at most)",
      len(fw.asked) == 2 and 0.9 <= took <= 1.6
      and len(downs(K["X"])) == 1, f"{len(fw.asked)} asks, {took:.2f} s")

# --------------------------------------------------- formato e rótulos --
norm = a.normalize_step
check("bad farm steps are refused",
      norm({"op": "watch"}) is None
      and norm({"op": "watch", "kind": "window"}) is None
      and norm({"op": "count", "name": "two words"}) is None
      and norm({"op": "window", "match": "   "}) is None)
s = norm({"op": "watch", "kind": "window", "match": " Sober  game ",
          "act": "left", "every": 5, "ok": "jump", "ok_to": 0})
check("watch values are clamped, and a window watch never clicks",
      s == {"op": "watch", "kind": "window", "want": "seen", "every": 100.0,
            "match": "Sober game", "act": "none", "ok": "jump", "ok_to": 1},
      str(s))
check("a count without every has no flow",
      norm({"op": "count", "name": "X", "ok": "jump"}) ==
      {"op": "count", "name": "x", "every": 0})
labels = [a.step_label(norm(x)) for x in (
    {"op": "watch", "path": "/p/Skip.png", "every": 2000},
    {"op": "watch", "kind": "window", "match": "Sober", "want": "gone",
     "every": 5000, "ok": "restart"},
    {"op": "watch", "kind": "pixel", "x": 3, "y": 4, "color": "#ff0000",
     "act": "none", "every": 500, "ok": "jump", "ok_to": 9},
    {"op": "count", "name": "matches", "every": 10, "ok": "stop"},
    {"op": "window", "match": "Sober", "act": "focus", "wait": 5000,
     "fail": "restart"},
    {"op": "window", "match": "Sober", "wait": 0, "ok": "jump", "ok_to": 4,
     "fail": "next"})]
check("farm step labels read well", labels == [
    "Watch for “Skip”, every 2 s, click it",
    "Watch for the “Sober” window to close, every 5 s, then start over",
    "Watch pixel 3, 4 for #ff0000, every 0.5 s, then go to step 9",
    "Count {matches}, every 10 then stop",
    "Focus the “Sober” window, up to 5 s; else start over",
    "Check the “Sober” window is open, once, then go to step 4"],
      "\n" + "\n".join(labels))
m = a.normalize_macro({"code": K["F6"], "key": "F6", "repeat": True,
                       "period_ms": 0, "limit": 25, "steps": [
                           {"op": "tap", "code": K["E"]}]})
check("the macro list says when it stops",
      a.macro_summary(m) == "F6, toggle, stop after 25 runs: Tap E",
      a.macro_summary(m))
c = a.CompiledMacro(a.normalize_macro({"steps": [
    {"op": "watch", "path": "/x.png"}, {"op": "count", "name": "a"},
    {"op": "count", "name": "a"}, {"op": "window", "match": "x"}]}))
check("what a farm macro needs is known up front",
      c.uses_screen and c.uses_pointer and c.uses_windows
      and c.counters == ("a",))
a.LANG = "pt_BR"
pt = [a.step_label(norm(x)) for x in (
    {"op": "watch", "path": "/p/Skip.png", "every": 2000},
    {"op": "count", "name": "partidas", "every": 10, "ok": "stop"},
    {"op": "window", "match": "Sober", "act": "focus", "wait": 5000})]
check("and in Portuguese", all(x != y for x, y in zip(pt, labels))
      and pt[0].startswith("Vigiar") and "partidas" in pt[1], str(pt))
a.LANG = "en_US"

# ----------------------------------------- o farm montado, ponta a ponta --
# o jogo de mentira: clicar Jogar começa a partida, que acaba sozinha; clicar
# no botão do fim volta ao lobby. A tela muda com os cliques que o macro dá.
lobby_scene, over_scene = scene(1080, 1920), scene(1080, 1920)
play_img = save_png(lobby_scene[300:350, 800:960].copy(), "Play.png")
over_img = save_png(over_scene[700:740, 1200:1340].copy(), "Again.png")
play_at, over_at = (1920 + 880, 325), (1920 + 1270, 720)
phase_frames = {"lobby": [left_screen, a.Frame(lobby_scene, 1920, 0)],
                "match": blank,
                "over": [left_screen, a.Frame(over_scene, 1920, 0)]}


class FarmScreen(FakeScreen):
    MATCH_S = 0.6

    def __init__(self):
        super().__init__(None)
        self.phase, self.t, self.seen, self.matches = "lobby", 0.0, 0, 0
        self.stray = []

    def frames(self):
        self.calls += 1
        rec.poll()
        clicks = rec.clicks(ws)
        for x, y, _ts in clicks[self.seen:]:
            def near(p):
                return abs(x - p[0]) <= 2 and abs(y - p[1]) <= 2
            if self.phase == "lobby" and near(play_at):
                self.phase, self.t = "match", time.monotonic()
            elif self.phase == "over" and near(over_at):
                self.phase = "lobby"
                self.matches += 1
            else:
                self.stray.append((self.phase, x, y))
        self.seen = len(clicks)
        if self.phase == "match" and time.monotonic() - self.t >= \
                self.MATCH_S:
            self.phase = "over"
        return phase_frames[self.phase]


got.clear()
srv = http.server.HTTPServer(("127.0.0.1", 0), Hook)
threading.Thread(target=srv.serve_forever, daemon=True).start()
calls.clear()
farm = a.farm_macro({
    "window": "Sober", "lobby": [play_img], "lobby_wait": 5,
    "link": "https://www.roblox.com/games/3260590327/Tower-Defense-Sim",
    "strategy": [{"op": "tap", "code": K["X"]}, {"op": "wait", "ms": 3000},
                 {"op": "tap", "code": K["Y"]}],
    "over": over_img, "over_wait": 1, "limit": 2,
    "hook": f"http://127.0.0.1:{srv.server_port}/api/webhooks/1/abc",
    "report": "Match {run} done, farming for {time}."})
fw = FakeWindows(lambda n: {"found": n >= 3, "title": "Sober"})
game = FarmScreen()
r, took = run(farm["steps"], game, fw, timeout=25.0, mode="toggle",
              repeat=True, period_ms=0, limit=farm["limit"])
deadline = time.monotonic() + 3
while len(got) < 2 and time.monotonic() < deadline:
    time.sleep(0.05)
srv.shutdown()
game.frames()                 # o jogo vê o último clique, já sem o macro
reports = [json.loads(b)["content"] for b in got]
check("the farm opens the game when its window is not there",
      calls == [("open", "roblox://placeId=3260590327")], str(calls))
check("then plays two matches: lobby button, strategy, end button",
      game.matches == 2 and not game.stray and not r.is_alive()
      and r.count == 2, f"{game.matches} matches, stray {game.stray}, "
      f"{took:.1f} s")
check("the end of the match cuts the strategy short",
      len(downs(K["X"])) == 2 and not downs(K["Y"]))
check("each match is reported on the webhook",
      len(reports) == 2 and reports[0].startswith("Match 1 done, farming "
                                                  "for 0:0")
      and reports[1].startswith("Match 2 done"), str(reports))
check("and the game window was brought to the front every match",
      sum(1 for _m, focus in fw.asked if focus) == 2, str(fw.asked))

# ------------------------------------------------------------- gravador --
kb, ms = h.fake_keyboard(), h.fake_mouse()
keep = h.KeepMice()
hub.add(keep)
h.wait_grabbed(hub, [kb, ms])
ck = h.clone_of(hub, kb)
where = [(100, 200)]
recorder = a.MacroRecorder(hub, stop_code=K["F10"], cursor=lambda: where[0])
stopped = []
recorder.finished.connect(lambda: stopped.append(1))
recorder.start()
time.sleep(0.15)
kb.tap(K["A"], 0.03)
time.sleep(0.2)
ms.key(LEFT, True)
time.sleep(0.05)
ms.key(LEFT, False)
time.sleep(0.2)
for _i in range(3):
    ms.ev(2, 8, -1)
    time.sleep(0.03)
time.sleep(0.2)
kb.key(K["W"], True)
time.sleep(0.45)
kb.key(K["W"], False)
time.sleep(0.1)
ms.key(LEFT, True)                    # arrasto: aperta aqui, solta lá
time.sleep(0.3)
where[0] = (400, 500)
time.sleep(0.05)
ms.key(LEFT, False)
time.sleep(0.2)
kb.tap(K["F10"], 0.03)
pump(0.3)
events = recorder.stop()
steps = a.recorded_steps(events)
ops = [(st["op"], st.get("code"), st.get("x"), st.get("click"),
        st.get("dir"), st.get("times")) for st in steps if st["op"] != "wait"]
check("the stop key ends the recording", stopped == [1] and not
      recorder.active)
check("keys, a click with its position, the wheel and a drag are recorded",
      ops == [("tap", K["A"], None, None, None, 1),
              ("point", None, 100, "left", None, None),
              ("wheel", None, None, None, "down", 3),
              ("down", K["W"], None, None, None, None),
              ("up", K["W"], None, None, None, None),
              ("point", None, 100, "none", None, None),
              ("down", LEFT, None, None, None, None),
              ("point", None, 400, "none", None, None),
              ("up", LEFT, None, None, None, None)], "\n" + "\n".join(
                  map(str, ops)))
waits = [st["ms"] for st in steps if st["op"] == "wait"]
w_hold = steps[[i for i, st in enumerate(steps)
                if st["op"] == "down" and st["code"] == K["W"]][0] + 1]
check("with the pauses between them, in steps of 10 ms",
      len(waits) == 6 and all(w % 10 == 0 for w in waits)
      and w_hold["op"] == "wait" and 420 <= w_hold["ms"] <= 520,
      f"{waits}")
check("and nothing of it is the stop key",
      not [e for e in events if e[2] == K["F10"]])
check("without pauses there are no waits",
      not [st for st in a.recorded_steps(events, pauses=False)
           if st["op"] == "wait"])
ck.poll(0.05)
check("recording never swallows keys, only the stop key",
      ck.keys(K["A"], 1) and ck.keys(K["W"], 0) and not ck.keys(K["F10"]),
      f"{len(ck.keys())} key events relayed")
check("recorded_steps copes with odd input",
      a.recorded_steps([]) == []
      and a.recorded_steps([[0.1, "key", K["A"], 0, None]]) == []
      and a.recorded_steps([[0.1, "key", K["B"], 1, None]]) ==
      [{"op": "down", "code": K["B"], "name": "B"},
       {"op": "up", "code": K["B"], "name": "B"}])
no_pos = a.recorded_steps([[0.0, "key", LEFT, 1, None],
                           [0.05, "key", LEFT, 0, None]])
check("without KWin a click is recorded as a plain click",
      no_pos == [{"op": "tap", "code": LEFT, "times": 1}], str(no_pos))
hub.remove(keep)
kb.close()
ms.close()

engine.close()
ptr.close()
vk.close()
vm.close()
code = h.finish()
import shutil                                               # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(code)
