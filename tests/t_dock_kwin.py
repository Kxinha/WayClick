"""A Doca de verdade, dentro da caixa de kwin_nested.py: o KWin dela sobe
numa janela do KWin da caixa, o jogo (uma janela Qt que anota o que recebe)
abre dentro dela, e um macro da Doca joga com o foco do "desktop" em outra
janela. Confere teclas, cliques, roda, arrasto relativo, tela e cor, imagem,
janelas, minimizada, tamanho fixo, links, fechar pela janela e a Doca que um
WayClick que caiu deixou aberta. Nada sai da caixa: o desktop de quem roda
não recebe tecla nem clique, e o Sober não é aberto.

    nice -n 19 python3 -B tests/t_dock_kwin.py
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


# o jogo: verde, com um desenho que não se repete em (600, 300), e anota
GAME = r"""
import sys
from PySide6.QtGui import QGuiApplication, QPainter, QColor
from PySide6.QtWidgets import QApplication, QWidget
log = open(sys.argv[1], "a", buffering=1)
app = QApplication(sys.argv[:1])
QGuiApplication.setDesktopFileName("org.wctest.Game")
class W(QWidget):
    def __init__(s):
        super().__init__()
        s.setMouseTracking(True)
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 200, 90))
        for x, y, w, h, c in ((600, 300, 40, 40, (255, 0, 255)),
                              (605, 305, 12, 20, (255, 230, 0)),
                              (622, 310, 10, 8, (0, 60, 255)),
                              (610, 328, 25, 6, (255, 255, 255))):
            p.fillRect(x, y, w, h, QColor(*c))
    def keyPressEvent(self, e):
        log.write(f"key {e.nativeScanCode()} {e.text()!r}\n")
    def mousePressEvent(self, e):
        p = e.position()
        log.write(f"press {e.button().value} {round(p.x())} {round(p.y())}\n")
    def mouseReleaseEvent(self, e):
        p = e.position()
        log.write(f"release {e.button().value} {round(p.x())} {round(p.y())}\n")
    def mouseMoveEvent(self, e):
        if e.buttons().value:
            p = e.position()
            log.write(f"drag {round(p.x())} {round(p.y())}\n")
    def wheelEvent(self, e):
        log.write(f"wheel {e.angleDelta().x()} {e.angleDelta().y()}\n")
    def resizeEvent(self, e):
        log.write(f"size {self.width()} {self.height()}\n")
w = W()
w.setWindowTitle("Game")
w.show()
app.exec()
"""

# outra janela no "desktop", que fica com o foco
OTHER = r"""
import sys
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication(sys.argv[:1])
QGuiApplication.setDesktopFileName("org.wctest.Other")
w = QWidget()
w.setWindowTitle("Other")
w.resize(700, 500)
w.show()
app.exec()
"""

# quem atende wctest:// (o papel do Sober com roblox://)
HANDLER = r"""
import os, sys
with open(os.path.expanduser("~/handler.log"), "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
    fh.write("WAYLAND_DISPLAY=" + os.environ.get("WAYLAND_DISPLAY", "") + "\n")
"""

# um WayClick que abre a Doca e fica aberto (ou cai, quando o teste mata)
HOLDER = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import wayclick as a
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
d = a.Dock()
d.size, d.command = (800, 600), ""
ok = d.start(launch=False)
with open(sys.argv[2], "w") as fh:
    fh.write(f"{int(ok)} {d.kwin.pid if d.kwin else 0} "
             f"{d.bus_proc.pid if d.bus_proc else 0}\n")
while True:
    app.processEvents()
    time.sleep(0.05)
"""

PROBE = r"""
import json, os, signal, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import wayclick as a
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
a.LANG = "en_US"
HOLDER = sys.argv[2]
HOME = os.environ["HOME"]
K, BTN = a.KEYS, a.BTN
R = []


def check(label, ok, detail=""):
    R.append([label, bool(ok), str(detail)[:300]])


def until(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        app.processEvents()
        time.sleep(0.02)
    return cond()


def pump(seconds):
    until(lambda: False, seconds)


def game():
    try:
        with open(HOME + "/game.log") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


HOST_JS = '''
var ws = workspace.windowList();
var out = {found: false};
for (var i = 0; i < ws.length; i++) {
  var w = ws[i];
  if (!(%(match)s)) continue;
  %(body)s
  out.found = true;
  out.active = w.active === true;
  out.minimized = w.minimized === true;
  out.w = w.clientGeometry.width;
  out.h = w.clientGeometry.height;
  break;
}
callDBus("%(svc)s", "%(path)s", "%(iface)s", "answer",
         "%(token)s " + JSON.stringify(out));
'''


def host(match, body=""):
    raw = a.KWIN().call(HOST_JS, match=match, body=body, timeout=3.0)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def pid_is(pid):
    return f"w.pid === {int(pid)}"


OTHER = 'String(w.resourceClass) === "org.wctest.Other"'


def dead(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] in ("Z", "X")
    except OSError:
        return True


class Hub:
    def add(self, s): pass
    def remove(self, s): pass
    def changed(self): pass
    def mods_active(self): return frozenset()


d = a.DOCK()
check("this computer has what the Dock needs", d.missing() == "",
      d.missing())
cfgfile = os.path.join(os.environ["XDG_CONFIG_HOME"],
                       "kwinoutputconfig.json")
before = open(cfgfile, "rb").read() if os.path.exists(cfgfile) else None

# ------------------------------------------------------------- abrir --
d.size = (1280, 720)
d.command = f"{sys.executable} -I -B {HOME}/game.py {HOME}/game.log"
states = []
d.changed.connect(lambda: states.append(d.state))
sizes = []
d._sized.connect(lambda w, h: sizes.append((w, h)))
t0 = time.perf_counter()
ok = d.start(launch=True)
took = time.perf_counter() - t0
check("the Dock opens", ok and d.alive(), d.error)
check("in a few seconds", took < 10, f"{took:.2f} s")
check("saying it is opening first", states[:1] == ["starting"]
      and "on" in states, str(states))
up = until(lambda: any(x.startswith("size") for x in game()), 40)
found = until(lambda: (d.windows.find("org.wctest.Game") or {})
              .get("found"), 20)
check("the game opens inside it, as a window of the KWin of the Dock",
      up and found and d.game_running(), str(game()[-5:]))
until(lambda: game() and game()[-1] == "size 1280 720", 5)
check("and fills it, with no border",
      [x for x in game() if x.startswith("size")][-1:] == ["size 1280 720"],
      str([x for x in game() if x.startswith("size")]))
dockcfg = os.path.join(d.folder(), "config")
check("the KWin of the Dock keeps its settings in a folder of its own",
      os.path.exists(os.path.join(dockcfg, "kwinoutputconfig.json")),
      str(sorted(os.listdir(dockcfg))))
after = open(cfgfile, "rb").read() if os.path.exists(cfgfile) else None
check("and the screen settings of the desktop are not touched",
      after == before)
pid = d.kwin.pid
info = d._host()
check("on the desktop the Dock is one window of 1280×720",
      info and info.get("found") and (info["w"], info["h"]) == (1280, 720),
      str(info))
check("and its screen inside is 1280×720", d.out_size == (1280, 720),
      str(d.out_size))

# ---------------------------------------- jogar com o foco em outra --
host(OTHER, "workspace.activeWindow = w;")
pump(0.3)
o0 = host(OTHER)
c0 = a.KWIN().cursor()
eng = a.MacroEngine(lambda: (None, None), hub=Hub())
eng.dock = d
problems = []
eng.report = problems.append


def run(steps, wait=30, **extra):
    m = a.normalize_macro(dict({"steps": steps, "dock": True,
                                "mode": "once", "name": "bench"}, **extra))
    r = eng._start(a.CompiledMacro(m))
    if r is not None and wait:
        until(lambda: not r.is_alive(), wait)
    return r


n0 = len(game())
r = run([{"op": "tap", "code": K["A"]},
         {"op": "point", "x": 300, "y": 200, "click": "left"},
         {"op": "wheel", "dir": "down", "times": 2},
         {"op": "wheel", "dir": "up"},
         {"op": "text", "text": "Hi"},
         {"op": "down", "code": BTN["Right"]},
         {"op": "move", "dx": 0, "dy": 40},
         {"op": "up", "code": BTN["Right"]},
         {"op": "wait", "ms": 400}])
got = game()[n0:]
keys = [x for x in got if x.startswith("key")]
check("a Dock macro runs to the end", r is not None and not r.is_alive())
check("its keys reach the game, with the desktop focus on another window",
      o0 and o0.get("active") and keys[:1] == ["key 38 'a'"], str(got[:6]))
check("a click lands on the point, in Dock coordinates",
      "press 1 300 200" in got and "release 1 300 200" in got, str(got))
wheel = [x for x in got if x.startswith("wheel")]
ys = [int(x.split()[2]) for x in wheel]
check("the wheel turns one notch at a time, down twice and up once",
      ys == [-120, -120, 120], str(wheel))
check("text types the capital with Shift",
      keys[1:4] == ["key 50 ''", "key 43 'H'", "key 31 'i'"], str(keys))
drag = [x for x in got if x.startswith(("drag", "press 2", "release 2"))]
check("a held button with a relative move drags, pixel for pixel",
      drag[:1] == ["press 2 300 200"] and drag[-1:] == ["release 2 300 240"],
      str(drag))
o1 = host(OTHER)
check("the desktop focus stayed where it was",
      o1 and o1.get("active"), str(o1))
check("and so did the desktop cursor", a.KWIN().cursor() == c0,
      f"{c0} {a.KWIN().cursor()}")
check("with nothing to report", not problems, str(problems))

# ------------------------------------------------------------- a tela --
shot = d.screen
check("the screen of the Dock comes with no portal", shot.ensure(),
      shot.error)
f = shot.frames()[0]
check("it is the 1280×720 of the Dock",
      f.rgb.shape == (720, 1280, 3) and (f.w, f.h) == (1280, 720),
      str(f.rgb.shape))
check("with the colors of the game",
      [abs(int(v) - c) <= 3 for v, c in zip(f.rgb[100, 100], (20, 200, 90))]
      == [True] * 3, str(f.rgb[100, 100].tolist()))
t = time.perf_counter()
for _i in range(20):
    shot._shot()
avg = (time.perf_counter() - t) / 20
check("each capture takes milliseconds", avg < 0.1, f"{avg * 1000:.1f} ms")
tpl = HOME + "/tpl.png"
with open(tpl, "wb") as fh:
    fh.write(a.png_bytes(f.rgb[300:340, 600:640]))
n0 = len(game())
r = run([{"op": "pixel", "x": 100, "y": 100, "color": "#14c85a", "tol": 12,
          "wait": 3000, "fail": "stop"},
         {"op": "image", "path": tpl, "conf": 0.9, "wait": 3000,
          "act": "left", "fail": "stop"},
         {"op": "window", "match": "org.wctest.Game", "act": "focus",
          "wait": 3000, "fail": "stop"},
         {"op": "tap", "code": K["B"]}])
got = game()[n0:]
press = [x.split() for x in got if x.startswith("press 1")]
check("a color step sees the game, an image step finds and clicks it",
      len(press) == 1 and 600 <= int(press[0][2]) < 640
      and 300 <= int(press[0][3]) < 340, str(got))
check("and a window step finds it in the Dock", "key 56 'b'" in got,
      str(got))

host(pid_is(pid), "w.minimized = true;")
mini = (host(pid_is(pid)) or {}).get("minimized")
n0 = len(game())
r = run([{"op": "pixel", "x": 100, "y": 100, "color": "#14c85a", "tol": 12,
          "wait": 3000, "fail": "stop"},
         {"op": "tap", "code": K["C"]}])
check("with the Dock window minimized the macro still sees and plays",
      mini and "key 54 'c'" in game()[n0:], str(game()[n0:]))
info = d.show()
h2 = host(pid_is(pid)) or {}
check("Show brings it back to the front",
      info and info.get("found") and not h2.get("minimized")
      and h2.get("active"), str(h2))
host(OTHER, "w.frameGeometry = {x: 0, y: 0, width: 1920, height: 1080}; "
     "workspace.activeWindow = w;")
pump(0.3)
n0 = len(game())
r = run([{"op": "pixel", "x": 100, "y": 100, "color": "#14c85a", "tol": 12,
          "wait": 3000, "fail": "stop"},
         {"op": "tap", "code": K["D"]}])
check("and covered by another window too", "key 40 'd'" in game()[n0:],
      str(game()[n0:]))

# ------------------------------------------------------- tamanho fixo --
host(pid_is(pid), "w.frameGeometry = {x: w.frameGeometry.x, y: "
     "w.frameGeometry.y, width: 1000, height: 600};")
# 1000×600 é a moldura; a tela de dentro perde a barra de título
back = until(lambda: any(x[0] == 1000 for x in sizes)
             and d.out_size == (1280, 720)
             and (host(pid_is(pid)) or {}).get("w") == 1280, 10)
check("a resized Dock window goes back to its size", back,
      f"{sizes} {d.out_size} {host(pid_is(pid))}")
check("with no note left", not d.note, d.note)
d.set_size(1600, 900)
ok = until(lambda: d.out_size == (1600, 900), 10)
check("a new size resizes the Dock and the game in it",
      ok and until(lambda: "size 1600 900" in game(), 10),
      f"{d.out_size} {[x for x in game() if x.startswith('size')][-3:]}")
d.set_size(1280, 720)
until(lambda: d.out_size == (1280, 720), 10)

# -------------------------------------------------------------- links --
opened = []
a.OPENER = lambda url: opened.append(url)
r = run([{"op": "open", "url": "wctest://hello?x=1"},
         {"op": "open", "url": "https://example.com/z"}])
log = HOME + "/handler.log"
until(lambda: os.path.exists(log) and "WAYLAND" in open(log).read(), 15)
text = open(log).read() if os.path.exists(log) else ""
check("an app link of a Dock macro opens its app inside the Dock",
      "wctest://hello?x=1" in text
      and "WAYLAND_DISPLAY=wayland-wayclick-dock" in text, text)
check("a web link opens on the desktop", opened == ["https://example.com/z"],
      str(opened))

# ------------------------------------------------ fechar pela janela --
problems.clear()
r = run([{"op": "wait", "ms": 2000}, {"op": "tap", "code": K["A"]}], wait=0)
pump(0.3)
host(pid_is(pid), "w.closeWindow();")
gone = until(lambda: d.state == "off", 15)
check("closing the Dock window closes the Dock", gone, d.state)
until(lambda: not r.is_alive(), 10)
check("the game closes with it", until(lambda: not d.game_running(), 15))
check("and the macro playing in it stops, saying so once",
      not r.is_alive()
      and problems == ["bench: the Dock closed, so the macro stopped."],
      str(problems))
check("its files and socket go away",
      not os.path.exists(os.path.join(d.folder(), "pids"))
      and not os.path.exists(os.path.join(d.folder(), "bus"))
      and until(lambda: not os.path.exists(d.socket_path()), 5))
r = run([{"op": "tap", "code": K["A"]}])
check("a Dock macro with the Dock closed does not start, and says why",
      r is None and problems[-1:] == ["bench: the Dock is not open, so "
                                      "the macro did not start."],
      str(problems))

# ------------------------------------- outro WayClick, e o que caiu --
mark = HOME + "/holder.txt"
hp = subprocess.Popen([sys.executable, "-I", "-B", "-c", HOLDER,
                       sys.argv[1], mark])
until(lambda: os.path.exists(mark) and open(mark).read().endswith("\n"),
      40)
hok, kpid, bpid = (int(x) for x in open(mark).read().split())
check("another WayClick opens its Dock", hok == 1 and kpid and bpid)
ok = d.start(launch=False)
check("this one does not take it away",
      not ok and d.state == "failed" and d.error.startswith(
          "The Dock is already open in another WayClick window.")
      and not dead(kpid), d.error)
hp.send_signal(signal.SIGKILL)
hp.wait()
pump(0.5)
left = not dead(kpid) and not dead(bpid)
ok = d.start(launch=False)
check("when that WayClick crashes its Dock stays behind",
      left, f"{kpid} {bpid}")
check("and the next Dock closes it and opens",
      ok and d.alive() and until(lambda: dead(kpid) and dead(bpid), 10),
      d.error)
d.stop()
check("closing leaves nothing running",
      d.state == "off" and d.kwin is None and d.bus_proc is None
      and until(lambda: not os.path.exists(d.socket_path()), 5))
print(json.dumps(R))
"""


def until(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.2)
    return cond()


with kn.Bench(outputs=1) as b:
    with open(os.path.join(b.home, "game.py"), "w") as fh:
        fh.write(GAME)
    with open(os.path.join(b.home, "handler.py"), "w") as fh:
        fh.write(HANDLER)
    apps = os.path.join(b.home, ".local", "share", "applications")
    os.makedirs(apps, exist_ok=True)
    with open(os.path.join(apps, "wctest-handler.desktop"), "w") as fh:
        fh.write("[Desktop Entry]\nType=Application\nName=wctest handler\n"
                 f"Exec={sys.executable} -I -B /home/wctest/handler.py %u\n"
                 "MimeType=x-scheme-handler/wctest;\n")
    os.makedirs(os.path.join(b.home, ".config"), exist_ok=True)
    with open(os.path.join(b.home, ".config", "mimeapps.list"), "a") as fh:
        fh.write("[Default Applications]\n"
                 "x-scheme-handler/wctest=wctest-handler.desktop\n")
    b.request(op="spawn", name="other",
              argv=[sys.executable, "-I", "-B", "-c", OTHER],
              env={"QT_QPA_PLATFORM": "wayland"})
    up = until(lambda: any(w["cls"] == "org.wctest.Other"
                           for w in b.kwin.windows()), 30)
    check("the desktop of the box has another window", up)
    r = b.run([sys.executable, "-I", "-B", "-c", PROBE, kn.REPO, HOLDER],
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
            check(label, ok, detail if not ok or len(detail) < 160 else "")
        if not all(x[1] for x in res):
            print(b.tail("inside", 2000))
    b.request(op="stop", name="other")

n, bad = len(RESULTS), RESULTS.count(False)
print(f"\n{n - bad}/{n} passed")
sys.exit(1 if bad else 0)
