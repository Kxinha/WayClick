"""O interpretador de AutoHotkey do WayClick, sem tocar no desktop (ver
harness.py): nenhum device é criado aqui. O MacroRunner de teste anota cada
tecla, botão e espera em vez de escrever num uinput, e o "motor" é falso
(teclas físicas, janela ativa e SetTimer de mentira).

    QT_QPA_PLATFORM=offscreen nice -n 19 python3 tests/t_ahk_vm.py
"""
import os, sys, tempfile, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wctest-ahkvm-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "data")
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402
from PySide6.QtCore import QCoreApplication                 # noqa: E402

app = QCoreApplication(sys.argv)
a.LANG = "en_US"
K = a.KEYS


class Env:
    """O que o motor dá a um script: aqui, tudo de mentira."""

    def __init__(self):
        self.down = set()
        self.win = None
        self.said = []
        self.problems = []
        self.suspends = []
        self.beeps = []
        self.workspace = (0, 0, 1920, 1080)
        self.timers = []
        self.screen = None
        self.pointer = None
        self.windows = None

    def key_down(self, code):
        return code in self.down

    def active_window(self, fast=False):
        return self.win

    def say(self, title, text):
        self.said.append(text)

    def report(self, text):
        self.problems.append(text)

    def suspend(self, mode):
        self.suspends.append(mode)

    def beep_now(self, which):
        self.beeps.append(which)

    def start_timer(self, run, label, period, quiet=False):
        st = dict(run.st, label=label)
        st.pop("line", None)
        c = a.CompiledMacro(a.normalize_macro({
            "name": label, "mode": "toggle", "repeat": period > 0,
            "period_ms": abs(period), "press_ms": 1, "steps": [st]}))
        r = Rec(c, self, loop=period > 0)
        r.start()
        self.timers.append((label, period, r))
        return r


class Rec(a.MacroRunner):
    """Anota em vez de apertar; espera de verdade (curta) para os laços."""

    def __init__(self, macro, env, loop=False):
        super().__init__(macro, None, None, loop, False, None, env)
        self.log = []

    def _set(self, code, down):
        self.log.append(("d" if down else "u", code))
        if down:
            self.held.append(code)
        elif code in self.held:
            self.held.remove(code)

    def _step(self, st, i=0):
        if st["op"] not in ("tap", "down", "up", "text", "wait", "script"):
            self.log.append((st["op"], dict(st)))
            return None
        if st["op"] == "wait":
            self.log.append(("wait", st["ms"]))
        return super()._step(st, i)

    def keys(self):
        return [c for k, c in self.log if k == "d"]


def script(src, line=0, label="", v2=None, env=None, hotkey=""):
    env = env or Env()
    st = {"op": "script", "src": src}
    if line:
        st["line"] = line
    if label:
        st["label"] = label
    if v2:
        st["v2"] = True
    if hotkey:
        st["hotkey"] = hotkey
    st = a.normalize_step(st)
    c = a.CompiledMacro(a.normalize_macro({
        "name": "t", "code": K["F1"], "mode": "once", "repeat": False,
        "press_ms": 1, "steps": [st]}))
    r = Rec(c, env)
    r.run()
    return r, env


def val(expr, pre="", v2=False):
    """Valor de uma expressão, por um MsgBox."""
    src = ("#Requires AutoHotkey v2\n" if v2 else "") + pre + \
        ("\nMsgBox(" + expr + ")\n" if v2 else "\nMsgBox % " + expr + "\n")
    r, env = script(src)
    if env.problems:
        return "ERR " + env.problems[0]
    return env.said[-1] if env.said else None


def chars(text):
    return [a.CHAR_KEYS[c][0] for c in text]


# ============================================================ expressões ==
cases = [
    ("1 + 2 * 3", "7"), ("(1 + 2) * 3", "9"), ("2 ** 10", "1024"),
    ("7 // 2", "3"), ("-7 // 2", "-3"), ("10 / 4", "2.5"), ("10 / 5", "2"),
    ("\"a\" . \"b\"", "ab"), ("\"a\" \"b\"", "ab"), ("5 > 3", "1"),
    ("\"abc\" = \"ABC\"", "1"), ("\"abc\" == \"ABC\"", "0"),
    ("!0", "1"), ("not 1", "0"), ("1 && 0", "0"), ("0 || 1", "1"),
    ("3 > 2 ? \"y\" : \"n\"", "y"), ("0x10 + 1", "17"), ("1 << 4", "16"),
    ("6 & 3", "2"), ("6 | 3", "7"), ("6 ^ 3", "5"), ("~0", "-1"),
    ("Mod(10, 3)", "1"), ("Abs(-4)", "4"), ("Round(2.567, 2)", "2.57"),
    ("Floor(2.9)", "2"), ("Ceil(2.1)", "3"), ("Max(1, 9, 3)", "9"),
    ("StrLen(\"hello\")", "5"), ("SubStr(\"hello\", 2, 3)", "ell"),
    ("InStr(\"hello\", \"L\")", "3"), ("StrReplace(\"a-b-c\", \"-\", \"+\")",
                                       "a+b+c"),
    ("Format(\"{1}x{2}\", 3, 4)", "3x4"), ("Chr(65) Ord(\"B\")", "A66"),
    ("RegExMatch(\"abc123\", \"\\d+\")", "4"),
    ("RegExReplace(\"a1b2\", \"\\d\", \"#\")", "a#b#"),
    ("Trim(\"  x  \")", "x"), ("\"x\" A_Space \"y\"", "x y"),
]
bad = [(e, want, got) for e, want in cases
       if (got := val(e)) != want]
check(f"{len(cases)} expressions give AutoHotkey's values", not bad, bad)
check("v2: / always gives a float", val("10 / 5", v2=True) == "2.0",
      val("10 / 5", v2=True))
check("v2: strings with backtick escapes",
      val("\"a`tb\"", v2=True) == "a\tb", repr(val("\"a`tb\"", v2=True)))

# ============================================== variáveis e controle ==
check("v1 x = text, %x% deref and EnvAdd",
      val("y", "x = 5\ny := x + 1\nEnvAdd, y, 10") == "16")
check("if/else if/else",
      val("r", "n := 7\nif (n < 5)\n r := \"a\"\nelse if (n < 10)\n"
          " r := \"b\"\nelse\n r := \"c\"") == "b")
check("legacy v1 if var = text",
      val("r", "s = D\nif s = d\n  r := 1\nelse\n  r := 2") == "1")
check("Loop N with A_Index",
      val("t", "t := 0\nLoop 5\n  t += A_Index") == "15")
check("Loop with break and until",
      val("i", "i := 0\nLoop {\n  i++\n  if (i = 4)\n    break\n}") == "4"
      and val("i", "i := 0\nLoop {\n  i++\n} until i >= 6") == "6")
check("while with continue",
      val("s", "s := \"\"\ni := 0\nwhile (i < 5) {\n  i++\n"
          "  if (Mod(i, 2) = 0)\n    continue\n  s .= i\n}") == "135")
check("functions: params, default, return, recursion",
      val("Fact(5) Add(2)", "Fact(n) {\n  if (n <= 1)\n    return 1\n"
          "  return n * Fact(n - 1)\n}\nAdd(a, b := 10) {\n"
          "  return a + b\n}") == "12012")
check("ByRef and v2 &ref",
      val("x", "Inc(ByRef v) {\n  v += 1\n}\nx := 1\nInc(x)\nInc(x)") == "3"
      and val("x", "Inc(&v) {\n  v += 1\n}\nx := 1\nInc(&x)", v2=True)
      == "2")
check("globals: declared, and read from a function",
      val("g", "g := 1\nSetG() {\n  global g\n  g := 42\n}\nSetG()") == "42"
      and val("Get()", "k := 9\nGet() {\n  return k\n}") == "9")
check("local stays local",
      val("q", "q := 1\nF() {\n  q := 5\n}\nF()") == "1")
check("static keeps its value between calls",
      val("C() C() C()", "C() {\n  static n := 0\n  n++\n  return n\n}")
      == "123")
check("try/catch takes the error",
      val("r", "r := 0\ntry {\n  x := 1 / 0\n} catch {\n  r := 1\n}")
      == "1")
check("Gosub runs a label",
      val("z", "z := 0\nGosub, Bump\nGosub, Bump\nMsgBox % z\nreturn\n"
          "Bump:\nz += 2\nreturn\n") == "4")

# ========================================================== atalhos ==
SRC = """toggle := 0
count := 0
F1::
count++
Send, {Space}x
return

F2::
toggle := !toggle
if (toggle)
    Send a
else
    Send b
return

F3::
Loop 3
    Send % "k" A_Index
return

^j::Send ^c
"""
lines = {t.split("::")[0]: n for n, t in enumerate(SRC.splitlines(), 1)
         if "::" in t}
r, env = script(SRC, line=lines["F1"])
check("F1 runs only its own body: Space then X",
      r.keys() == [K["Space"], K["X"]], r.keys())
r2, _e = script(SRC, line=lines["F2"])
r3, _e = script(SRC, line=lines["F2"])
check("globals last from one press to the next (toggle)",
      r2.keys() == [K["A"]] and r3.keys() == [K["B"]],
      (r2.keys(), r3.keys()))
r, _e = script(SRC, line=lines["F3"])
check("Send % expression with A_Index",
      r.keys() == [K["K"], K["1"], K["K"], K["2"], K["K"], K["3"]],
      r.keys())
r, _e = script(SRC, line=lines["^j"])
check("Send ^c holds Ctrl around C",
      r.log[:4] == [("d", K["Left Ctrl"]), ("d", K["C"]), ("u", K["C"]),
                    ("u", K["Left Ctrl"])], r.log)

V2 = """#Requires AutoHotkey v2
n := 0
F5:: {
    global n
    n += 1
    Send "{Tab " n "}"
}
"""
ln5 = [i for i, t in enumerate(V2.splitlines(), 1) if t.startswith("F5")][0]
r1, _e = script(V2, line=ln5)
r2, _e = script(V2, line=ln5)
check("v2 block hotkey with global and Send expression: Tab, then Tab x2",
      r1.keys() == [K["Tab"]] and r2.keys() == [K["Tab"], K["Tab"]],
      (r1.keys(), r2.keys()))

# ===================================================== teclas físicas ==
env = Env()
env.down = {K["Space"]}
src = ("*Space::\nwhile GetKeyState(\"Space\", \"P\") {\n  Send {F9}\n"
       "  Sleep 20\n}\nreturn\n")


def release():
    time.sleep(0.25)
    env.down.clear()


threading.Thread(target=release, daemon=True).start()
t0 = time.perf_counter()
r, env = script(src, line=1, env=env)
dt = time.perf_counter() - t0
n = r.keys().count(K["F9"])
check("while GetKeyState(P) spams until the key is let go",
      5 <= n <= 14 and 0.2 < dt < 0.6, f"{n} F9 in {dt:.2f} s")
env = Env()
r, env = script("GetKeyState, st, LButton\nif st = U\n  Send u\nelse\n"
                "  Send d\nreturn", env=env)
check("v1 GetKeyState command gives U and D", r.keys() == [K["U"]],
      r.keys())
env = Env()
env.down = {K["E"]}
threading.Timer(0.15, env.down.clear).start()
t0 = time.perf_counter()
r, env = script("KeyWait, E\nSend z", env=env)
check("KeyWait waits for the release",
      r.keys() == [K["Z"]] and time.perf_counter() - t0 >= 0.13)
env = Env()
r, env = script("KeyWait, E, D T0.1\nif ErrorLevel\n  Send t", env=env)
check("KeyWait D with a timeout sets ErrorLevel", r.keys() == [K["T"]],
      r.keys())

# ============================================================ janelas ==
env = Env()
env.win = {"cls": "org.vinegarhq.Sober", "name": "sober",
           "title": "Roblox"}
r, env = script("if WinActive(\"ahk_exe RobloxPlayerBeta.exe\")\n  Send y\n"
                "else\n  Send n", env=env)
check("WinActive(ahk_exe RobloxPlayerBeta.exe) finds Sober",
      r.keys() == [K["Y"]], r.keys())
env.win = {"cls": "org.kde.konsole", "name": "konsole", "title": "bash"}
r, env = script("IfWinActive, ahk_exe RobloxPlayerBeta.exe\n  Send y\n"
                "else\n  Send n", env=env)
check("IfWinActive is false in another window", r.keys() == [K["N"]],
      r.keys())
check("ahk_win_match: exe, class, title, id",
      a.ahk_win_match("ahk_exe C:\\Games\\game.exe")["match"] == "game"
      and a.ahk_win_match("ahk_class Notepad")["how"] == "class"
      and a.ahk_win_match("Untitled - Notepad")["match"]
      == "Untitled - Notepad"
      and a.ahk_win_match("ahk_id 0x1234") is None)

# ========================================================== SetTimer ==
env = Env()
src = ("F1::\ntoggle := !toggle\nSetTimer, Spam, % toggle ? 30 : \"Off\"\n"
       "return\nSpam:\nSend e\nreturn\n")
script(src, line=1, env=env)
time.sleep(0.2)
label, period, tr = env.timers[0]
script(src, line=1, env=env)
tr.join(1.0)
n = tr.keys().count(K["E"])
check("SetTimer toggle: starts the label every 30 ms, Off stops it",
      label == "spam" and period == 30 and 3 <= n <= 8 and not tr.is_alive(),
      f"{label} {period} {n} alive={tr.is_alive()}")

# ===================================================== avisos e erros ==
r, env = script("F1::\nx := [1, 2]\nreturn\n", line=1)
check("arrays stop the macro with the line in the message",
      env.problems and "line 2" in env.problems[0], env.problems)
check("ahk_check: a broken hotkey does not hide the good one",
      a.ahk_check("F1::\nx := [1]\nreturn\nF2::\nSend a\nreturn\n", line=4)
      is None and a.ahk_check("F1::\nx := [1]\nreturn\n", line=1)
      is not None)
check("ahk_check: a broken function breaks the hotkey that calls it",
      a.ahk_check("F1::\nBad()\nreturn\nBad() {\n  for k in x\n"
                  "    Send a\n}\n", line=1) is not None)
env = Env()
st = a.normalize_step({"op": "script", "src": "Loop {\n  n++\n}"})
c = a.CompiledMacro(a.normalize_macro({"name": "t", "code": K["F1"],
                                       "mode": "once", "steps": [st]}))
r = Rec(c, env)
r.start()
time.sleep(0.15)
r.stop()
r.join(1.0)
check("an endless loop without Sleep stops at once with the macro",
      not r.is_alive())
env = Env()
r, env = script("Suspend, Toggle\nSoundBeep\nExitApp\nSend x", env=env)
check("Suspend, SoundBeep and ExitApp reach the engine",
      env.suspends == ["toggle"] and env.beeps == ["on"]
      and r.keys() == [], (env.suspends, env.beeps, r.keys()))

# ================================================== importação ==
def imp(text, name="t.ahk"):
    path = os.path.join(TMP, name)
    with open(path, "w") as fh:
        fh.write(text)
    return a.import_ahk_file(path)


i = imp("F1::\nx := x + 1\nSend, %x%\nreturn\nF2::Send a\n")
m1, m2 = i.macros
check("a hotkey with math becomes one AutoHotkey code step",
      m1["steps"][0]["op"] == "script" and m1["steps"][0]["line"] == 1
      and m2["steps"][0]["op"] == "tap",
      [s["op"] for s in m1["steps"] + m2["steps"]])
check("and the report says it runs on the interpreter",
      any("interpreter" in a.note_text(n) for n in i.notes),
      [a.note_text(n) for n in i.notes])
i = imp("#MaxThreadsPerHotkey 2\nF1::\ntoggle := !toggle\n"
        "while (toggle) {\n  Send, e\n  Sleep, 50\n}\nreturn\n")
m = i.macros[0]
check("#MaxThreadsPerHotkey 2 reaches the step",
      m["steps"][0].get("threads") == 2 or m["mode"] == "toggle",
      m)
i = imp("#IfWinActive ahk_exe RobloxPlayerBeta.exe\nF1::Send a\n"
        "#IfWinActive\nF2::Send b\n#HotIf !WinActive(\"Discord\")\n"
        "F3::Send c\n#HotIf\n")
w = [m.get("window") for m in i.macros]
check("#IfWinActive and #HotIf become window conditions",
      w == [{"match": "Sober"}, None, {"match": "Discord", "not": True}], w)
i = imp("::btw::by the way\n:*:@@::someone@example.com\n"
        ":C:BTW::By The Way\n::sig::\nSend, Regards{Enter}\nreturn\n")
hs = [(m["hotstring"]["abbr"], m["hotstring"]["opts"],
       [s["op"] for s in m["steps"]]) for m in i.macros]
check("hotstrings: text, *, C and an action body",
      hs == [("btw", [], ["text"]), ("@@", ["*"], ["text"]),
             ("BTW", ["C"], ["text"]), ("sig", [], ["text", "tap"])]
      and not [n for n in i.notes if "hotstring" in a.note_text(n)], hs)
check("hotkey_label shows ::btw", a.hotkey_label(i.macros[0]) == "::btw")
i = imp("F1::\nGui, Add, Text,, hi\nGui, Show\nreturn\nF2::\nx := [1, 2]\n"
        "Send % x[1]\nreturn\nF3::Send a\n")
notes = [a.note_text(n) for n in i.notes]
check("a command the interpreter lacks (Gui) is reported at import, not "
      "turned into code that fails later",
      [m["key"] for m in i.macros] == ["F3"]
      and any("Gui" in n for n in notes), notes)
check("Send % x[1] with an array is skipped with the reason, never typed "
      "as the text x[1]",
      any("F2" in n and "arrays" in n for n in notes), notes)

sys.exit(h.finish())
