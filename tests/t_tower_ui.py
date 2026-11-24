"""A aba Tower Defense sem compositor: o modelo (estratégias, links e
arquivos), o gravador, o farm inteiro contra um Tower Defense de mentira
(lobby, dinheiro que sobe com o tempo, torres com painel, melhorias, pular
onda, vitória, derrota, reconectar, jogo fechado, pausa), a visão da Doca e
a aba na janela. A Doca é a classe de verdade com as peças de fora trocadas
por falsas, como em t_dock_ui.py: nenhum KWin sobe, nenhum device é criado,
nada sai para a rede e tudo vive num diretório temporário.

    env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen nice -n 19 \\
        dbus-run-session -- python3 -B tests/t_tower_ui.py
"""
import json, os, shutil, sys, tempfile, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-tower-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
RT = os.path.join(TMP, "rt")
os.makedirs(RT, mode=0o700)
os.environ["XDG_RUNTIME_DIR"] = RT       # a pasta da Doca nasce aqui
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
import numpy as np                                          # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt      # noqa: E402
from PySide6.QtGui import (QImage, QKeyEvent, QMouseEvent,  # noqa: E402
                           QWheelEvent)
from PySide6.QtWidgets import (QApplication, QDialog,       # noqa: E402
                               QMessageBox)

a.CFG = os.path.join(TMP, "wayclick.json")
with open(a.CFG, "w") as fh:
    json.dump({"sound": False, "language": "en_US"}, fh)
app = QApplication(sys.argv)
a.LANG = "en_US"
K, BTN = a.KEYS, a.BTN
LEFT, RIGHT = BTN["Left"], BTN["Right"]
rng = np.random.default_rng(20261121)
hooks = []
a.WEBHOOK = lambda url, text, shots: hooks.append((url, text, len(shots))) \
    or 204
opened = []
a.OPENER = lambda url: opened.append(url)
QMessageBox.information = lambda *x, **k: None
QMessageBox.warning = lambda *x, **k: None
ASK = {"answer": QMessageBox.Yes, "asked": []}
QMessageBox.question = lambda *x, **k: ASK["asked"].append(x[1:3]) \
    or ASK["answer"]


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def until(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        app.processEvents()
        time.sleep(0.01)
    return bool(cond())


# ------------------------------------------------- o jogo de mentira --
W, H = 640, 360
BG = rng.integers(0, 60, (H, W, 3)).astype(np.uint8)
NAMES = ("play", "wave", "panel", "sell", "skip", "victory", "defeat",
         "again", "reconnect", "never")
SPR = {n: rng.integers(0, 256, (24, 24, 3)).astype(np.uint8) for n in NAMES}
POS = {"play": (100, 300), "wave": (600, 20), "panel": (560, 200),
       "sell": (560, 240), "skip": (320, 40), "victory": (320, 150),
       "defeat": (320, 150), "again": (320, 250), "reconnect": (320, 180),
       "never": (40, 40)}
PIC = {}
for n, img in SPR.items():
    PIC[n] = os.path.join(TMP, n + ".png")
    with open(PIC[n], "wb") as fh:
        fh.write(a.png_bytes(img))
SPOT1, SPOT2 = (150, 200), (220, 200)


class FakeTDS:
    """Um Tower Defense que responde ao fake_input da Doca: lobby com
    PLAY, partida com dinheiro subindo (50 + 100 por segundo, a torre
    custa 100), 1 a 5 pega a torre, o clique coloca (e abre o painel), Q
    cancela e fecha o painel, E e Z melhoram a torre do painel, pular onda
    nos segundos ímpares, e no fim a vitória (ou derrota) com o botão de
    jogar de novo."""
    COST = 100.0

    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with self.lock:
            self.state, self.since = "lobby", time.monotonic()
            self.ptr = (0.0, 0.0)
            self.down = set()
            self.holding = self.panel = None
            self.towers, self.events = [], []
            self.spent = 0.0
            self.skipped = set()
            self.match_len = 5.0
            self.results, self.result = [], None
            self.reconnect_at = None
            self.zoom_s, self._zoom_t, self.drag = 0.0, None, 0.0
            self.played = 0

    def t(self):
        return time.monotonic() - self.since

    def go(self, state):
        with self.lock:
            self.state, self.since = state, time.monotonic()

    def _tick(self):
        st, t = self.state, self.t()
        if st == "loading" and t >= 0.3:
            self.go("match")
            self.towers, self.spent = [], 0.0
            self.panel = self.holding = None
            self.skipped = set()
            self.played += 1
            self.events.append(("start", self.played))
        elif st == "match":
            if self.reconnect_at is not None and t >= self.reconnect_at:
                self.reconnect_at = None
                self.go("disconnected")
                self.events.append(("disconnected",))
            elif t >= self.match_len:
                self.result = self.results.pop(0) if self.results \
                    else "victory"
                self.go("end")
                self.events.append(("end", self.result))

    def cash(self):
        return 50.0 + 100.0 * self.t() - self.spent

    def skip_shown(self):
        p = int(self.t())
        return self.state == "match" and p % 2 == 1 and p not in self.skipped

    def shown(self):
        st = self.state
        if st == "lobby":
            return ["play"]
        if st == "match":
            out = ["wave"]
            if self.panel is not None:
                out += ["panel", "sell"]
            if self.skip_shown():
                out.append("skip")
            return out
        if st == "end":
            return [self.result, "again"]
        if st == "disconnected":
            return ["reconnect"]
        return []

    @staticmethod
    def hit(name, x, y):
        cx, cy = POS[name]
        return abs(x - cx) <= 12 and abs(y - cy) <= 12

    def key(self, code, down):
        with self.lock:
            self._tick()
            if code == K["O"]:
                if down:
                    self._zoom_t = time.monotonic()
                elif self._zoom_t is not None:
                    self.zoom_s += time.monotonic() - self._zoom_t
                    self._zoom_t = None
            if not down:
                self.down.discard(code)
                return
            self.down.add(code)
            slots = [K[str(k + 1)] for k in range(5)]
            if self.state != "match" or code == K["O"]:
                return
            if code in slots:
                self.holding = slots.index(code)
            elif code == K["Q"]:
                self.holding = self.panel = None
            elif code in (K["E"], K["Z"]) and self.panel is not None:
                tw = self.towers[self.panel]
                path = "top" if code == K["E"] else "bottom"
                tw[3].append(path)
                self.events.append(("upgrade", tw[0], tw[1], tw[2], path))
            else:
                self.events.append(("key", code))

    def button(self, code, down):
        with self.lock:
            self._tick()
            if not down:
                self.down.discard(code)
                return
            self.down.add(code)
            if code == LEFT:
                self._click(int(self.ptr[0]), int(self.ptr[1]))

    def _click(self, x, y):
        st, t = self.state, self.t()
        if st == "lobby" and self.hit("play", x, y):
            self.events.append(("play",))
            self.go("loading")
        elif st == "end" and self.hit("again", x, y):
            self.events.append(("again",))
            self.go("lobby")
        elif st == "disconnected" and self.hit("reconnect", x, y):
            self.events.append(("reconnect",))
            self.go("lobby")
        elif st == "match":
            if self.skip_shown() and self.hit("skip", x, y):
                self.skipped.add(int(t))
                self.events.append(("skip", int(t)))
            elif self.panel is not None and self.hit("sell", x, y):
                tw = self.towers.pop(self.panel)
                self.events.append(("sell", tw[0], tw[1], tw[2]))
                self.panel = None
            elif self.holding is not None:
                if self.cash() >= self.COST:
                    self.spent += self.COST
                    self.towers.append([self.holding, x, y, []])
                    self.panel = len(self.towers) - 1
                    self.events.append(("place", self.holding, x, y,
                                        round(t, 2)))
                    self.holding = None
                else:
                    self.events.append(("broke", self.holding, round(t, 2)))
            else:
                near = [i for i, tw in enumerate(self.towers)
                        if abs(tw[1] - x) <= 12 and abs(tw[2] - y) <= 12]
                self.panel = near[0] if near else None

    def move(self, dx, dy):
        with self.lock:
            if RIGHT in self.down:
                self.drag += dy

    def absolute(self, x, y):
        with self.lock:
            self.ptr = (x, y)

    def frame(self):
        with self.lock:
            self._tick()
            img = BG.copy()
            for n in self.shown():
                cx, cy = POS[n]
                img[cy - 12:cy + 12, cx - 12:cx + 12] = SPR[n]
        return a.Frame(img, 0, 0, W, H)

    def got(self, kind):
        with self.lock:
            return [e for e in self.events if e[0] == kind]


class GameInput:
    """O fake_input da Doca, ligado ao jogo: anota e repassa."""

    def __init__(self, game):
        self.g, self.log, self.fail = game, [], False

    def _put(self, *ev):
        if self.fail:
            raise BrokenPipeError("the Dock is closed")
        self.log.append(ev)

    def key(self, code, down):
        self._put("key", code, bool(down))
        self.g.key(code, down)

    def button(self, code, down):
        self._put("button", code, bool(down))
        self.g.button(code, down)

    def axis(self, axis, value):
        self._put("axis", axis, value)

    def move(self, dx, dy):
        self._put("move", dx, dy)
        self.g.move(dx, dy)

    def absolute(self, x, y):
        self._put("abs", x, y)
        self.g.absolute(x, y)


class GameScreen:
    def __init__(self, game):
        self.g, self.calls, self.users = game, 0, 0
        self.state, self.error = "on", ""

    def ensure(self, timeout=None):
        return True

    def start(self):
        pass

    def acquire(self):
        self.users += 1

    def release(self):
        self.users -= 1

    def stop(self, join=True):
        pass

    def frames(self):
        self.calls += 1
        return [self.g.frame()]

    def qimage(self):
        rgb = np.ascontiguousarray(self.g.frame().rgb)
        return QImage(rgb.data, W, H, 3 * W, QImage.Format_RGB888).copy()


class GameWindows:
    def __init__(self):
        self.found, self.asked, self.ok, self.error = True, [], True, None

    def find(self, match, focus=False):
        self.asked.append(match)
        return {"found": self.found, "title": "Roblox", "cls": "sober",
                "active": True}

    def cursor(self):
        return None

    def close(self):
        self.ok = False


class FakeLink:
    def __init__(self):
        self.closed, self.on_close = False, None

    def close(self):
        self.closed = True


class FakeProc:
    def __init__(self, pid=4242424):
        self.pid, self.code = pid, None

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15

    def kill(self):
        self.code = -9

    def wait(self, timeout=None):
        return self.code


game = FakeTDS()
dock = a.DOCK()
runs = []
LINK = ("https://www.roblox.com/games/3260590327/Tower-Defense-Simulator"
        "?privateServerLinkCode=abc123")
DEEP = "roblox://placeId=3260590327&linkCode=abc123"


def game_opens(argv):
    runs.append(argv)
    dock.windows.found = True
    game.go("lobby")


def fake_open():
    dock.state = "on"
    dock.kwin, dock.link = FakeProc(), FakeLink()
    dock.input = GameInput(game)
    dock.pointer = a.DockPointer(dock.input)
    dock.windows = GameWindows()
    dock.screen = GameScreen(game)
    dock.out_size = (W, H)
    dock._run = game_opens
    dock.sober_outside = lambda argv: False
    return dock.input


a.scheme_handler = lambda url: ["sober", url]
dock.hide = lambda: {"ok": True}
dock.show = lambda: {"ok": True}

# --------------------------------------------------------- o modelo --
check("a game page becomes a roblox:// link, and a private server keeps "
      "its link code", [a.farm_link(x) for x in (
          LINK, "https://www.roblox.com/games/3260590327/x?a=1"
          "&privateServerLinkCode=Zz_9-1#top",
          "https://www.roblox.com/games/3260590327/Tower-Defense-Simulator",
          "https://www.roblox.com/share?code=abc&type=Server")] == [
          DEEP, "roblox://placeId=3260590327&linkCode=Zz_9-1",
          "roblox://placeId=3260590327",
          "https://www.roblox.com/share?code=abc&type=Server"])
check("the tab only takes links that open in the Dock",
      a.tower_link(LINK) == DEEP and a.tower_link("roblox://placeId=5")
      == "roblox://placeId=5" and a.tower_link("https://example.com") == ""
      and a.tower_link("https://www.roblox.com/share?code=abc&type=Server")
      == "" and a.tower_link("") == "")
raw = {"name": "  My   strat ", "size": [W, H, 3], "towers": [
    {"name": "Scout", "points": [[10, 20], "bad", [-1, 5], [30, 40]]},
    {"points": [[1, 2]]}, "junk"],
    "actions": [
        {"t": 5, "do": "upgrade", "tower": 0, "point": 3, "path": "x"},
        {"t": 1, "do": "place", "tower": 0, "point": 0},
        {"t": 2, "do": "place", "tower": 0, "point": 1},
        {"t": 3, "do": "place", "tower": 4, "point": 0},
        {"t": 4, "do": "key", "code": LEFT},
        {"t": 4.5, "do": "key", "code": K["F"]},
        {"t": 6, "do": "image", "path": "", "click": True},
        {"t": 7, "do": "image", "path": "/x.png", "click": False,
         "wait": 99999},
        {"t": 8, "do": "fly"}],
    "lobby": ["/a.png", 5, ""]}
s = a.normalize_tower_strategy(raw)
check("a strategy keeps 5 towers, the valid spots, and the actions that "
      "point at them (remapped when a spot before was dropped)",
      [len(t["points"]) for t in s["towers"]] == [2, 1, 0, 0, 0]
      and s["towers"][0]["points"] == [[10, 20], [30, 40]]
      and s["name"] == "My strat" and s["size"] is None
      and [(x["t"], x["do"]) for x in s["actions"]] == [
          (1.0, "place"), (4.5, "key"), (5.0, "upgrade"), (7.0, "image")]
      and s["actions"][2] == {"t": 5.0, "do": "upgrade", "tower": 0,
                              "point": 1, "path": "top"}
      and s["actions"][3]["wait"] == 3600.0
      and s["actions"][3]["click"] is False and s["lobby"] == ["/a.png"],
      json.dumps(s))
c = a.normalize_tower({"strategies": [s, dict(s)], "current": "nope",
                       "keys": {"sell": 0, "top": LEFT}, "speed": "warp",
                       "drag": 99999, "retry": 0, "screens": {
                           "victory": "/v.png", "after": ["/a.png", 3]}})
check("the whole tab config falls back to sane values",
      len(c["strategies"]) == 2 and c["strategies"][0]["id"]
      != c["strategies"][1]["id"] and c["current"] == s["id"]
      and c["keys"] == {"top": K["E"], "bottom": K["Z"], "sell": 0,
                        "cancel": K["Q"], "zoom": K["O"]}
      and c["hotkeys"] == {"start": K["F4"], "stop": K["F5"],
                           "pause": K["F6"]}
      and c["speed"] == "balanced" and c["drag"] == 3000
      and c["retry"] == 0.2 and c["window"] == "Sober"
      and c["screens"]["victory"] == "/v.png"
      and c["screens"]["after"] == ["/a.png"] and c["camera"] is True,
      json.dumps(c))
st = a.normalize_tower_strategy({"towers": [{"name": "Scout", "points": [
    [1, 1], [2, 2], [3, 3]]}], "actions": [
    {"t": 1, "do": "place", "tower": 0, "point": 0},
    {"t": 2, "do": "place", "tower": 0, "point": 1},
    {"t": 3, "do": "place", "tower": 0, "point": 2},
    {"t": 4, "do": "upgrade", "tower": 0, "point": 2, "path": "bottom"}]})
check("the timeline reads well",
      [a.tower_action_text(x, st) for x in st["actions"][2:]] == [
          "Place 1 Scout at spot 3",
          "Upgrade 1 Scout at spot 3, bottom path"]
      and a.tower_name(st, 1) == "tower 2" and a.tower_time(62.5) == "1:02.5"
      and a.tower_action_text({"t": 0, "do": "key", "code": K["F"],
                               "name": "F"}, st) == "Press F")
check("removing a spot takes its actions and shifts the ones after",
      len(a.tower_uses(st, 0)) == 4 and len(a.tower_uses(st, 0, 1)) == 1)
a.tower_drop_point(st, 0, 1)
check("…and it does", st["towers"][0]["points"] == [[1, 1], [3, 3]]
      and [(x["t"], x.get("point")) for x in st["actions"]]
      == [(1.0, 0), (3.0, 1), (4.0, 1)])

# -------------------------------------------------------- o gravador --
clock = [100.0]
rs = a.normalize_tower_strategy({"towers": [{"points": [[50, 50]]}]})
rec = a.TowerRecorder(rs, a.normalize_tower({})["keys"],
                      clock=lambda: clock[0])
clock[0] = 101.5
check("a click with no tower in hand is not an action",
      rec.click(300, 300) is None and rec.selected is None)
check("1 picks the tower, and the click places it on a spot it already "
      "had (8 px)", rec.key(K["1"]) is None
      and rec.click(55, 53) == {"t": 1.5, "do": "place", "tower": 0,
                                "point": 0}
      and rs["towers"][0]["points"] == [[50, 50]])
clock[0] = 102.0
check("E upgrades what was just placed",
      rec.key(K["E"]) == {"t": 2.0, "do": "upgrade", "tower": 0,
                          "point": 0, "path": "top"})
clock[0] = 103.0
rec.key(K["2"])
p2 = rec.click(200, 100)
check("a new spot joins the tower", p2 == {"t": 3.0, "do": "place",
                                           "tower": 1, "point": 0}
      and rs["towers"][1]["points"] == [[200, 100]])
rec.key(K["3"])
rec.key(K["Q"])
check("Q drops the tower in hand", rec.click(400, 300) is None
      and rec.pending is None)
check("clicking near a tower (24 px) selects it, and Z upgrades it",
      rec.click(215, 110) is None and rec.selected == (1, 0)
      and rec.key(K["Z"])["path"] == "bottom")
check("with no sell key, the sell key does nothing",
      rec.key(K["X"]) is None)
undone = rec.undo()
check("undo takes the last action", undone["do"] == "upgrade"
      and len(rs["actions"]) == 3)
rec.undo()
check("and the spot it created goes with it",
      rs["towers"][1]["points"] == [] and len(rs["actions"]) == 2)

# ---------------------------------------------------- arquivos --
data, missing = a.export_tower(
    {"name": "Share", "towers": [{"name": "Scout", "points": [[5, 5]]}],
     "actions": [{"t": 1, "do": "place", "tower": 0, "point": 0},
                 {"t": 2, "do": "image", "path": PIC["skip"]},
                 {"t": 3, "do": "image", "path": "/gone.png"}],
     "lobby": [PIC["play"], PIC["play"]], "lobby_macro": "abc"},
    {"victory": PIC["victory"], "after": [PIC["again"]],
     "skip": "/gone.png"})
check("a strategy file carries its pictures once each, and drops the "
      "lobby macro", data["kind"] == "tower"
      and sorted(data["images"]) == ["again.png", "play.png", "skip.png",
                                     "victory.png"]
      and data["tower"]["strategy"]["lobby"] == ["play.png", "play.png"]
      and data["tower"]["strategy"]["lobby_macro"] == ""
      and len(data["tower"]["strategy"]["actions"]) == 2
      and missing == ["/gone.png"], json.dumps(data)[:400])
check("the Hotkeys import does not take it as macros", (lambda: (
    a.import_macros(data, dry=True)))() if False else True)
try:
    a.import_macros(data, dry=True)
    bad = False
except ValueError:
    bad = True
check("…it says it is not a macro file", bad)
mine = {k: "" for k in a.TOWER_SCREENS}
mine["after"] = []
mine["victory"] = "/mine.png"
got, fill, notes = a.import_tower(json.loads(json.dumps(data)), mine)
check("importing saves the pictures and only fills empty screens",
      got["name"] == "Share" and got["id"] != data["tower"]["strategy"]["id"]
      and os.path.isfile(got["lobby"][0])
      and got["lobby"][0].startswith(a.images_dir())
      and list(fill) == ["after"] and os.path.isfile(fill["after"][0])
      and any("kept" in n for n in notes), str((got, fill, notes)))
got2, _f, _n = a.import_tower(json.loads(json.dumps(data)), mine)
check("the same picture is not copied twice",
      got2["lobby"][0] == got["lobby"][0])
try:
    a.import_tower({"wayclick": 1, "macros": []}, mine)
    bad = False
except ValueError:
    bad = True
check("a macro file is not a strategy", bad)

# --------------------------------------------------------- o farm --
fake_open()
SCREENS = {"start": PIC["wave"], "placed": PIC["panel"], "skip": PIC["skip"],
           "victory": PIC["victory"], "defeat": PIC["defeat"],
           "reconnect": PIC["reconnect"], "sell": "",
           "after": [PIC["again"]]}
S1 = a.normalize_tower_strategy({
    "name": "Test", "size": [W, H],
    "towers": [{"name": "Scout", "points": [list(SPOT1)]},
               {"name": "Sniper", "points": [list(SPOT2)]}],
    "actions": [
        {"t": 0.8, "do": "place", "tower": 0, "point": 0},
        {"t": 1.0, "do": "place", "tower": 1, "point": 0},
        {"t": 3.0, "do": "upgrade", "tower": 0, "point": 0, "path": "top"},
        {"t": 3.2, "do": "upgrade", "tower": 1, "point": 0,
         "path": "bottom"},
        {"t": 3.4, "do": "key", "code": K["F"], "name": "F"}],
    "lobby": [PIC["play"]]})


def make_plan(strategy, **over):
    c = a.normalize_tower({
        "strategies": [strategy], "screens": SCREENS, "speed": "fast",
        "zoom_ms": 300, "drag": 120, "retry": 0.3, "link": LINK,
        "hook": "https://discord.com/api/webhooks/1/x", "shot": False,
        "lobby_wait": 5, "start_wait": 5})
    plan = json.loads(json.dumps(c))
    plan["strategy"] = plan["strategies"][0]
    plan["lobby_steps"] = []
    plan.update(over)
    return plan


def farm(plan, seconds, stop_when=None, during=None):
    env = a.TowerEnv(dock)
    done = []
    r = a.TowerRunner(plan, dock, env, on_done=done.append)
    r.start()
    end = time.monotonic() + seconds
    lines = []
    while time.monotonic() < end and r.is_alive():
        if during:
            during(r)
        if stop_when and stop_when(r):
            r.stop()
        lines += env.take()
        time.sleep(0.02)
    if r.is_alive():
        r.stop()
    r.join(3.0)
    lines += env.take()
    return r, [x[2] for x in lines], [x[2] for x in lines if x[1]], done


game.reset()
game.results = ["victory", "defeat"]
t_start = time.monotonic()
r, log, warns, done = farm(make_plan(S1, limit=2), 60)
took = time.monotonic() - t_start
check("the farm plays 2 matches and stops, as set", not r.is_alive()
      and done == [r] and r.counters == {"matches": 2, "wins": 1,
                                         "losses": 1}, f"{r.counters} "
      f"{took:.1f}s\n" + "\n".join(log[-12:]))
check("it clicked PLAY in the lobby and Play again at the end, twice",
      len(game.got("play")) == 2 and len(game.got("again")) == 2,
      str(game.events))
places = game.got("place")
check("each tower went down on its spot, in order",
      [(e[1], e[2], e[3]) for e in places] == [(0,) + SPOT1, (1,) + SPOT2]
      * 2, str(places))
check("the first one at its time in the match",
      all(e[4] >= 0.8 for e in places[0::2]), str(places))
check("the second one waited for the cash, trying again",
      game.got("broke") and any("maybe not enough cash" in x for x in log)
      and all(e[4] >= 1.5 for e in places[1::2]), str(game.events))
check("each tower got its upgrade, on the right path",
      [(e[1], e[4]) for e in game.got("upgrade")]
      == [(0, "top"), (1, "bottom")] * 2, str(game.got("upgrade")))
check("the key of the timeline was pressed",
      game.got("key") == [("key", K["F"])] * 2, str(game.got("key")))
check("Skip wave was clicked when it showed up", len(game.got("skip")) >= 2,
      str(game.got("skip")))
check("the camera was lined up each match: zoom held, dragged down",
      0.5 <= game.zoom_s <= 1.5 and game.drag == 240,
      f"{game.zoom_s:.2f} s {game.drag}")
check("nothing is left pressed", not game.down, str(game.down))
check("the webhook got one report per match",
      [x[1].split(", farming")[0] for x in hooks] == [
          "Match 1: victory. 1 wins, 0 losses",
          "Match 2: defeat. 1 wins, 1 losses"]
      and hooks[0][0] == "https://discord.com/api/webhooks/1/x", str(hooks))
check("the log tells the story",
      log[0] == "Farm started with Test." and "Match 1 started." in log
      and any(x.startswith("Match 1: victory, ") for x in log)
      and any(x.startswith("Match 2: defeat, ") for x in log)
      and log[-1] == "Done: 2 matches, as set in Settings."
      and not warns, "\n".join(log) + "\nWARN: " + "\n".join(warns))

# reconectar, e o jogo fechado no meio
game.reset()
game.reconnect_at = 1.0
hooks.clear()


def close_game(r):
    if game.played == 2 and game.state == "match" and game.t() > 0.5 \
            and dock.windows.found and not runs:
        dock.windows.found = False


r, log, warns, done = farm(make_plan(S1, limit=1, hook=""), 60,
                           during=close_game)
check("a disconnect is clicked away and the farm starts over",
      game.got("disconnected") and game.got("reconnect")
      and "Disconnected: clicked Reconnect, starting over." in log,
      "\n".join(log))
check("the game closed: it opens again by the link, in the Dock",
      "The game window closed." in log and runs == [["sober", DEEP]]
      and "The game is open." in log, "\n".join(log) + str(runs))
check("and the farm goes on to finish its match",
      r.counters["matches"] == 1 and r.counters["wins"] == 1
      and not hooks and not warns, "\n".join(log + warns))

# pausa: o relógio da partida para junto
game.reset()
S2 = a.normalize_tower_strategy({
    "name": "Pause", "towers": [{"points": [list(SPOT1)]}],
    "actions": [{"t": 1.0, "do": "place", "tower": 0, "point": 0}],
    "lobby": [PIC["play"]]})
phases = []


def pause_once(r):
    if r.t0 is not None and not phases and \
            time.perf_counter() - r.t0 > 0.3:
        r.paused = True
        phases.append(time.monotonic())
    elif phases and r.paused and time.monotonic() - phases[0] > 1.2:
        phases.append(r.phase)
        r.paused = False


r, log, warns, done = farm(make_plan(S2, camera=False, hook=""), 20,
                           stop_when=lambda r: game.got("place"),
                           during=pause_once)
pl = game.got("place")
check("a pause holds the clock of the match", pl and pl[0][4] >= 2.1
      and phases[1:] == ["Paused"] and "Paused." in log
      and "Resumed." in log, f"{pl} {phases} {log}")
check("Stop ends it right away, with nothing pressed",
      not r.is_alive() and done == [r] and not game.down)

# juntando dinheiro para uma torre, o pular onda ainda é clicado
game.reset()
game.COST = 250.0                       # só dá depois de 2 s de partida
S3 = a.normalize_tower_strategy({
    "name": "Cash", "towers": [{"points": [list(SPOT1)]}],
    "actions": [{"t": 0.2, "do": "place", "tower": 0, "point": 0}],
    "lobby": [PIC["play"]]})
r, log, warns, done = farm(make_plan(S3, camera=False, hook=""), 20,
                           stop_when=lambda r: game.got("place"))
game.COST = FakeTDS.COST
check("waiting for the cash of a tower, Skip wave is still clicked",
      game.got("place") and game.got("place")[0][4] >= 2.0
      and ("skip", 1) in game.got("skip") and not game.down,
      f"{game.events}")

# nada de partida: para depois de 5 rodadas
game.reset()
r, log, warns, done = farm(make_plan(S2, camera=False, hook="",
                                     start_wait=1, lobby_wait=1, screens=dict(
                                         SCREENS, start=PIC["never"])), 40)
check("rounds with no match stop the farm, saying the pictures may not "
      "match", not r.is_alive() and r.counters["matches"] == 0
      and any(x.startswith("5 rounds in a row without a match") for x in
              log), "\n".join(log))

# sem o jogo e sem link
game.reset()
dock.windows.found = False
r, log, warns, done = farm(make_plan(S2, link=""), 10)
check("with no game and no link it stops and says what to do",
      not r.is_alive() and any("put the game link in Settings" in x
                               for x in log), "\n".join(log))
dock.windows.found = True

# a Doca fecha no meio
game.reset()


def kill_dock(r):
    if r.t0 is not None and not dock.input.fail:
        dock.input.fail = True
        dock.link.closed = True


r, log, warns, done = farm(make_plan(S1, camera=False, hook=""), 20,
                           during=kill_dock)
check("the Dock closing stops the farm quietly", not r.is_alive()
      and done == [r], "\n".join(log + warns))
fake_open()

# --------------------------------------------------- a visão da Doca --
view = a.DockView(dock)
view.resize(2 * W, 2 * H)
view.image = QImage(W, H, QImage.Format_RGB32)
view.image.fill(0)
acted, picked = [], []
view.acted.connect(lambda *x: acted.append(x))
view.picked.connect(lambda *x: picked.append(x))
inp = dock.input
inp.log.clear()


def mouse(kind, x, y, button=Qt.LeftButton, buttons=None):
    t = {"press": QEvent.MouseButtonPress, "release":
         QEvent.MouseButtonRelease, "move": QEvent.MouseMove}[kind]
    ev = QMouseEvent(t, QPointF(x, y), QPointF(x, y),
                     button if kind != "move" else Qt.NoButton,
                     buttons if buttons is not None else (
                         button if kind == "press" else Qt.NoButton),
                     Qt.NoModifier)
    QApplication.sendEvent(view, ev)


def key(code, down=True, auto=False):
    ev = QKeyEvent(QEvent.KeyPress if down else QEvent.KeyRelease, 0,
                   Qt.NoModifier, code + 8, 0, 0, "", auto)
    QApplication.sendEvent(view, ev)


check("view points map to the Dock (2× here), the letterbox does not",
      view.to_dock(QPointF(201, 101)) == (100.5, 50.5)
      and view.to_dock(QPointF(2 * W + 5, 10)) is None)
mouse("move", 201, 101)
mouse("press", 201, 101)
mouse("release", 201, 101)
check("moving and clicking over the view goes to the game",
      inp.log == [("abs", 100.5, 50.5), ("abs", 100.5, 50.5),
                  ("button", LEFT, True), ("button", LEFT, False)]
      and acted == [("click", LEFT, 100, 50)], str(inp.log) + str(acted))
inp.log.clear()
mouse("press", 201, 101, Qt.RightButton)
mouse("move", 221, 121, buttons=Qt.RightButton)
mouse("release", 221, 121, Qt.RightButton)
check("with the right button held the motion is relative (camera)",
      ("move", 10.0, 10.0) in inp.log and inp.log[-1] == ("abs", 110.5,
                                                           60.5),
      str(inp.log))
inp.log.clear()
QApplication.sendEvent(view, QWheelEvent(
    QPointF(201, 101), QPointF(201, 101), QPoint(0, 0), QPoint(0, 120),
    Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
check("the wheel goes too, one notch up",
      inp.log == [("axis", 0, -a.DockRunner.WHEEL_STEP)], str(inp.log))
inp.log.clear()
view.skip_keys = {K["F4"]}
key(K["1"])
key(K["1"], auto=True)
key(K["F4"])
key(K["1"], False)
check("keys go to the game (no auto repeat), farm hotkeys do not",
      inp.log == [("key", K["1"], True), ("key", K["1"], False)]
      and acted[-2:] == [("key", K["1"], 0, 0), ("hotkey", K["F4"], 0, 0)],
      str(inp.log) + str(acted))
inp.log.clear()
key(K["W"])
mouse("press", 201, 101)
view.release_all()
check("losing focus lets go of everything held",
      inp.log[-2:] == [("key", K["W"], False), ("button", LEFT, False)],
      str(inp.log))
inp.log.clear()
view.pick = True
mouse("press", 301, 201)
mouse("press", 301, 201, Qt.RightButton)
check("in pick mode a click is a spot, and nothing goes to the game",
      picked == [(150, 100, LEFT), (150, 100, RIGHT)] and inp.log == [],
      str(picked) + str(inp.log))
view.pick = False
view.image = QImage()
view.start()
check("the view captures the Dock", until(
    lambda: view.image.width() == W and view.image.height() == H, 3.0))
view.stop()
check("and stops", not view.running())

# ------------------------------------------------------- a aba --
dock.state = "off"
dock.changed.emit()
tab = a.TowerTab({}, dock, macros=lambda: [a.normalize_macro(
    {"name": "Walk in", "steps": [{"op": "tap", "code": K["W"]}],
     "id": "lobbymac"})])
saves = []
tab.changed.connect(lambda: saves.append(1))
check("the tab starts with one strategy, ready for spots",
      tab.strat.count() == 1 and tab.strat.currentText() == "Strategy 1"
      and tab.name.text() == "Strategy 1")
tab._pick(0)
check("spots are picked on the game: with the Dock closed it says so",
      tab.picking is None and "Open the Dock first" in tab.error.text())
fake_open()
game.reset()
dock.changed.emit()
check("with the Dock open the farm hotkeys are on",
      sorted(w.keycode for w in tab.watchers.values())
      == sorted([K["F4"], K["F5"], K["F6"]]))
tab.set_claims(lambda code: code == K["F6"])
check("…and they yield to a macro on the same key",
      tab.watchers["pause"].shadow(K["F6"]))
tab._pick(0)
tab._picked(*SPOT1, LEFT)
tab._picked(400, 300, LEFT)
s = tab._strategy()
check("Pick adds spots to the tower and shows them on the view",
      s["towers"][0]["points"] == [list(SPOT1), [400, 300]]
      and tab.tower_rows[0][2].text() == "2 set ✓" and tab.view.pick
      and (SPOT1[0], SPOT1[1], "1.1", a.TOWER_COLORS[0]) in tab.view.markers
      and s["size"] == [W, H] and saves)
tab._picked(405, 305, RIGHT)
check("a right click removes the spot", s["towers"][0]["points"]
      == [list(SPOT1)])
tab._pick(None)
check("Esc or Pick again ends it", not tab.view.pick
      and not tab.tower_rows[0][3].isChecked())
tab._pick(1)
tab._picked(*SPOT2, LEFT)
tab._pick(None)

# gravar: a câmera primeiro, depois jogando pela visão
tab.zoom_ms.setValue(200)
tab.drag.setValue(60)
game.go("match")
game.since -= 10.0                      # dinheiro de sobra
tab.b_rec.setChecked(True)
tab._record(True)
check("recording lines up the camera first",
      until(lambda: tab.rec is not None, 5.0) and game.drag == 60
      and game.zoom_s >= 0.15, f"{game.drag} {game.zoom_s}")
tab.view.acted.emit("key", K["1"], 0, 0)
tab.view.acted.emit("click", LEFT, SPOT1[0] + 3, SPOT1[1] - 2)
tab.view.acted.emit("key", K["E"], 0, 0)
tab.view.acted.emit("key", K["2"], 0, 0)
tab.view.acted.emit("click", LEFT, *SPOT2)
tab.view.acted.emit("key", K["Z"], 0, 0)
tab.view.acted.emit("hotkey", K["F10"], 0, 0)
check("playing records the actions, F10 undoes the last",
      [(x["do"], x.get("tower"), x.get("path")) for x in s["actions"]] == [
          ("place", 0, None), ("upgrade", 0, "top"), ("place", 1, None)]
      and tab.timeline.count() == 3 and "Recording" in tab.view_note.text())
check("while recording, the farm and the timeline buttons wait",
      not tab.b_start.isEnabled() and not tab.b_add.isEnabled()
      and tab.b_undo.isEnabled() and K["F10"] in tab.view.skip_keys)
tab._record(False)
check("Stop recording puts it in order",
      tab.rec is None and not tab.b_rec.isChecked()
      and tab.timeline_box.title() == "Timeline (3)"
      and K["F10"] not in tab.view.skip_keys)
tab._ask_replace = lambda n: "add"
tab.b_rec.setChecked(True)
tab._record(True)
until(lambda: tab.rec is not None, 5.0)
tab.view.acted.emit("key", K["1"], 0, 0)
tab.view.acted.emit("click", LEFT, *SPOT1)
tab._record(False)
check("recording again can add to the timeline", len(s["actions"]) == 4)
tab._ask_replace = lambda n: None
tab.b_rec.setChecked(True)
tab._record(True)
check("…or be cancelled", tab.rec is None and tab.cam is None
      and not tab.b_rec.isChecked() and len(s["actions"]) == 4)

# a linha do tempo à mão
up = next(x for x in s["actions"] if x["do"] == "upgrade")
dlg = a.TowerActionDialog(None, s, up)
check("the action dialog opens on the action (the path of an upgrade is "
      "not a picture)", dlg.kind.currentData() == "top"
      and dlg.tower.currentData() == 0 and dlg.t.value() == up["t"]
      and dlg.image.path == "", f"{dlg.kind.currentData()} {dlg.t.value()} "
      f"{up} {dlg.image.path!r}")
dlg.kind.setCurrentIndex(dlg.kind.findData("key"))
dlg.key.set_key(K["F"], "F")
dlg.t.setValue(9.5)
check("and gives back what was chosen", dlg.action() == {
    "t": 9.5, "do": "key", "code": K["F"], "name": "F"})
dlg.kind.setCurrentIndex(dlg.kind.findData("click"))
dlg._ok()
check("a picture action needs the picture", dlg.result() != QDialog.Accepted
      and dlg.error.text() == "Capture the picture first.")
tab._put(dlg.action() or {"t": 9.5, "do": "key", "code": K["F"],
                          "name": "F"})
check("an action goes in by its time", s["actions"][-1]["t"] == 9.5
      and tab.timeline.currentRow() == len(s["actions"]) - 1)
tab.timeline.setCurrentRow(len(s["actions"]) - 1)
tab._remove()
check("and comes out", len(s["actions"]) == 4)
ASK["answer"] = QMessageBox.Yes
tab._clear_tower(1)
check("clearing a tower takes its actions",
      s["towers"][1]["points"] == [] and all(
          x.get("tower") != 1 for x in s["actions"]))

# as telas, os ajustes e o farm pela aba
tab.screen_fields["start"].set_path(PIC["wave"])
tab.screen_fields["placed"].set_path(PIC["panel"])
tab.screen_fields["skip"].set_path(PIC["skip"])
tab.after_fields[0].set_path(PIC["again"])
check("the screens go to the config",
      tab.c["screens"]["start"] == PIC["wave"]
      and tab.c["screens"]["after"] == [PIC["again"]])
tab.limit.setValue(1)
tab.speed.setCurrentIndex(tab.speed.findData("fast"))
tab.retry.setValue(0.3)
tab.link.setText("https://example.com/game")
tab.link.textEdited.emit("https://example.com/game")
check("a link that would open outside the Dock is flagged",
      not tab.link_note.isHidden() and "outside the Dock"
      in tab.link_note.text())
tab.start()
check("…and Start refuses it", tab.runner is None
      and "outside the Dock" in tab.error.text())
tab.link.setText(LINK)
tab.link.textEdited.emit(LINK)
check("a private server link is fine", tab.link_note.isHidden()
      and tab.b_join.isEnabled())
s["lobby"] = [PIC["play"]]
s["lobby_macro"] = "lobbymac"
tab._fill_lobby()
tab._fill_lobby_macro()
check("the lobby shows its buttons and macro",
      len(tab.lobby_fields) == 1 and tab.lobby_macro.currentData()
      == "lobbymac")
plan = tab._plan()
check("the plan carries the strategy and the lobby macro steps",
      plan["strategy"]["id"] == s["id"] and plan["lobby_steps"]
      and plan["lobby_steps"][0]["code"] == K["W"] and plan["limit"] == 1)
s["lobby_macro"] = ""
tab._fill_lobby_macro()
tab.screen_fields["victory"].set_path("")
tab.after_fields[0].set_path("")
tab.start()
check("Start wants a way to see the end of a match",
      tab.runner is None and "how a match ends" in tab.error.text())
tab.after_fields[0].set_path(PIC["again"])
tab.screen_fields["victory"].set_path(PIC["victory"])
game.reset()
tab.watchers["start"].pressed.emit()
check("F4 starts the farm", tab.runner is not None and tab.running()
      and not tab.b_start.isEnabled() and tab.b_stop.isEnabled())
tab.pause()
pump(0.4)
check("F6 pauses", tab.runner.paused and tab.b_pause.text() == "Resume"
      and tab.phase.text() == "Paused")
tab.pause()
check("and resumes", until(lambda: tab.runner is None, 30.0)
      and tab.stats["matches"].text() == "1"
      and tab.stats["wins"].text() == "1"
      and tab.phase.text() == "Stopped"
      and "Farm stopped." in tab.log.toPlainText()
      and "Match 1: victory" in tab.log.toPlainText(),
      tab.log.toPlainText()[-600:])
game.reset()
tab.start()
pump(0.3)
tab.watchers["stop"].pressed.emit()
check("F5 stops it", until(lambda: tab.runner is None, 3.0))

# arquivo da estratégia, de ida e volta
out = os.path.join(TMP, "share.wayclick")
data, missing = a.export_tower(s, tab.c["screens"])
with open(out, "w") as fh:
    json.dump(data, fh)
n0 = len(tab.c["strategies"])
got = tab.import_path(out)
check("a strategy file comes back as a new strategy",
      got is not None and len(tab.c["strategies"]) == n0 + 1
      and tab.strat.currentData() == got["id"]
      and "Imported" in tab.log.toPlainText())
mac = os.path.join(TMP, "macros.wayclick")
with open(mac, "w") as fh:
    json.dump({"wayclick": 1, "macros": []}, fh)
check("a macros file is pointed to the Hotkeys tab",
      tab.import_path(mac) is None and "Hotkeys tab" in tab.error.text())
dock.out_size = (1280, 720)
tab._size_note()
check("a strategy made at another Dock size warns",
      not tab.size_note.isHidden() and "1280×720" in tab.size_note.text())
dock.out_size = (W, H)
tab._size_note()
a.LANG = "pt_BR"
tab.retranslate()
check("the tab is translated", tab.side.tabText(1) == "Telas"
      and tab.b_rec.text() == "● Gravar"
      and tab.b_start.text() == "Iniciar (F4)"
      and tab.screen_fields["victory"].b_cap.text() == "Capturar…")
a.LANG = "en_US"
tab.retranslate()
dock.state = "off"
dock.changed.emit()
check("the Dock closing turns the hotkeys off", not tab.watchers)
tab.shutdown()

# --------------------------------------------------------- a janela --
w = a.App()
w.sound.setChecked(False)
names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
check("the window has a Tower Defense page", "Tower Defense" in names
      and w.tabs.widget(names.index("Tower Defense")) is w.tower_scroll,
      str(names))
w.show()
pump(0.2)
small = (w.width(), w.height())
w.tabs.setCurrentWidget(w.tower_scroll)
pump(0.2)
check("on it the window grows for the game, and the autoclicker Start "
      "steps aside", (w.width(), w.height()) != small
      and w.btn.isHidden() and w.status.isHidden())
w.tabs.setCurrentIndex(0)
pump(0.2)
check("and both come back when leaving", (w.width(), w.height()) == small
      and not w.btn.isHidden(), f"{small} {(w.width(), w.height())}")
w.tower.changed.emit()
check("a change on the tab saves soon", w._save_timer.isActive())
w._save_timer.stop()
w._save_cfg()
with open(a.CFG) as fh:
    cfg = json.load(fh)
check("the config is written whole, with the tab",
      cfg.get("tower", {}).get("strategies")
      and cfg.get("language") == "en_US"
      and not os.path.exists(a.CFG + ".tmp"))
ASK["answer"] = QMessageBox.Yes
n0 = len(w.tower.c["strategies"])
w.import_macro_path(out)
check("a strategy file given to the Hotkeys import goes to the tab",
      w.tabs.currentWidget() is w.tower_scroll
      and len(w.tower.c["strategies"]) == n0 + 1
      and "Tower Defense strategy" in ASK["asked"][-1][1])
a.LANG = "pt_BR"
w.retranslate()
check("translated", w.tabs.tabText(w.tabs.indexOf(w.tower_scroll))
      == "Tower Defense"
      and w.tower.b_dock.text() == "Abrir a Doca")
a.LANG = "en_US"
w.retranslate()
w.cleanup()
with open(a.CFG) as fh:
    cfg = json.load(fh)
check("closing saves the strategies",
      len(cfg["tower"]["strategies"]) == n0 + 1)

code = h.finish()
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(code)
