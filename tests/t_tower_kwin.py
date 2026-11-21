"""A aba Tower Defense contra a Doca de verdade, dentro da caixa de
kwin_nested.py. Um Tower Defense de mentira (uma janela Qt com lobby, partida,
dinheiro que sobe, torres com painel, pular onda, vitória e derrota) abre na
Doca, e o farm joga duas partidas nele só pela tela (ScreenShot2) e pelo
fake_input. No meio da segunda o jogo cai, e o farm abre de novo pelo link
roblox://, que na caixa um "Sober" de mentira atende. A visão da aba também
é conferida contra a Doca: quantos quadros por segundo ela mostra, quanto
custa, e se mouse, roda e teclas em cima dela chegam ao jogo. Nada sai da
caixa: o desktop de quem roda não recebe tecla nem clique, e o Sober de
verdade não é aberto.

    nice -n 19 python3 -B tests/t_tower_kwin.py
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kwin_nested as kn                                    # noqa: E402

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail
                                                     else ""))
    sys.stdout.flush()


# os desenhos do jogo, iguais no jogo e no teste (a mesma semente): fundo
# escuro em blocos de 8 px, botões de 32 px em blocos de 4 px
SPRITES = r"""
import numpy as np
W, H, S = 960, 540, 16
_rng = np.random.default_rng(20261121)
BG = (_rng.integers(10, 50, (H // 8, W // 8, 3)).astype(np.uint8)
      .repeat(8, 0).repeat(8, 1))
NAMES = ("play", "wave", "panel", "sell", "skip", "victory", "defeat",
         "again", "reconnect")
SPR = {n: _rng.integers(0, 256, (8, 8, 3)).astype(np.uint8)
       .repeat(4, 0).repeat(4, 1) for n in NAMES}
POS = {"play": (150, 450), "wave": (900, 40), "panel": (840, 300),
       "sell": (840, 370), "skip": (480, 60), "victory": (480, 225),
       "defeat": (480, 225), "again": (480, 380), "reconnect": (480, 300)}
SPOTS = ((225, 300), (330, 300))
"""

# o jogo: anota em ~/game.log o que acontece com ele
GAME = SPRITES + r"""
import os, sys, time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

HOME = os.path.expanduser("~")
log = open(sys.argv[1], "a", buffering=1)


def note(*x):
    log.write(" ".join(str(v) for v in x) + "\n")


def qimg(arr):
    arr = np.ascontiguousarray(arr)
    return QImage(arr.data, arr.shape[1], arr.shape[0], 3 * arr.shape[1],
                  QImage.Format_RGB888).copy()


note("argv", *sys.argv[2:])
note("env", os.environ.get("WAYLAND_DISPLAY", ""))
app = QApplication(sys.argv[:1])
QGuiApplication.setDesktopFileName("org.wctest.Sober")
BGI = qimg(BG)
SPI = {n: qimg(v) for n, v in SPR.items()}
SLOTS = {2 + k: k for k in range(5)}          # 1 a 5, códigos do evdev
KQ, KE, KZ, KO = 16, 18, 44, 24
COST, LENGTH = 100.0, 6.0
ENDED = HOME + "/ended"        # partidas terminadas, de um processo ao outro
CRASH = HOME + "/crash-once"   # existindo, a segunda partida cai, uma vez


def ended():
    try:
        with open(ENDED) as fh:
            return int(fh.read() or 0)
    except (OSError, ValueError):
        return 0


class Game(QWidget):
    def __init__(s):
        super().__init__()
        s.setMouseTracking(True)
        s.state, s.since = "lobby", time.monotonic()
        s.holding = s.panel = s.result = s.zoom_t = s.drag_from = None
        s.towers, s.spent, s.skipped = [], 0.0, set()
        s.timer = QTimer(s)
        s.timer.timeout.connect(s.tick)
        s.timer.start(30)

    def t(s):
        return time.monotonic() - s.since

    def go(s, state):
        s.state, s.since = state, time.monotonic()

    def tick(s):
        st, t = s.state, s.t()
        if st == "loading" and t >= 0.5:
            s.go("match")
            s.towers, s.spent, s.skipped = [], 0.0, set()
            s.holding = s.panel = None
            note("start", ended() + 1)
        elif st == "match":
            if t >= 1.5 and ended() == 1 and os.path.exists(CRASH):
                os.unlink(CRASH)
                note("crash")
                os._exit(0)
            if t >= LENGTH:
                n = ended() + 1
                with open(ENDED, "w") as fh:
                    fh.write(str(n))
                s.result = "victory" if n % 2 else "defeat"
                s.go("end")
                note("end", s.result)
        s.update()

    def cash(s):
        return 50.0 + 100.0 * s.t() - s.spent

    def skip_shown(s):
        p = int(s.t())
        return s.state == "match" and p % 2 == 1 and p not in s.skipped

    def shown(s):
        if s.state == "lobby":
            return ["play"]
        if s.state == "match":
            out = ["wave"]
            if s.panel is not None:
                out += ["panel", "sell"]
            if s.skip_shown():
                out.append("skip")
            return out
        if s.state == "end":
            return [s.result, "again"]
        return []

    def paintEvent(s, e):
        p = QPainter(s)
        p.drawImage(0, 0, BGI)
        for n in s.shown():
            cx, cy = POS[n]
            p.drawImage(cx - S, cy - S, SPI[n])

    @staticmethod
    def hit(n, x, y):
        cx, cy = POS[n]
        return abs(x - cx) <= S and abs(y - cy) <= S

    def keyPressEvent(s, e):
        if e.isAutoRepeat():
            return
        c = e.nativeScanCode() - 8
        note("key", c)
        if c == KO:
            s.zoom_t = time.monotonic()
        elif s.state != "match":
            return
        elif c in SLOTS:
            s.holding = SLOTS[c]
        elif c == KQ:
            s.holding = s.panel = None
        elif c in (KE, KZ) and s.panel is not None:
            tw = s.towers[s.panel]
            note("upgrade", tw[0], tw[1], tw[2],
                 "top" if c == KE else "bottom")
        s.update()

    def keyReleaseEvent(s, e):
        if e.isAutoRepeat():
            return
        c = e.nativeScanCode() - 8
        note("keyup", c)
        if c == KO and s.zoom_t is not None:
            note("zoom", round((time.monotonic() - s.zoom_t) * 1000))
            s.zoom_t = None

    def mousePressEvent(s, e):
        x, y = int(e.position().x()), int(e.position().y())
        note("press", e.button().value, x, y)
        if e.button() == Qt.RightButton:
            s.drag_from = e.position().y()
        if e.button() != Qt.LeftButton:
            return
        st, t = s.state, s.t()
        if st == "lobby" and s.hit("play", x, y):
            note("play")
            s.go("loading")
        elif st == "end" and s.hit("again", x, y):
            note("again")
            s.go("lobby")
        elif st == "match":
            if s.skip_shown() and s.hit("skip", x, y):
                s.skipped.add(int(t))
                note("skip", int(t))
            elif s.panel is not None and s.hit("sell", x, y):
                tw = s.towers.pop(s.panel)
                s.panel = None
                note("sell", tw[0], tw[1], tw[2])
            elif s.holding is not None:
                if s.cash() >= COST:
                    s.spent += COST
                    s.towers.append([s.holding, x, y])
                    s.panel = len(s.towers) - 1
                    note("place", s.holding, x, y, round(t, 2))
                    s.holding = None
                else:
                    note("broke", s.holding, round(t, 2))
            else:
                near = [i for i, tw in enumerate(s.towers)
                        if abs(tw[1] - x) <= 12 and abs(tw[2] - y) <= 12]
                s.panel = near[0] if near else None
        s.update()

    def mouseReleaseEvent(s, e):
        note("release", e.button().value, int(e.position().x()),
             int(e.position().y()))
        if e.button() == Qt.RightButton and s.drag_from is not None:
            note("drag", round(e.position().y() - s.drag_from))
            s.drag_from = None

    def wheelEvent(s, e):
        note("wheel", e.angleDelta().x(), e.angleDelta().y())

    def resizeEvent(s, e):
        note("size", s.width(), s.height())


w = Game()
w.setWindowTitle("Roblox")
w.show()
app.exec()
"""

PROBE = SPRITES + r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import wayclick as a
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
a.LANG = "en_US"
HOME = os.environ["HOME"]
K, BTN = a.KEYS, a.BTN
LINK = "roblox://placeId=3260590327&linkCode=abc123"
R = []


def check(label, ok, detail=""):
    R.append([label, bool(ok), str(detail)[:1500]])


def until(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        app.processEvents()
        time.sleep(0.01)
    return cond()


def pump(seconds):
    until(lambda: False, seconds)


def game():
    try:
        with open(HOME + "/game.log") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def cpu(pid):
    with open(f"/proc/{pid}/stat") as fh:
        f = fh.read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")


pics = {}
os.makedirs(HOME + "/pics", exist_ok=True)
for n, img in SPR.items():
    pics[n] = f"{HOME}/pics/{n}.png"
    with open(pics[n], "wb") as fh:
        fh.write(a.png_bytes(img))
open(HOME + "/crash-once", "w").close()

d = a.DOCK()
check("this computer has what the Dock needs", d.missing() == "",
      d.missing())
d.size = (W, H)
d.command = f"{sys.executable} -I -B {HOME}/game.py {HOME}/game.log"
ok = d.start(launch=True)
found = ok and until(lambda: (d.windows.find("Sober") or {}).get("found"),
                     40)
check("the Dock opens with the game in it", found, d.error)
until(lambda: d.out_size == (W, H) and f"size {W} {H}" in game(), 10)
check("and the game fills it", d.out_size == (W, H)
      and f"size {W} {H}" in game(), f"{d.out_size} {game()[-3:]}")
check("its screen comes with no portal", d.screen.ensure(), d.screen.error)
fr = d.screen.frames()[0]
hit = a.find_image(fr.rgb, SPR["play"], 0.9)
check("the farm sees the game as it draws itself",
      hit and (hit[0] + S, hit[1] + S) == POS["play"] and hit[2] > 0.97,
      str(hit))

# ------------------------------------------------------- a visão --
view = a.DockView(d)
view.resize(W * 3 // 2, H * 3 // 2)             # a Doca em 1,5×
view.show()
calls = []
orig = d.screen.qimage


def timed():
    t = time.perf_counter()
    img = orig()
    calls.append(time.perf_counter() - t)
    return img


d.screen.qimage = timed
kpid = d.kwin.pid
c0, k0, t0 = time.process_time(), cpu(kpid), time.perf_counter()
view.start()
pump(4.0)
wall = time.perf_counter() - t0
c1, k1 = time.process_time(), cpu(kpid)
fps = len(calls) / wall
each = 1000 * sum(calls) / max(1, len(calls))
VIEW = (f"{fps:.1f} fps, {each:.1f} ms per capture, WayClick "
        f"{100 * (c1 - c0) / wall:.0f}% and the Dock KWin "
        f"{100 * (k1 - k0) / wall:.0f}% of a core")
check("the view shows the Dock about 20 times a second", fps >= 15, VIEW)
img = view.image
px = img.pixelColor(5, 5) if not img.isNull() else None
check("with the picture of the game, at the size of the Dock",
      img.width() == W and img.height() == H and px is not None
      and all(abs(v - int(c)) <= 3 for v, c in zip(
          (px.red(), px.green(), px.blue()), BG[5, 5])),
      f"{img.width()}x{img.height()} {px and px.getRgb()} "
      f"{BG[5, 5].tolist()}")


def mouse(kind, x, y, button=Qt.LeftButton, held=None):
    t = {"press": QEvent.MouseButtonPress, "release":
         QEvent.MouseButtonRelease, "move": QEvent.MouseMove}[kind]
    p = view.to_view(x, y)
    QApplication.sendEvent(view, QMouseEvent(
        t, p, p, button if kind != "move" else Qt.NoButton,
        held if held is not None else (button if kind == "press"
                                       else Qt.NoButton), Qt.NoModifier))


def key(code, down=True):
    QApplication.sendEvent(view, QKeyEvent(
        QEvent.KeyPress if down else QEvent.KeyRelease, 0, Qt.NoModifier,
        code + 8, 0, 0, "", False))


n0 = len(game())
mouse("move", 100, 100)
mouse("press", 100, 100)
mouse("release", 100, 100)
key(K["F"])
key(K["F"], False)
p = view.to_view(100, 100)
QApplication.sendEvent(view, QWheelEvent(
    p, p, QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
    Qt.NoScrollPhase, False))
mouse("press", 480, 270, Qt.RightButton)
mouse("move", 480, 285, held=Qt.RightButton)
mouse("move", 480, 300, held=Qt.RightButton)
mouse("release", 480, 300, Qt.RightButton)
until(lambda: any(x.startswith("drag") for x in game()[n0:]), 5)
got = game()[n0:]
check("a click on the view lands on that point of the game",
      "press 1 100 100" in got and "release 1 100 100" in got, str(got))
check("keys on the view reach the game", f"key {K['F']}" in got
      and f"keyup {K['F']}" in got, str(got))
check("the wheel too, one notch up", "wheel 0 120" in got, str(got))
check("a right drag on the view turns the camera, pixel for pixel",
      "press 2 480 270" in got and "drag 30" in got, str(got))
view.pick = True
picked = []
view.picked.connect(lambda *x: picked.append(x))
n0 = len(game())
mouse("press", 200, 210)
pump(0.5)
check("picking a spot does not click the game",
      picked == [(200, 210, BTN["Left"])] and len(game()) == n0,
      f"{picked} {game()[n0:]}")
view.pick = False
key(K["W"])
until(lambda: f"key {K['W']}" in game()[n0:], 3)
QApplication.sendEvent(view, QFocusEvent(QEvent.FocusOut))
until(lambda: f"keyup {K['W']}" in game()[n0:], 3)
check("a key held on the view is let go when it loses the focus",
      f"keyup {K['W']}" in game()[n0:], str(game()[n0:]))

# ----------------------------------------------------------- o farm --
S1 = a.normalize_tower_strategy({
    "name": "Bench", "size": [W, H],
    "towers": [{"name": "Scout", "points": [list(SPOTS[0])]},
               {"name": "Sniper", "points": [list(SPOTS[1])]}],
    "actions": [
        {"t": 0.8, "do": "place", "tower": 0, "point": 0},
        {"t": 1.0, "do": "place", "tower": 1, "point": 0},
        {"t": 3.0, "do": "upgrade", "tower": 0, "point": 0, "path": "top"},
        {"t": 3.3, "do": "upgrade", "tower": 1, "point": 0,
         "path": "bottom"},
        {"t": 3.6, "do": "key", "code": K["F"], "name": "F"}],
    "lobby": [pics["play"]]})
c = a.normalize_tower({
    "strategies": [S1], "screens": {
        "start": pics["wave"], "placed": pics["panel"], "skip": pics["skip"],
        "victory": pics["victory"], "defeat": pics["defeat"],
        "reconnect": pics["reconnect"], "after": [pics["again"]]},
    "speed": "fast", "zoom_ms": 400, "drag": 150, "retry": 0.3,
    "link": LINK, "limit": 2, "lobby_wait": 10, "start_wait": 10})
plan = json.loads(json.dumps(c))
plan["strategy"] = plan["strategies"][0]
plan["lobby_steps"] = []
env = a.TowerEnv(d)
done, lines = [], []
r = a.TowerRunner(plan, d, env, on_done=done.append)
n0, v0 = len(game()), len(calls)
t0 = time.perf_counter()
r.start()
while r.is_alive() and time.perf_counter() - t0 < 150:
    pump(0.1)
    lines += env.take()
r.join(3.0)
lines += env.take()
took = time.perf_counter() - t0
vfps = (len(calls) - v0) / took
log = [x[2] for x in lines]
warns = [x[2] for x in lines if x[1]]
check("the farm plays its 2 matches and stops",
      not r.is_alive() and done == [r]
      and r.counters == {"matches": 2, "wins": 1, "losses": 1},
      f"{r.counters} in {took:.0f} s\n" + "\n".join(log))
got = game()[n0:]
segs, cur = [], None
for line in got:
    w = line.split()
    if w[0] == "start":
        cur = [line]
        segs.append(cur)
    elif cur is not None:
        cur.append(line)
        if w[0] in ("end", "crash"):
            cur = None
whole = [s for s in segs if s[-1].startswith("end")]
check("in the game: a victory, a crash, then a defeat",
      [s[-1] for s in segs] == ["end victory", "crash", "end defeat"],
      str([s[-1] for s in segs]))


def evs(seg, kind):
    return [x.split()[1:] for x in seg if x.split()[0] == kind]


check("each tower went down on its spot, in each match",
      all([e[:3] for e in evs(s, "place")] == [
          ["0", str(SPOTS[0][0]), str(SPOTS[0][1])],
          ["1", str(SPOTS[1][0]), str(SPOTS[1][1])]] for s in whole)
      and len(whole) == 2, str([evs(s, "place") for s in whole]))
check("the second one after trying again for the cash",
      all(evs(s, "broke") for s in whole)
      and any("maybe not enough cash" in x for x in log),
      str([evs(s, "broke") for s in whole]))
check("each got its upgrade, on its path",
      all(evs(s, "upgrade") == [
          ["0", str(SPOTS[0][0]), str(SPOTS[0][1]), "top"],
          ["1", str(SPOTS[1][0]), str(SPOTS[1][1]), "bottom"]]
          for s in whole), str([evs(s, "upgrade") for s in whole]))
check("the key of the timeline was pressed",
      all(["33"] in evs(s, "key") for s in whole))
check("Skip wave was clicked when it showed up",
      all(evs(s, "skip") for s in whole), str([evs(s, "skip")
                                               for s in whole]))
zooms = [int(e[0]) for s in whole for e in evs(s, "zoom")]
drags = [int(e[0]) for s in whole for e in evs(s, "drag")]
check("the camera was lined up each match: zoom held, dragged down",
      len(zooms) == 2 and all(350 <= z <= 700 for z in zooms)
      and drags == [150, 150], f"zoom {zooms} ms, drag {drags}")
check("the crashed game opened again in the Dock, by the link",
      "The game window closed." in log and "The game is open." in log
      and f"argv {LINK}" in got and "env wayland-wayclick-dock" in got,
      "\n".join(log) + "\n" + str([x for x in got if x[:4] in ("argv",
                                                              "env ")]))
last = got[max(i for i, x in enumerate(got) if x.startswith("argv")):]
held = {}
for line in last:
    w = line.split()
    if w[0] in ("key", "keyup"):
        held[("k", w[1])] = w[0] == "key"
    elif w[0] in ("press", "release"):
        held[("b", w[1])] = w[0] == "press"
check("nothing is left pressed in the game",
      not [k for k, v in held.items() if v], str(held))
check("with nothing to warn about", not warns, "\n".join(warns))
check("while the view kept showing it", vfps >= 10,
      f"{vfps:.1f} fps during the farm, {took:.0f} s")
view.stop()
d.stop()
check("closing the Dock closes the game", d.state == "off"
      and until(lambda: not d.game_running(), 10))
R.append(["view: " + VIEW, True, ""])
print(json.dumps(R))
"""


with kn.Bench(outputs=1) as b:
    with open(os.path.join(b.home, "game.py"), "w") as fh:
        fh.write(GAME)
    apps = os.path.join(b.home, ".local", "share", "applications")
    os.makedirs(apps, exist_ok=True)
    with open(os.path.join(apps, "wctest-sober.desktop"), "w") as fh:
        fh.write("[Desktop Entry]\nType=Application\nName=wctest Sober\n"
                 f"Exec={sys.executable} -I -B /home/wctest/game.py "
                 "/home/wctest/game.log %u\n"
                 "MimeType=x-scheme-handler/roblox;\n")
    os.makedirs(os.path.join(b.home, ".config"), exist_ok=True)
    with open(os.path.join(b.home, ".config", "mimeapps.list"), "a") as fh:
        fh.write("[Default Applications]\n"
                 "x-scheme-handler/roblox=wctest-sober.desktop\n")
    r = b.run([sys.executable, "-I", "-B", "-c", PROBE, kn.REPO],
              timeout=420)
    out = (r.get("out") or "").strip().splitlines()
    try:
        res = json.loads(out[-1])
    except (IndexError, ValueError):
        res = None
    if not res:
        check("the probe ran", False, f"{r.get('code')} "
              f"{(r.get('out') or '')[-3000:]} {(r.get('err') or '')[-3000:]}")
    else:
        for label, ok, detail in res:
            check(label, ok, detail if not ok or len(detail) < 200 else "")
        if not all(x[1] for x in res):
            print(b.tail("inside", 2000))

n, bad = len(RESULTS), RESULTS.count(False)
print(f"\n{n - bad}/{n} passed")
sys.exit(1 if bad else 0)
