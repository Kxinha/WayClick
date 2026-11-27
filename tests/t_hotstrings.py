"""Hotstrings, #IfWinActive, o som dos macros e scripts no motor, no kernel,
sem tocar no desktop (ver harness.py): o teclado falso digita, o hub do
WayClick captura, e o que ele manda sai num teclado virtual capturado aqui.

A janela ativa é de mentira (o vigia do KWin não roda): o motor só lê
windows.active_info, e é isso que o teste troca.

    QT_QPA_PLATFORM=offscreen nice -n 19 dbus-run-session -- \
        python3 tests/t_hotstrings.py
"""
import os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-hotstrings-")
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
from PySide6.QtCore import QCoreApplication                 # noqa: E402

app = QCoreApplication(sys.argv)
a.LANG = "en_US"
K = a.KEYS
SHIFT, BS, SPACE = K["Left Shift"], K["Backspace"], K["Space"]


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


kbd = h.fake_keyboard()
hub = a.HUB()
hub.add(h.KeepMice())
h.wait_grabbed(hub, [kbd])
ck = h.clone_of(hub, kbd)
vk, vm = a.VirtualKeyboard(), a.VirtualMouse()
ok_ = h.Recorder(vk.node)
engine = a.MacroEngine(lambda: (vk, vm), hub=hub)
check("every device the app created was captured by the test",
      {vk.node, vm.node, hub.device(kbd.node).out.node} <= set(h.READERS))
beeps, problems = [], []
engine.beep.connect(beeps.append)
engine.problem.connect(problems.append)


class FakeWindows:
    active_info = None

    def find(self, match, focus=False):
        return {"found": False}

    def active_now(self):
        return self.active_info


wins = FakeWindows()
engine.windows = wins


def typed(text, gap=0.012):
    """Digita no teclado falso, com Shift onde o caractere pede."""
    for ch in text:
        if ch == "\b":
            kbd.tap(BS, 0.01)
        else:
            code, shift = a.CHAR_KEYS[ch]
            if shift:
                kbd.key(SHIFT, True)
                time.sleep(0.005)
            kbd.tap(code, 0.01)
            if shift:
                kbd.key(SHIFT, False)
        time.sleep(gap)


def out_text():
    """O que o teclado virtual digitou: letras com Shift viram maiúsculas,
    Backspace vira \\b."""
    ok_.poll(0.05)
    shift, s = False, ""
    for t, c, v, _ts in ok_.keys():
        if c == SHIFT:
            shift = v == 1
            continue
        if v != 1:
            continue
        if c == BS:
            s += "\b"
            continue
        ch = a.CHAR_OF.get((c, shift))
        s += ch if ch is not None else f"<{c}>"
    return s


def relayed_downs(code):
    ck.poll(0.03)
    return len(ck.keys(code, 1))


def hs(abbr, text, opts=(), **extra):
    return a.normalize_macro(dict({
        "name": "", "hotstring": {"abbr": abbr, "opts": list(opts)},
        "press_ms": a.HS_PRESS_MS,
        "steps": [{"op": "text", "text": text}]}, **extra))


macros = [hs("btw", "by the way"), hs("@@", "me@mail.com", ["*"]),
          hs("Ab", "exact", ["C"]), hs("keep", "!", ["B0"])]
engine.configure(macros, True)
pump(0.2)

# ================================================================ texto ==
ok_.clear()
ck.clear()
typed("btw ")
pump(0.6)
check("btw + space: three Backspaces, the text, then the space",
      out_text() == "\b\b\bby the way ", repr(out_text()))
check("the space that ended it did not reach the app (it is retyped)",
      relayed_downs(SPACE) == 0)
ok_.clear()
typed("Btw.")
pump(0.6)
check("Btw: the capital is copied, and the period is kept",
      out_text() == "\b\b\bBy the way.", repr(out_text()))
ok_.clear()
typed("BTW ")
pump(0.6)
check("BTW: all capitals", out_text() == "\b\b\bBY THE WAY ",
      repr(out_text()))
ok_.clear()
typed("xbtw ")
pump(0.4)
check("inside a word it does not fire (no ? option)", out_text() == "",
      repr(out_text()))
ok_.clear()
typed("btx\bw ")
pump(0.6)
check("Backspace edits what was typed: btx, erase, w fires",
      out_text() == "\b\b\bby the way ", repr(out_text()))
ok_.clear()
typed("@@")
pump(0.6)
check("* fires on the last character, which is swallowed",
      out_text() == "\bme@mail.com", repr(out_text()))
ok_.clear()
typed("ab Ab ")
pump(0.6)
check("C: only the exact case fires", out_text() == "\b\bexact ",
      repr(out_text()))
ok_.clear()
typed("keep ")
pump(0.4)
check("B0: nothing is erased and the space is not swallowed",
      out_text() == "!" and relayed_downs(SPACE) >= 1, repr(out_text()))
check("hotstrings make no sound by default", beeps == [], beeps)

# ======================================================== #IfWinActive ==
F2, F3, E, Q = K["F2"], K["F3"], K["E"], K["Q"]
win_macros = [
    a.normalize_macro({"code": F2, "mode": "once", "steps": [
        {"op": "tap", "code": E}], "window": {"match": "Sober"}}),
    a.normalize_macro({"code": F3, "mode": "once", "steps": [
        {"op": "tap", "code": Q}], "window": {"match": "Sober",
                                              "not": True}})]
engine.configure(win_macros, True)
pump(0.2)
wins.active_info = {"cls": "org.kde.konsole", "name": "konsole",
                    "title": "bash"}
ok_.clear()
ck.clear()
kbd.tap(F2)
kbd.tap(F3)
pump(0.3)
ok_.poll()
check("in another window: F2 goes to the app, F3 (not in Sober) runs",
      relayed_downs(F2) == 1 and relayed_downs(F3) == 0
      and len(ok_.keys(E, 1)) == 0 and len(ok_.keys(Q, 1)) == 1)
wins.active_info = {"cls": "org.vinegarhq.Sober", "name": "sober",
                    "title": "Roblox"}
ok_.clear()
ck.clear()
kbd.tap(F2)
kbd.tap(F3)
pump(0.3)
ok_.poll()
check("in Sober: F2 runs and is hidden, F3 goes to the game",
      relayed_downs(F2) == 0 and relayed_downs(F3) == 1
      and len(ok_.keys(E, 1)) == 1 and len(ok_.keys(Q, 1)) == 0)
check("claims() follows the window too",
      engine.claims(F2) and not engine.claims(F3))
wins.active_info = None
ok_.clear()
kbd.tap(F2)
pump(0.3)
ok_.poll()
check("window unknown (no KWin): the hotkey works, with one warning",
      len(ok_.keys(E, 1)) == 1 and len(problems) == 1
      and "KDE Plasma" in problems[0], problems)

# =============================================================== sons ==
F4, F6, F9 = K["F4"], K["F6"], K["F9"]
beeps.clear()
snd = [a.normalize_macro({"code": F4, "mode": "toggle", "period_ms": 20,
                          "steps": [{"op": "tap", "code": E}]}),
       a.normalize_macro({"code": F6, "mode": "hold", "beep": True,
                          "period_ms": 20,
                          "steps": [{"op": "tap", "code": Q}]}),
       a.normalize_macro({"code": F9, "mode": "once",
                          "steps": [{"op": "tap", "code": Q}]})]
check("the beep key is stored only when it differs from the mode default",
      "beep" not in snd[0] and snd[1]["beep"] is True
      and "beep" not in snd[2])
engine.configure(snd, True)
pump(0.2)
kbd.tap(F4)
pump(0.2)
kbd.tap(F4)
pump(0.2)
check("toggle: two notes up when it starts, down when it stops",
      beeps == ["mon", "moff"], beeps)
beeps.clear()
kbd.key(F6, True)
pump(0.25)
kbd.key(F6, False)
pump(0.2)
check("while held, with sound on: up on press, down on release",
      beeps == ["mon", "moff"], beeps)
beeps.clear()
kbd.tap(F9)
pump(0.2)
check("once: silent by default", beeps == [], beeps)

# ======================================================= scripts no motor ==
def imp(text, name):
    path = os.path.join(TMP, name)
    with open(path, "w") as fh:
        fh.write(text)
    return a.import_ahk_file(path)


i = imp("#MaxThreadsPerHotkey 2\nF8::\ntoggle := !toggle\nn := 0\n"
        "while (toggle) {\n  n++\n  Send, %n%\n  Sleep, 40\n}\nreturn\n",
        "threads.ahk")
m = i.macros[0]
check("a while toggle with Send %n% becomes code with 2 threads",
      m["steps"][0]["op"] == "script" and m["steps"][0].get("threads") == 2
      and m.get("beep") is True, m["steps"][0].get("threads"))
engine.configure(i.macros, True)
pump(0.2)
beeps.clear()
ok_.clear()
F8 = K["F8"]
kbd.tap(F8)
pump(0.5)
kbd.tap(F8)
pump(0.3)
got = out_text()
n_after = len(got)
pump(0.2)
check("F8 counts 1, 2, 3... and the second F8 stops it (#MaxThreads 2)",
      got.startswith("1234") and len(out_text()) == n_after
      and not engine.running(), repr(got))
check("and it sounds up, then down when the loop ends",
      beeps == ["mon", "moff"], beeps)

i = imp("F7::\ntoggle := !toggle\nSetTimer, Spam, % toggle ? 20 : \"Off\"\n"
        "return\nSpam:\nSend {Q}\nreturn\n", "timer.ahk")
m = i.macros[0]
check("SetTimer with a ternary stays a native toggle macro",
      m["mode"] == "toggle" and m["steps"][0]["op"] in ("tap", "text"),
      m["steps"])
i = imp("F7::\ncount := 0\ntoggle := !toggle\nSetTimer, Spam, % toggle ? 20 : "
        "\"Off\"\nreturn\nSpam:\ncount += 1\nif (count > 1000)\n  count := 0\n"
        "Send {Q}\nreturn\n", "timer2.ahk")
m = i.macros[0]
check("a timer label with math becomes code",
      m["steps"][0]["op"] == "script", [s["op"] for s in m["steps"]])
engine.configure(i.macros, True)
pump(0.2)
beeps.clear()
ok_.clear()
F7 = K["F7"]
kbd.tap(F7)
pump(0.4)
kbd.tap(F7)
pump(0.2)
ok_.poll()
nq = len(ok_.keys(Q, 1))
pump(0.2)
ok_.poll()
check("SetTimer from code: Q every 20 ms, F7 again stops it",
      8 <= nq <= 22 and len(ok_.keys(Q, 1)) == nq and not engine.running(),
      f"{nq} Q")
check("the timer sounds up when it starts and down when it stops",
      beeps == ["mon", "moff"], beeps)
engine.configure(i.macros, False)

engine.close()
vk.close()
vm.close()
kbd.close()
sys.exit(h.finish())
