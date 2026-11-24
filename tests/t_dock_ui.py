"""A Doca sem compositor: macros que jogam nela (motor, passos, avisos), os
editores, o assistente, a aba, os links e as travas de segurança. A Doca
aqui é a classe de verdade com as peças de fora trocadas por falsas: nenhum
KWin sobe, nenhum device é criado, nada sai para a rede e a pasta da Doca
fica num diretório temporário. O KWin de verdade está em t_dock_kwin.py.

    env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen nice -n 19 \\
        dbus-run-session -- python3 -B tests/t_dock_ui.py
"""
import json, os, shutil, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-dockui-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
RT = os.path.join(TMP, "rt")
os.makedirs(RT, mode=0o700)
os.environ["XDG_RUNTIME_DIR"] = RT       # a pasta da Doca nasce aqui
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
import numpy as np                                          # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog,       # noqa: E402
                               QMessageBox)

a.CFG = os.path.join(TMP, "wayclick.json")
with open(a.CFG, "w") as fh:
    json.dump({"sound": False, "language": "en_US", "dock_size": "1600x900",
               "dock_command": "mygame --windowed"}, fh)
app = QApplication(sys.argv)
a.LANG = "en_US"
K = a.KEYS
LEFT = a.BTN["Left"]
rng = np.random.default_rng(20261120)
opened = []
a.OPENER = lambda url: opened.append(url)
REAL_SCHEME = a.scheme_handler
REAL_SCREEN = a.SCREEN


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


# ------------------------------------------------------- peças falsas --
class FakeInput:
    """O fake_input do KWin da Doca: anota tudo, ou falha como um socket
    fechado."""

    def __init__(self):
        self.log, self.fail = [], False

    def _put(self, *ev):
        if self.fail:
            raise BrokenPipeError("the Dock is closed")
        self.log.append(ev)

    def key(self, code, down):
        self._put("key", code, bool(down))

    def button(self, code, down):
        self._put("button", code, bool(down))

    def axis(self, axis, value):
        self._put("axis", axis, value)

    def move(self, dx, dy):
        self._put("move", dx, dy)

    def absolute(self, x, y):
        self._put("abs", x, y)


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


class FakeScreen:
    def __init__(self, frame):
        self.frame, self.calls, self.users = frame, 0, 0
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
        return [self.frame]


class FakeWindows:
    def __init__(self):
        self.asked, self.ok, self.error = [], True, None

    def find(self, match, focus=False):
        self.asked.append((match, focus))
        return {"found": True, "title": "Game", "cls": "org.wctest.Game",
                "active": True}

    def windows(self):
        return [{"cls": "org.wctest.Game", "title": "The game"}]

    def cursor(self):
        return (321, 123)

    def close(self):
        self.ok = False


# a tela da Doca: verde, com um retalho de ruído em (600, 300)
scene = np.zeros((720, 1280, 3), np.uint8)
scene[:] = (20, 200, 90)
patch = rng.integers(0, 256, (40, 40, 3)).astype(np.uint8)
scene[300:340, 600:640] = patch
tpl = os.path.join(TMP, "patch.png")
with open(tpl, "wb") as fh:
    fh.write(a.png_bytes(patch))
FRAME = a.Frame(scene, 0, 0, 1280, 720)


def fake_open(d):
    d.state = "on"
    d.kwin, d.link = FakeProc(), FakeLink()
    d.input = FakeInput()
    d.pointer = a.DockPointer(d.input)
    d.windows = FakeWindows()
    d.screen = FakeScreen(FRAME)
    d.out_size = (1280, 720)
    return d.input


dock = a.DOCK()
check("there is one Dock for the app", a.DOCK() is dock)
check("and it starts closed", dock.state == "off" and not dock.alive())

# ------------------------------------------------------------- macros --
m = a.normalize_macro({"steps": [{"op": "tap", "code": K["A"]}],
                       "dock": True})
plain = a.normalize_macro({"steps": [{"op": "tap", "code": K["A"]}]})
check("a macro can play in the Dock", m.get("dock") is True)
check("and the macros from before stay byte for byte the same",
      "dock" not in plain and "dock" not in a.normalize_macro(
          {"steps": [{"op": "tap", "code": 30}], "dock": "yes"}))
check("the list says where it plays",
      a.macro_summary(m).endswith("in the Dock: Tap A")
      and "Dock" not in a.macro_summary(plain), a.macro_summary(m))
check("the engine knows it",
      a.CompiledMacro(m).dock and not a.CompiledMacro(plain).dock)
check("turning it on or off restarts the macro",
      a.CompiledMacro(m).spec != a.CompiledMacro(
          dict(m, dock=False)).spec)


class Hub:
    def add(self, s):
        pass

    def remove(self, s):
        pass

    def changed(self):
        pass

    def mods_active(self):
        return frozenset()


host_out = []
eng = a.MacroEngine(lambda: host_out.append(1) or (None, None), hub=Hub())
eng.dock = dock
problems = []
eng.report = problems.append


def run(steps, wait=5.0, **extra):
    mac = a.normalize_macro(dict({"steps": steps, "dock": True,
                                  "mode": "once", "name": "dock test"},
                                 **extra))
    r = eng._start(a.CompiledMacro(mac))
    if r is not None and wait:
        r.join(wait)
    return r


r = run([{"op": "tap", "code": K["A"]}])
check("with the Dock closed a Dock macro does not start, and says why",
      r is None and problems
      and problems[-1] == "dock test: the Dock is not open, so the macro "
                          "did not start.", str(problems))
n = len(problems)
mac = a.normalize_macro({"steps": [{"op": "wait", "ms": 10}], "dock": True,
                         "mode": "always", "period_ms": 1000})
check("an Always macro of the Dock waits for it quietly",
      eng._start(a.CompiledMacro(mac), only_if_idle=True) is None
      and len(problems) == n)

inp = fake_open(dock)
runs = []
dock._run = lambda argv: runs.append(argv)
a.scheme_handler = lambda url: ["handler", url]
r = run([{"op": "tap", "code": K["A"]},
         {"op": "down", "code": K["Left Shift"]},
         {"op": "up", "code": K["Left Shift"]},
         {"op": "text", "text": "Hi"},
         {"op": "wheel", "dir": "down", "times": 2},
         {"op": "wheel", "dir": "up"},
         {"op": "wheel", "dir": "right"},
         {"op": "move", "dx": 5, "dy": -3},
         {"op": "point", "x": 300, "y": 200, "click": "left"},
         {"op": "tap", "code": LEFT},
         {"op": "open", "url": "roblox://placeId=1"},
         {"op": "open", "url": "https://example.com/x"}])
check("a Dock macro runs", r is not None and not r.is_alive())
check("as a DockRunner", isinstance(r, a.DockRunner))
log = inp.log
A, SH, H_, I_ = K["A"], K["Left Shift"], K["H"], K["I"]
check("keys go to the fake input of the Dock, in order",
      log[:4] == [("key", A, True), ("key", A, False), ("key", SH, True),
                  ("key", SH, False)], str(log[:4]))
check("text types with Shift for the capital",
      log[4:9] == [("key", SH, True), ("key", H_, True), ("key", H_, False),
                   ("key", SH, False), ("key", I_, True)]
      and log[9] == ("key", I_, False), str(log[4:10]))
check("the wheel scrolls 10 a notch, positive down and right",
      log[10:14] == [("axis", 0, 10.0), ("axis", 0, 10.0),
                     ("axis", 0, -10.0), ("axis", 1, 10.0)], str(log[10:14]))
check("a move is relative, pixel for pixel", log[14] == ("move", 5, -3))
check("a click at a point lands in the middle of that pixel of the Dock",
      log[15] == ("abs", 300.5, 200.5)
      and log[16:18] == [("button", LEFT, True), ("button", LEFT, False)],
      str(log[15:18]))
check("a mouse button tap goes there too",
      log[18:20] == [("button", LEFT, True), ("button", LEFT, False)])
check("an app link opens inside the Dock, a web link on the desktop",
      runs == [["handler", "roblox://placeId=1"]]
      and opened == ["https://example.com/x"], f"{runs} {opened}")
check("nothing went through the devices of the desktop", not host_out)
check("and nothing was left pressed", not r.held and not r.pheld)

inp.log.clear()
r = run([{"op": "pixel", "x": 100, "y": 100, "color": "#14c85a", "tol": 8,
          "wait": 1000, "fail": "stop"},
         {"op": "tap", "code": K["B"]},
         {"op": "image", "path": tpl, "conf": 0.9, "wait": 1000,
          "act": "left", "fail": "stop"},
         {"op": "window", "match": "org.wctest.Game", "wait": 1000,
          "act": "focus", "fail": "stop"},
         {"op": "tap", "code": K["C"]}])
clicks = [e for e in inp.log if e[0] == "abs"]
check("color and image steps look at the screen of the Dock",
      dock.screen.calls >= 2 and ("key", K["B"], True) in inp.log
      and ("key", K["C"], True) in inp.log, str(inp.log))
check("and the image is clicked where it is in the Dock",
      len(clicks) == 1 and 600 <= clicks[0][1] <= 640
      and 300 <= clicks[0][2] <= 340, str(clicks))
check("window steps ask the KWin of the Dock",
      dock.windows.asked == [("org.wctest.Game", True)],
      str(dock.windows.asked))
check("and the screen is handed back", dock.screen.users == 0)

inp.log.clear()
mac = a.normalize_macro({"steps": [{"op": "down", "code": K["W"]}],
                         "dock": True, "mode": "toggle", "repeat": False})
r = eng._start(a.CompiledMacro(mac))
until(lambda: ("key", K["W"], True) in inp.log, 2)
r.stop()
r.join(2)
check("a key held by the steps is let go in the Dock when it stops",
      inp.log == [("key", K["W"], True), ("key", K["W"], False)],
      str(inp.log))

problems.clear()
r = run([{"op": "wait", "ms": 300}, {"op": "tap", "code": K["A"]}], wait=0)
time.sleep(0.05)
dock.link.closed, inp.fail = True, True
r.join(3)
check("when the Dock closes under a macro it stops, saying so once",
      not r.is_alive()
      and problems == ["dock test: the Dock closed, so the macro stopped."],
      str(problems))
dock.link.closed, inp.fail = False, False

problems.clear()
host = eng._start(a.CompiledMacro(a.normalize_macro(
    {"steps": [{"op": "wait", "ms": 50}], "mode": "toggle",
     "name": "host"})))
mine = run([{"op": "wait", "ms": 2000}], wait=0, mode="toggle")
time.sleep(0.2)
dock.link.closed = True            # como Fechar a Doca: fecha e para
eng.stop_dock()
mine.join(2)
check("stop_dock stops the macros of the Dock, not the others",
      not mine.is_alive() and host.is_alive())
check("and a macro the person stopped reports nothing", not problems,
      str(problems))
dock.link.closed = False
host.stop()
host.join(2)
check("the desktop macro used the desktop outputs", host_out == [1])

# ---------------------------------------------------------- os links --
opened.clear()
runs.clear()
dock.state = "off"
reports = []
dock.open("https://example.com/y", reports.append)
dock.open("roblox://placeId=2", reports.append)
check("with the Dock closed web links still open, app links say why",
      opened == ["https://example.com/y"] and not runs
      and reports == ["the Dock is not open, so roblox://placeId=2 was not "
                      "opened."], str(reports))
dock.state = "on"
reports.clear()
a.scheme_handler = lambda url: None
dock.command = ""
dock.open("wctest://x", reports.append)
check("a link nothing opens is reported",
      reports == ["nothing on this computer opens wctest://x."]
      and not runs, str(reports))
dock.command = "mygame --windowed"
dock.open("wctest://x", reports.append)
check("without a handler the game command gets the link",
      runs == [["mygame", "--windowed", "wctest://x"]], str(runs))
dock.open("-rf", reports.append)
dock.open("steam://run/1", reports.append)
check("an option is never passed, and steam:// goes to the desktop Steam",
      runs == [["mygame", "--windowed", "wctest://x"]]
      and opened[-1] == "steam://run/1")

# um Sober aberto fora da Doca
ps = []
dock._flatpak_ps = lambda: ps.append(1) or "999999\torg.vinegarhq.Sober\n"


class P:
    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        return None


sober = ["flatpak", "run", "org.vinegarhq.Sober"]
check("a Sober open outside the Dock is seen",
      dock.sober_outside(sober) and ps == [1])
dock.games = [P(999999)]
check("the one the Dock opened is not", not dock.sober_outside(sober))
dock.games = []
ps.clear()
check("and other games never ask flatpak",
      not dock.sober_outside(["mygame"]) and not ps)
a.scheme_handler = lambda url: sober + [url]
runs.clear()
reports.clear()
dock.open("roblox://placeId=3", reports.append)
check("opening Sober while another runs says why, and opens nothing",
      not runs and reports == [a.SOBER_TWICE], str(reports))
dock.command = "flatpak run org.vinegarhq.Sober"
dock.note = ""
check("Start the game does the same",
      dock.launch() is False and dock.note == a.SOBER_TWICE and not runs)
dock._flatpak_ps = lambda: ""
dock.command = "mygame"

# ------------------------------------------------------- tamanho fixo --
hosts = []
dock._host = lambda fix=False, show=False: hosts.append((fix, show)) or {}
dock.size, dock.note = (1280, 720), ""
dock._on_sized(1000, 600)
check("a resized Dock window is put back after a moment",
      dock._fix_timer.isActive() and dock.out_size == (1000, 600))
for _i in range(a.DOCK_FIX_TRIES):
    dock._fix()
check("a few times", hosts == [(True, False)] * a.DOCK_FIX_TRIES, str(hosts))
dock._fix()
check("then it gives up and says the window may not fit",
      len(hosts) == a.DOCK_FIX_TRIES and "1000×600, not 1280×720"
      in dock.note, dock.note)
dock._on_sized(1280, 720)
check("back at its size, the note goes", not dock.note and dock._fixes == 0)
dock._fix_timer.stop()
hosts.clear()
dock.set_size(1600, 900)
check("a new size goes to the window", dock.size == (1600, 900)
      and hosts == [(True, False)])

# ---------------------------------------------------- travas e pastas --
dock2 = a.Dock()
check("the Dock folder is in the runtime dir",
      dock2.folder() == os.path.join(RT, "wayclick-dock")
      and dock2.socket_path() == os.path.join(RT, "wayland-wayclick-dock"))
os.makedirs(dock2.folder(), exist_ok=True)
for name in ("pids", "bus"):
    open(os.path.join(dock2.folder(), name), "w").close()
dock2.stop()
check("a Dock that did not open leaves the files of another alone",
      all(os.path.exists(os.path.join(dock2.folder(), n))
          for n in ("pids", "bus")))
holder = subprocess.Popen([sys.executable, "-I", "-c",
                           "import time; time.sleep(30)", "wayclick-owner"])
with open(os.path.join(dock2.folder(), "pids"), "w") as fh:
    fh.write(f"{os.getpid()} {os.getpid()} {holder.pid}\n")
try:
    dock2._reap_stale()
    err = ""
except a.DockError as e:
    err = str(e)
check("the Dock of another open WayClick is not taken",
      err == "The Dock is already open in another WayClick window.", err)
holder.kill()
holder.wait()
dock2._reap_stale()
check("once that WayClick is gone its record is dropped, and a pid that "
      "is not a Dock is never killed",
      not os.path.exists(os.path.join(dock2.folder(), "pids")))
dock2._own = True
dock2._teardown()
check("the Dock that opened cleans its own files",
      not os.path.exists(os.path.join(dock2.folder(), "bus")))

saved = os.environ.pop("WAYLAND_DISPLAY", None)
check("without Wayland the Dock says what it needs",
      dock2.missing() == "The Dock needs a Wayland session of KDE Plasma.")
os.environ["WAYLAND_DISPLAY"] = "wayland-wctest-none"
which = shutil.which
shutil.which = lambda n, *x, **k: None if n == "kwin_wayland" else \
    which(n, *x, **k)
check("and without KWin too", "kwin_wayland" in dock2.missing())
shutil.which = which
dock2.start(launch=False)
check("a Dock that cannot start says why, and is not left half open",
      dock2.state == "failed" and dock2.kwin is None
      and dock2.bus_proc is None and dock2.error, dock2.error)
os.environ.pop("WAYLAND_DISPLAY")
if saved is not None:
    os.environ["WAYLAND_DISPLAY"] = saved

# --------------------------------------------------- arquivos .desktop --
check("Exec codes: %u is the link, %i %c %k go away, %% is a percent",
      a.desktop_argv("app --x=%%done %i %c %k %U", "u://x")
      == ["app", "--x=%done", "u://x"])
check("Sober's own Exec line gets the link where it belongs",
      a.desktop_argv("/usr/bin/flatpak run --branch=stable --arch=x86_64 "
                     "--command=sober --file-forwarding org.vinegarhq.Sober "
                     "-- @@u %u @@", "roblox://placeId=1")[-3:]
      == ["@@u", "roblox://placeId=1", "@@"])
check("a broken Exec line is refused", a.desktop_argv('app "x', "u") is None)
apps = os.path.join(TMP, "data", "applications")
os.makedirs(apps)
with open(os.path.join(apps, "wctest-handler.desktop"), "w") as fh:
    fh.write("[Desktop Entry]\nType=Application\nName=handler\n"
             "Exec=/bin/echo main %u\nMimeType=x-scheme-handler/wctest;\n\n"
             "[Desktop Action other]\nName=Other\nExec=/bin/false %u\n")
os.makedirs(os.path.join(TMP, "config"), exist_ok=True)
with open(os.path.join(TMP, "config", "mimeapps.list"), "w") as fh:
    fh.write("[Default Applications]\n"
             "x-scheme-handler/wctest=wctest-handler.desktop\n")
os.environ["XDG_CURRENT_DESKTOP"] = "X-Generic"
check("only the main group of a .desktop counts, not an action",
      a._desktop_entry_field(os.path.join(apps, "wctest-handler.desktop"),
                             "Exec") == "/bin/echo main %u")
# o de verdade, com o xdg-mime do sistema e a configuração temporária
got_h = REAL_SCHEME("wctest://hello?x=1")
check("the handler of a link comes from xdg-mime and its .desktop",
      got_h == ["/bin/echo", "main", "wctest://hello?x=1"], str(got_h))
check("a scheme nobody opens has no handler, a bad one is not asked",
      REAL_SCHEME("wctestnone://x") is None
      and REAL_SCHEME("1bad://x") is None)

# ---------------------------------------------------------- editores --
fake_open(dock)
dock.command = "mygame"
d = a.MacroDialog(None, m)
check("the macro editor shows Play in the Dock",
      d.dock_box.isChecked() and d.dock_box.text() == "Play in the Dock")
check("and keeps it", d.macro().get("dock") is True
      and d.target_dock() is dock)
d.dock_box.setChecked(False)
check("unchecked, the macro is a desktop one again",
      "dock" not in d.macro() and d.target_dock() is None)
d.dock_box.setChecked(True)

a.SCREEN = lambda: (_ for _ in ()).throw(AssertionError("portal asked"))
sd = a.StepDialog(d, a.normalize_step({"op": "pixel", "x": 1, "y": 2,
                                       "color": "#000000"}))
check("a step of a Dock macro knows the Dock", sd._dock() is dock)
picked = []


def pick_exec(self):
    picked.append(True)
    return QDialog.Rejected


a.ScreenPicker.exec = pick_exec
sd.delay.setValue(0)
calls = dock.screen.calls
sd._capture("point")
check("its capture takes the screen of the Dock, with no portal",
      dock.screen.calls == calls + 1 and picked and not sd.error.text(),
      sd.error.text())
sd2 = a.StepDialog(d, a.normalize_step({"op": "window", "match": "x"}))
sd2._list_windows()
check("and its window list is the one inside the Dock",
      [sd2.match.itemText(i) for i in range(sd2.match.count())]
      == ["org.wctest.Game", "The game"])

got = []


def rec_exec(self):
    got.append((self.cursor() if self.cursor else None, self.dock))
    return QDialog.Rejected


a.RecordDialog.exec = rec_exec
d._record()
check("the recorder reads the cursor inside the Dock",
      got == [((321, 123), True)], str(got))

fd = a.FarmDialog(None, [], {"dock": True, "over": tpl})
check("the farm assistant can play in the Dock",
      fd.dock_box.isChecked() and fd.spec()["dock"] is True
      and fd.target_dock() is dock)
fd.delay.setValue(0)
frames = fd._grab()
check("and captures the buttons from the Dock",
      len(frames) == 1 and frames[0] is FRAME)
farm = a.farm_macro(dict(fd.spec(), window="Sober"))
check("the farm it builds plays in the Dock", farm.get("dock") is True)
fd.dock_box.setChecked(False)
check("or on the desktop", "dock" not in a.farm_macro(
    dict(fd.spec(), window="Sober")))

dock.state = "off"
sd._capture("point")
check("with the Dock closed a capture says to open it",
      sd.error.text() == "The Dock is not open. Open it on the Dock tab "
                         "first.", sd.error.text())
d.error.setText("")
got.clear()
d._record()
check("and so does the recorder", not got and d.error.text()
      == "The Dock is not open. Open it on the Dock tab first.")
dock.state = "on"

# --------------------------------------------------------------- app --
a.SCREEN = REAL_SCREEN
QMessageBox.information = lambda *x, **k: None
QMessageBox.warning = lambda *x, **k: None
dock.state = "off"
w = a.App()
w.sound.setChecked(False)
names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
DOCK_TAB = names.index("Dock") if "Dock" in names else -1
check("the window has a Dock page, before Tower Defense and the Sober "
      "accounts", DOCK_TAB >= 0
      and names[DOCK_TAB + 1:] == ["Tower Defense", "Sober accounts"],
      str(names))
check("the engine plays Dock macros in it", w.engine.dock is dock)
check("size and game come from the settings",
      w.dock_size.currentText() == "1600 × 900"
      and w.dock.size == (1600, 900) and w.dock.command == "mygame --windowed"
      and w.dock_cmd.text() == "mygame --windowed")
check("closed, it says so and offers to open",
      w.dock_state.text() == "Closed." and w.dock_open.text()
      == "Open the Dock" and not w.dock_show.isEnabled()
      and w.dock_game.isEnabled())
starts = []
dock.start = lambda launch=True: starts.append(launch) or True
w._dock_toggle()
check("Open the Dock opens it with the game", starts == [True])
w.dock_cmd.setText("")
w._dock_toggle()
check("with no game command it opens empty, and Start the game is off",
      starts == [True, False] and not w.dock_game.isEnabled()
      and dock.command == "")
w.dock_cmd.setText("mygame --windowed")
fake_open(dock)
dock.changed.emit()
check("open, it shows its size and marks the tab",
      w.dock_state.text() == "Open, 1280×720. No game is running in it."
      and w.dock_open.text() == "Close the Dock" and w.dock_show.isEnabled()
      and w.tabs.tabText(DOCK_TAB) == "● Dock",
      w.dock_state.text())
w.macros = [a.normalize_macro({"steps": [{"op": "tap", "code": K["A"]}],
                               "dock": True, "code": K["F7"]})]
check("Dock macros do not need the keyboard of the desktop",
      not w._macros_need_keyboard())
w.dock_size.setCurrentIndex(w.dock_size.findData("1366x768"))
check("changing the size resizes the open Dock",
      dock.size == (1366, 768))
mine = w.engine._start(a.CompiledMacro(a.normalize_macro(
    {"steps": [{"op": "wait", "ms": 2000}], "dock": True, "mode": "toggle",
     "name": "app dock"})))
w._dock_toggle()
mine.join(3)
check("Close the Dock closes it and stops its macros",
      dock.state == "off" and not mine.is_alive()
      and w.dock_state.text() == "Closed.")
a.LANG = "pt_BR"
w.retranslate()
check("and the tab is translated",
      w.tabs.tabText(DOCK_TAB) == "Doca"
      and w.dock_open.text() == "Abrir a Doca"
      and w.dock_hint.text().startswith("O jogo abre numa janela"))
a.LANG = "en_US"
w.retranslate()
w.cleanup()
with open(a.CFG) as fh:
    cfg = json.load(fh)
check("size and game are saved",
      cfg.get("dock_size") == "1366x768"
      and cfg.get("dock_command") == "mygame --windowed", str(
          {k: cfg.get(k) for k in ("dock_size", "dock_command")}))

code = h.finish()
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(code)
