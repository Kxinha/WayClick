"""PixelGetColor, PixelSearch, ImageSearch e Suspend vindos de um .ahk, sem
tocar no desktop (ver harness.py).

Primeiro o importador: cada forma de escrever a busca (v1 com ErrorLevel e
cor BGR, v2 com retorno e cor RGB, o "if !ImageSearch(...) return" do v2, o
else, o Loop N, o break no laço do macro) vira passos Cor e Imagem com os
saltos certos. Depois a busca de cor numa área, na ordem do AutoHotkey, e o
macro importado rodando de verdade contra uma tela falsa: o ponteiro
absoluto e o teclado virtual são capturados pelo harness no instante em que
nascem, então o cursor e o teclado de verdade nunca recebem nada.

    QT_QPA_PLATFORM=offscreen nice -n 19 python3 tests/t_ahk_screen.py
"""
import math, os, struct, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-ahkscreen-")
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
rng = np.random.default_rng(20261127)


def imp(text, name="t.ahk"):
    path = os.path.join(TMP, name)
    with open(path, "w") as fh:
        fh.write(text)
    return a.import_ahk_file(path)


def notes(i):
    return [a.note_text(n) for n in i.notes]


def ops(m):
    return [s["op"] for s in m["steps"]]


# picture used by the ImageSearch scripts, next to them
tile = rng.integers(0, 256, (6, 8, 3))
btn = np.clip(np.kron(tile, np.ones((5, 5, 1))) + 0, 0, 255).astype(np.uint8)
BTN_PNG = os.path.join(TMP, "btn.png")
with open(BTN_PNG, "wb") as fh:
    fh.write(a.png_bytes(btn))

# ================================================================ Suspend ==
i = imp("F7::Suspend\n*$Space::\nwhile GetKeyState(\"Space\",\"P\") {\n"
        "    Send {Blind}{F9}\n    Sleep 15\n}\nreturn\n")
check("F7::Suspend becomes the Suspend hotkey, not a macro",
      i.suspend == (K["F7"], [], "F7") and len(i.macros) == 1, i.suspend)
m = i.macros[0]
check("and the bhop part is Space while held, F9 every 15.6 ms, Space hidden",
      (m["code"], m["mode"], m["block"], ops(m), m["steps"][0]["code"])
      == (K["Space"], "hold", True, ["tap", "wait"], K["F9"]))
check("no 'not supported' note for Suspend",
      not any("Suspend" in n for n in notes(i)), notes(i))
i = imp("^F7::Suspend, Toggle\n")
check("^F7::Suspend, Toggle keeps Ctrl",
      i.suspend == (K["F7"], ["ctrl"], "Ctrl+F7"), i.suspend)
i = imp("F7::\nSuspend\nSoundBeep\nreturn\nF8::Suspend\nF9::Suspend(-1)\n")
check("the first Suspend hotkey wins, the others get a note",
      i.suspend and i.suspend[0] == K["F7"]
      and sum("already F7" in n for n in notes(i)) == 2, notes(i))
i = imp("F6::Suspend Off\n")
check("Suspend Off still becomes the Suspend hotkey, with a note",
      i.suspend and any("Suspend Off" in n for n in notes(i)), notes(i))
i = imp("XButton1::Suspend\n")
check("a mouse button cannot be the Suspend hotkey",
      i.suspend is None and any("mouse button" in n for n in notes(i)))
rep = a.import_report(i, "x.ahk")
i = imp("F7::Suspend\nF1::Send e\n")
rep = a.import_report(i, "x.ahk", (K["F8"], "F8"))
check("the import report names the Suspend hotkey",
      "Suspend hotkey: F7" in rep, rep)
i = imp("F8::Suspend\nF8 & F1::Send x\n")
rep = a.import_report(i, "x.ahk", (K["F8"], "F8"))
check("and warns when it is also the arm/disarm hotkey",
      "also the arm/disarm hotkey" in rep, rep)
check("a macro with the Suspend key gets a warning",
      a.suspend_clash_text([a.normalize_macro({"code": K["F7"], "steps": [
          {"op": "tap", "code": K["E"]}]})], K["F7"], []) != "")

# ========================================================= PixelGetColor ==
i = imp("CoordMode, Pixel, Screen\nCoordMode, Mouse, Screen\nF2::\n"
        "PixelGetColor, c, 100, 200\nif (c = 0x0000FF)\n    Click, 100, 200\n"
        "return\n")
m = i.macros[0]
st = m["steps"][0]
check("v1 PixelGetColor is BGR: 0x0000FF is red",
      (st["op"], st["x"], st["y"], st["color"], st["tol"], st["want"],
       st["wait"]) == ("pixel", 100, 200, "#ff0000", 0, "seen", 0.0), st)
check("the click inside the if comes after it, the else jumps over it",
      st["fail"] == "jump" and st["fail_to"] == len(m["steps"])
      and m["steps"][-1] == {"op": "wait", "ms": 0.0}
      and ops(m)[1:3] == ["point", "tap"], m["steps"])
check("CoordMode Pixel, Screen leaves out the window note",
      not any("active window" in n for n in notes(i)), notes(i))
i = imp("F2::\nPixelGetColor, c, 1, 2, RGB\nif c != 0x0000FF\n  Send e\n"
        "return\n")
st = i.macros[0]["steps"][0]
check("RGB mode keeps the order, and != waits for the color to go",
      (st["color"], st["want"]) == ("#0000ff", "gone"), st)
check("without CoordMode the window note is there, once",
      sum("active window" in n for n in notes(i)) == 1, notes(i))
i = imp("#Requires AutoHotkey v2.0\nF2:: {\n  if PixelGetColor(5, 6) = "
        "0xFF0000\n    Send \"e\"\n}\n")
st = i.macros[0]["steps"][0]
check("v2 PixelGetColor is RGB", (st["op"], st["x"], st["y"], st["color"])
      == ("pixel", 5, 6, "#ff0000"), st)

# =========================================================== PixelSearch ==
i = imp("CoordMode Pixel, Screen\nF2::\nPixelSearch, Px, Py, 10, 20, 300, "
        "400, 0x00FF00, 8\nif !ErrorLevel\n  MouseClick, right, %Px%, %Py%\n"
        "return\n")
m = i.macros[0]
st = m["steps"][0]
check("v1 PixelSearch: the area, BGR color, variation as tolerance",
      (st["op"], st["region"], st["color"], st["tol"]) ==
      ("pixel", [10, 20, 291, 381], "#00ff00", 8), st)
check("v1 searches column by column unless Fast",
      st.get("scan") == "cols" and not st.get("rtl") and not st.get("btt"))
check("MouseClick right at the found spot became the step's action",
      st["act"] == "right" and ops(m) == ["pixel", "wait"], m["steps"])
i = imp("CoordMode Pixel, Screen\nF2::\nPixelSearch, Px, Py, 0, 0, "
        "A_ScreenWidth, A_ScreenHeight, 0x0000FF, 10\nif !ErrorLevel\n"
        "  Click %Px% %Py%\nreturn\n")
st = i.macros[0]["steps"][0]
txt = a.step_label(st)
check("PixelSearch over the whole screen says so in the list",
      a._whole_screen(st["region"]) and txt.startswith(
          "Find #ff0000 anywhere on the screen"), txt)
i = imp("F2::\nPixelSearch, Px, Py, 300, 400, 10, 20, 0xFF0000, , Fast RGB\n"
        "if (ErrorLevel = 0)\n  Click %Px% %Py%\nreturn\n")
st = i.macros[0]["steps"][0]
check("Fast RGB: row by row, RGB order, X1 > X2 and Y1 > Y2 reverse it",
      (st["color"], st.get("scan"), st.get("rtl"), st.get("btt"),
       st["region"], st["act"]) == ("#ff0000", None, True, True,
                                    [10, 20, 291, 381], "left"), st)
i = imp("#Requires AutoHotkey v2.0\nF2:: {\n  if PixelSearch(&x, &y, 0, 0, "
        "50, 50, 0x123456, 3)\n    Click x, y, 2\n}\n")
st = i.macros[0]["steps"][0]
check("v2 PixelSearch: RGB, rows, double click from Click x, y, 2",
      (st["color"], st.get("scan"), st["tol"], st["act"])
      == ("#123456", None, 3, "double"), st)

# =========================================================== ImageSearch ==
i = imp("CoordMode Pixel, Screen\nF2::\nImageSearch, FoundX, FoundY, 0, 0, "
        "A_ScreenWidth, A_ScreenHeight, *40 btn.png\nif (ErrorLevel = 0) {\n"
        "  Click, %FoundX%, %FoundY%\n  Sleep 50\n} else {\n  Send e\n}\n"
        "Send q\nreturn\n")
m = i.macros[0]
s = m["steps"]
check("v1 ImageSearch: the picture next to the script, whole screen",
      s[0]["op"] == "image" and s[0]["path"] == BTN_PNG
      and "region" not in s[0], s[0])
check("*40 became a similarity, with a note",
      s[0]["conf"] == 0.87 and any("*40 became 87%" in n for n in notes(i)),
      notes(i))
check("found: click it, wait 50; else: E; then Q for both",
      ops(m) == ["image", "wait", "count", "tap", "tap"]
      and s[0]["act"] == "left" and s[0]["fail_to"] == 4
      and s[2]["ok"] == "jump" and s[2]["ok_to"] == 5
      and s[3]["code"] == K["E"] and s[4]["code"] == K["Q"], s)
i = imp("F2::\nImageSearch, X, Y, 10, 10, 200, 100, C:\\Users\\me\\Desktop"
        "\\btn.png\nif ErrorLevel\n  Send e\nreturn\n")
st = i.macros[0]["steps"][0]
check("a Windows path finds the picture next to the script, and the area "
      "stays", st["path"] == BTN_PNG and st["region"] == [10, 10, 191, 91]
      and st["want"] == "gone" and st["act"] == "none", st)
i = imp("F2::\nImageSearch, X, Y, 0, 0, 10, 10, missing.png\nif !ErrorLevel"
        "\n  Click %X%, %Y%\nreturn\n")
check("a missing picture gets a note",
      any("missing.png was not found" in n for n in notes(i)), notes(i))
i = imp("#Requires AutoHotkey v2.0\nF3:: {\n  static on := false\n"
        "  on := !on\n  SetTimer(Farm, on ? 50 : 0)\n}\nFarm() {\n"
        "  if !ImageSearch(&x, &y, 0, 0, A_ScreenWidth, A_ScreenHeight, "
        "\"*0 \" A_ScriptDir \"\\btn.png\")\n    return\n  Click x, y\n"
        "  Send \"e\"\n}\n")
m = i.macros[0]
s = m["steps"]
check("v2 'if !ImageSearch(...) return' then Click x, y: find and click, "
      "else end this run", (m["mode"], m["period_ms"]) == ("toggle", 50.0)
      and ops(m) == ["image", "tap", "wait"] and s[0]["want"] == "seen"
      and s[0]["act"] == "left" and s[0]["fail_to"] == 3
      and s[0]["path"] == BTN_PNG and s[0]["conf"] == 0.95, s)
i = imp("#Requires AutoHotkey v2.0\nF2:: {\n  found := ImageSearch(&a, &b, "
        "0, 0, 99, 99, \"btn.png\")\n  if found\n    Click a, b\n}\n")
st = i.macros[0]["steps"][0]
check("found := ImageSearch(...) then if found",
      (st["op"], st["want"], st["act"], st["region"])
      == ("image", "seen", "left", [0, 0, 100, 100]), st)
i = imp("F2::\nImageSearch, X, Y, 0, 0, 99, 99, btn.png\nif !ErrorLevel\n"
        "{\n  Send e\n  Click %X%, %Y%\n}\nreturn\n")
check("a click at the found spot that is not right after the search "
      "runs on the interpreter instead", ops(i.macros[0]) == ["script"]
      and any("interpreter" in n for n in notes(i))
      and not any("right after the search" in n for n in notes(i)),
      notes(i))

# ===================================================== laços e repetição ==
i = imp("F2::\nLoop 3 {\n  ImageSearch, X, Y, 0, 0, 99, 99, btn.png\n"
        "  if !ErrorLevel\n    Click, %X%, %Y%\n  Send e\n}\nreturn\n")
m = i.macros[0]
s = m["steps"]
check("Loop 3 copies the search three times, each jumping inside its copy",
      ops(m) == ["image", "tap"] * 3
      and [st["fail_to"] for st in s if st["op"] == "image"] == [2, 4, 6], s)
i = imp("F2::\nLoop {\n  ImageSearch, X, Y, 0, 0, 99, 99, btn.png\n"
        "  if (ErrorLevel = 0)\n    break\n  Send e\n  Sleep 100\n}\n"
        "return\n")
m = i.macros[0]
st = m["steps"][0]
check("if found, break in the macro's loop: the macro stops when it sees it",
      (m["mode"], st["want"], st["ok"], st["fail"], ops(m))
      == ("toggle", "seen", "stop", "next", ["image", "tap", "wait"]),
      m["steps"])
i = imp("F2::\nPixelGetColor, c, 1, 1\nif (c = 0xFFFFFF) {\n  Send a\n"
        "  return\n}\nSend b\nreturn\n")
s = i.macros[0]["steps"]
check("return inside the if ends that run",
      [st["op"] for st in s] == ["pixel", "tap", "count", "tap", "wait"]
      and s[2]["ok_to"] == 5 and s[0]["fail_to"] == 4, s)
i = imp("F2::\nPixelGetColor, c, 1, 1\nif (c = 0x0000FF)\n  Send a\n"
        "else if (c = 0x00FF00)\n  Send b\nelse\n  Send c\nreturn\n")
s = i.macros[0]["steps"]
check("else if chains", [st["op"] for st in s] ==
      ["pixel", "tap", "count", "pixel", "tap", "count", "tap", "wait"]
      and s[0]["fail_to"] == 4 and s[2]["ok_to"] == 8
      and s[3]["fail_to"] == 7 and s[5]["ok_to"] == 8
      and (s[0]["color"], s[3]["color"]) == ("#ff0000", "#00ff00"), s)
i = imp("F2::\nif (x = 0x00FF00)\n  Send a\nreturn\n")
check("a comparison with no search before it runs on the interpreter",
      ops(i.macros[0]) == ["script"]
      and any("interpreter" in n for n in notes(i)), notes(i))
norm = a.normalize_macro(i.macros[0]) if i.macros else None
for case in ("v1", "v2"):
    pass
every = [a.normalize_macro(m) == m for m in imp(
    "F2::\nPixelSearch, X, Y, 9, 9, 0, 0, 0xABCDEF, 4\nif !ErrorLevel\n"
    "  Click %X%, %Y%\nreturn\n").macros]
check("imported screen steps survive normalize_macro unchanged", all(every)
      and every)

# ================================================ busca de cor, a ordem ==
H, W = 60, 80
img = np.zeros((H, W, 3), np.uint8)
for (x, y) in ((50, 10), (10, 40), (70, 30)):
    img[y, x] = (0, 200, 0)
fr = [a.Frame(img, 0, 0, W, H)]
G = (0, 200, 0)
whole = [0, 0, W, H]
check("rows: the topmost first", a.pixel_search(fr, whole, G, 0)
      == (50.5, 10.5))
check("columns (v1 default): the leftmost first",
      a.pixel_search(fr, whole, G, 0, cols=True) == (10.5, 40.5))
check("right to left", a.pixel_search(fr, whole, G, 0, cols=True, rtl=True)
      == (70.5, 30.5))
check("bottom to top", a.pixel_search(fr, whole, G, 0, btt=True)
      == (10.5, 40.5))
check("tolerance", a.pixel_search(fr, whole, (0, 190, 8), 10) is not None
      and a.pixel_search(fr, whole, (0, 190, 8), 9) is None)
check("the area limits it", a.pixel_search(fr, [0, 20, 40, 40], G, 0)
      == (10.5, 40.5) and a.pixel_search(fr, [0, 0, 40, 30], G, 0) is None)
right = [a.Frame(np.kron(img, np.ones((2, 2, 1), np.uint8)), 1920, 0, W, H)]
check("on a 200% screen to the right it answers in logical units",
      a.pixel_search(right, [1920, 0, W, H], G, 0) == (1920 + 50.25, 10.25))

# =============================================== rodando contra a tela ==
ws = (0, 0, 640, 480)


class AbsRecorder(h.Recorder):
    """Posições e botões a cada SYN, como o compositor leria."""

    def __init__(self, node):
        super().__init__(node)
        self.state = [0, 0]

    def clear(self):
        self.poll()
        self.events = []

    def poll(self, seconds=0.0):
        time.sleep(seconds + 0.01)
        with open(self.path, "rb") as fh:
            fh.seek(self.pos)
            data = fh.read()
        usable = len(data) - len(data) % h.SIZE
        self.pos += usable
        for k in range(0, usable, h.SIZE):
            _s, _u, t, c, v = struct.unpack_from(h.FMT, data, k)
            self.events.append((t, c, v))
        return self.events

    def clicks(self):
        out, ax, ay, btn = [], self.state[0], self.state[1], set()
        for t, c, v in self.events:
            if t == EV_ABS:
                if c == 0:
                    ax = v
                else:
                    ay = v
            elif t == 1:
                if v:
                    out.append((c, math.floor(ax * ws[2] / 65536),
                                math.floor(ay * ws[3] / 65536)))
        self.state = [ax, ay]
        return out


class FakeScreen:
    def __init__(self, frames):
        self.frames_ = frames
        self.ok, self.error, self.state = True, "", "on"

    def ensure(self, timeout=None):
        return True

    def acquire(self):
        pass

    def release(self):
        pass

    def frames(self):
        return self.frames_


ptr = a.AbsolutePointer()
vk, vm = a.VirtualKeyboard(), a.VirtualMouse()
check("the pointer and keyboard were captured by the test when they were "
      "born", {ptr.node, vk.node, vm.node} <= set(h.READERS))
rec, keys = AbsRecorder(ptr.node), h.Recorder(vk.node)
hub = a.HUB()
engine = a.MacroEngine(lambda: (vk, vm), hub=hub)
engine.pointer, engine.workspace = ptr, ws
problems = []
engine.problem.connect(problems.append)


def run(m, screen):
    engine.screen = screen
    m = a.normalize_macro(dict(m, mode="once", repeat=False, press_ms=10.0))
    rec.clear()
    keys.clear()
    r = engine._start(a.CompiledMacro(m))
    r.join(5.0)
    app.processEvents()
    rec.poll(0.02)
    keys.poll()
    return [e[1] for e in keys.keys(None, 1)]


bg = rng.integers(0, 256, (480, 640, 3)).astype(np.uint8)
with_btn = bg.copy()
with_btn[300:330, 400:440] = btn
script = ("CoordMode Pixel, Screen\nF2::\nImageSearch, FoundX, FoundY, 0, 0, "
          "A_ScreenWidth, A_ScreenHeight, *40 btn.png\nif (ErrorLevel = 0) {"
          "\n  Click, %FoundX%, %FoundY%\n} else {\n  Send e\n}\nSend q\n"
          "return\n")
m = imp(script, "run.ahk").macros[0]
got = run(m, FakeScreen([a.Frame(with_btn, 0, 0, 640, 480)]))
cl = rec.clicks()
check("picture on screen: clicks its middle, no E, then Q",
      got == [LEFT, K["Q"]] or (K["Q"] in got and K["E"] not in got)
      and cl and cl[0][0] == LEFT and abs(cl[0][1] - 420) <= 1
      and abs(cl[0][2] - 315) <= 1, f"keys {got}, clicks {cl}")
got = run(m, FakeScreen([a.Frame(bg, 0, 0, 640, 480)]))
check("picture not there: E then Q, no click",
      got == [K["E"], K["Q"]] and not rec.clicks(), f"keys {got}")
red = bg.copy()
red[200, 100] = (255, 0, 0)
m = imp("CoordMode Pixel, Screen\nF2::\nPixelGetColor, c, 100, 200\n"
        "if (c = 0x0000FF)\n  Send a\nelse\n  Send b\nreturn\n",
        "pg.ahk").macros[0]
check("PixelGetColor red at 100, 200: A",
      run(m, FakeScreen([a.Frame(red, 0, 0, 640, 480)])) == [K["A"]])
check("and B when it is not red",
      run(m, FakeScreen([a.Frame(bg, 0, 0, 640, 480)])) == [K["B"]])
dot = np.zeros((480, 640, 3), np.uint8)
dot[50, 600] = dot[400, 20] = (0, 255, 0)
m = imp("CoordMode Pixel, Screen\nF2::\nPixelSearch, Px, Py, 0, 0, 639, 479,"
        " 0x00FF00, 0\nif !ErrorLevel\n  MouseClick, right, %Px%, %Py%\n"
        "return\n", "ps.ahk").macros[0]
run(m, FakeScreen([a.Frame(dot, 0, 0, 640, 480)]))
cl = rec.clicks()
check("v1 PixelSearch right-clicks the leftmost dot (column order)",
      cl and cl[0][0] == RIGHT and (cl[0][1], cl[0][2]) == (20, 400), cl)
check("no problems were reported", not problems, problems)

engine.close()
ptr.close()
vk.close()
vm.close()
sys.exit(h.finish())
