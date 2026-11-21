"""Um jogo continua rodando dentro da Doca com a janela dela atrás de outra
ou minimizada, na caixa de kwin_nested.py.

O "jogo" é o es2gears_wayland (mesa-demos), que desenha com EGL e espera o
vsync a cada quadro, como um jogo; ele imprime os quadros por segundo a cada
5 s no log da Doca. Se o KWin da caixa parasse de mandar quadros à janela
escondida da Doca, o KWin da Doca pararia de desenhar e o jogo travaria.

    nice -n 19 python3 -B tests/t_dock_hidden.py     (pula sem es2gears_wayland)
"""
import json, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kwin_nested as kn                                    # noqa: E402

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail
                                                     else ""))
    sys.stdout.flush()


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

PROBE = r'''
import json, os, re, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import wayclick as a
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
R = []


def check(label, ok, detail=""):
    R.append([label, bool(ok), str(detail)[:300]])


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


HOST_JS = """
var ws = workspace.windowList();
var out = {found: false};
for (var i = 0; i < ws.length; i++) {
  var w = ws[i];
  if (!(%(match)s)) continue;
  %(body)s
  out.found = true;
  out.active = w.active === true;
  out.minimized = w.minimized === true;
  out.x = w.frameGeometry.x; out.y = w.frameGeometry.y;
  out.w = w.frameGeometry.width; out.h = w.frameGeometry.height;
  break;
}
callDBus("%(svc)s", "%(path)s", "%(iface)s", "answer",
         "%(token)s " + JSON.stringify(out));
"""


def host(match, body=""):
    raw = a.KWIN().call(HOST_JS, match=match, body=body, timeout=3.0)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


OTHER = 'String(w.resourceClass) === "org.wctest.Other"'
d = a.DOCK()
d.size = (800, 600)
d.command = "stdbuf -oL es2gears_wayland"
check("the Dock opens with the game", d.start(launch=True) and d.alive(),
      d.error)
DOCKW = f"w.pid === {d.kwin.pid}"


def fps(seconds=11):
    """Os quadros por segundo que o jogo contou nesse tempo."""
    def lines():
        try:
            with open(os.path.join(d.folder(), "dock.log"), "rb") as fh:
                t = fh.read().decode("utf-8", "replace")
        except OSError:
            return []
        return re.findall(r"frames in [\d.]+ seconds = ([\d.]+) FPS", t)
    n0 = len(lines())
    pump(seconds)
    return [float(x) for x in lines()[n0:]]


pump(3)
got = fps()
check("with the Dock window in front, the game draws at the screen rate",
      got and min(got) > 50, str(got))
host(OTHER, "w.frameGeometry = {x: 0, y: 0, width: 1920, height: 1080}; "
     "w.keepAbove = true; workspace.activeWindow = w;")
pump(1)
info, cover = host(DOCKW), host(OTHER)
got = fps()
check("behind a window that covers the whole screen, it keeps drawing",
      not info.get("active") and cover.get("w") == 1920 and got
      and min(got) > 50, f"{got} {info} {cover}")
host(OTHER, "w.keepAbove = false; w.minimized = true;")
host(DOCKW, "w.minimized = true;")
pump(1)
info = host(DOCKW)
got = fps()
check("minimized, it keeps drawing",
      info.get("minimized") and got and min(got) > 50, f"{got} {info}")
shot = d.screen.ensure() and d.screen.frames()[0]
check("and the Dock still sees it", shot is not False and shot is not None
      and shot.rgb.std() > 5, d.screen.error)
host(DOCKW, "w.minimized = false; workspace.activeWindow = w;")
pump(1)
got = fps()
check("back in front, same as before", got and min(got) > 50, str(got))
d.stop()
pump(1)
check("closing the Dock closes the game", not d.game_running())
print(json.dumps(R))
'''

if not shutil.which("es2gears_wayland") or not shutil.which("stdbuf"):
    print("SKIP  es2gears_wayland (mesa-demos) is not installed")
    sys.exit(0)
with kn.Bench(outputs=1) as b:
    b.request(op="spawn", name="other",
              argv=[sys.executable, "-I", "-B", "-c", OTHER],
              env={"QT_QPA_PLATFORM": "wayland"})
    end = time.monotonic() + 30
    while time.monotonic() < end and not any(
            w["cls"] == "org.wctest.Other" for w in b.kwin.windows()):
        time.sleep(0.2)
    r = b.run([sys.executable, "-I", "-B", "-c", PROBE, kn.REPO],
              timeout=300)
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
            check(label, ok, detail)
    b.request(op="stop", name="other")

n, bad = len(RESULTS), RESULTS.count(False)
print(f"\n{n - bad}/{n} passed")
sys.exit(1 if bad else 0)
