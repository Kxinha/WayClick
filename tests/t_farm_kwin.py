"""O que a 1.8.0 pergunta ao KWin, contra um KWin de verdade (aninhado e
isolado, ver kwin_nested.py): achar a janela do jogo pelo nome do app ou pelo
título inteiro, trazer para a frente (mesmo minimizada), saber quando ela
fechou, e onde está o cursor (o gravador). Nenhum device é criado: só o D-Bus
e o Wayland da caixa.

    nice -n 19 python3 -B tests/t_farm_kwin.py
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


# uma janela Qt com app id e título escolhidos: o "jogo" e o "navegador"
TARGET = r"""
import sys
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication(sys.argv[:1])
QGuiApplication.setDesktopFileName(sys.argv[1])
w = QWidget()
w.setWindowTitle(sys.argv[2])
w.resize(480, 320)
w.show()
app.exec()
"""

# a ponte do WayClick, como o app a usa, dentro da caixa
PROBE = r"""
import json, os, sys, threading, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import wayclick as a
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
kw = a.KWinBridge()
out = {"ok": kw.ok, "error": kw.error, "res": []}
for op, args in json.loads(sys.argv[2]):
    t0 = time.perf_counter()
    if op == "find":
        r = kw.find(*args)
    elif op == "cursor":
        r = kw.cursor()
    elif op == "thread":            # como o MacroRunner, de outra thread
        box = []
        t = threading.Thread(target=lambda: box.append(kw.find(*args)))
        t.start()
        while t.is_alive():
            app.processEvents()
            time.sleep(0.005)
        r = box[0] if box else None
    out["res"].append([op, args, r, round(time.perf_counter() - t0, 3)])
print(json.dumps(out))
"""


def probe(b, *ops):
    r = b.run([sys.executable, "-I", "-B", "-c", PROBE, kn.REPO,
               json.dumps(ops)], timeout=90)
    if not r.get("ok") or r.get("code") != 0:
        raise RuntimeError(f"probe failed: {r}")
    out = json.loads(r["out"].strip().splitlines()[-1])
    if not out["ok"]:
        raise RuntimeError(f"no bridge: {out['error']}")
    return out["res"]


def wins(b):
    return {w["cls"]: w for w in b.kwin.windows()}


def until(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.2)
    return cond()


with kn.Bench(outputs=2) as b:
    for name, app_id, title in (("game", "org.wctest.Sober", "Sober"),
                                ("browser", "org.wctest.Browser",
                                 "Sober download page")):
        b.request(op="spawn", name=name,
                  argv=[sys.executable, "-I", "-B", "-c", TARGET, app_id,
                        title], env={"QT_QPA_PLATFORM": "wayland"})
    up = until(lambda: {"org.wctest.Sober", "org.wctest.Browser"}
               <= set(wins(b)), 30)
    check("two windows are up in the nested KWin", up, str(list(wins(b))))

    res = probe(b, ["find", ["Sober"]], ["find", ["ORG.WCTEST.SOBER"]],
                ["find", ["  sober   download page "]],
                ["find", ["download"]], ["find", ["nothing like it"]],
                ["find", [""]])
    r = [x[2] for x in res]
    check("the app name finds the game, not the window titled about it",
          r[0] and r[0]["found"] and r[0]["cls"] == "org.wctest.Sober",
          str(r[0]))
    check("so does the whole app id, in any case",
          r[1] and r[1]["cls"] == "org.wctest.Sober")
    check("a whole title finds that window, spaces and case aside",
          r[2] and r[2]["found"] and r[2]["cls"] == "org.wctest.Browser",
          str(r[2]))
    check("a piece of a title is not enough",
          r[3] == {"found": False}, str(r[3]))
    check("a name no window has is not found, and an empty one neither",
          r[4] == {"found": False} and r[5] == {"found": False})
    check("each question takes well under a second",
          max(x[3] for x in res) < 1.0, str([x[3] for x in res]))

    # as duas janelas abrem juntas, e a que mapear por último fica ativa;
    # o jogo tem de começar atrás para o foco ter o que provar
    b.kwin.window_js("org.wctest.Browser",
                     "workspace.activeWindow = w; report({});")
    until(lambda: not wins(b)["org.wctest.Sober"]["active"], 5)
    before = wins(b)
    res = probe(b, ["find", ["sober", True]])
    after = wins(b)
    check("focus brings the game to the front",
          not before["org.wctest.Sober"]["active"]
          and after["org.wctest.Sober"]["active"]
          and not after["org.wctest.Browser"]["active"],
          f"before {before['org.wctest.Sober']['active']}, after "
          f"{after['org.wctest.Sober']['active']}")

    b.kwin.window_js("org.wctest.Browser",
                     "workspace.activeWindow = w; report({});")
    b.kwin.window_js("org.wctest.Sober",
                     "w.minimized = true; report({m: w.minimized});")
    mini = wins(b)["org.wctest.Sober"]["minimized"]
    res = probe(b, ["find", ["sober", True]])
    after = wins(b)["org.wctest.Sober"]
    check("even when it was minimized",
          mini and not after["minimized"] and after["active"]
          and res[0][2]["minimized"], str(res[0][2]))

    res = probe(b, ["thread", ["sober"]])
    check("asking from another thread works, as a running macro does",
          res[0][2] and res[0][2]["found"], str(res[0]))

    spots = []
    for x, y in ((100, 200), (2500, 640), (1919, 1079)):
        b.fake.absolute(x, y)
        b.wl.roundtrip()
        time.sleep(0.15)
        spots.append(((x, y), probe(b, ["cursor", []])[0][2],
                      b.kwin.cursor()))
    check("the recorder reads where the cursor is, on either screen",
          all(got == list(want) for want, got, _k in spots),
          str(spots))

    b.request(op="stop", name="game")
    gone = until(lambda: "org.wctest.Sober" not in wins(b), 15)
    res = probe(b, ["find", ["Sober"]], ["find", ["Sober", True]])
    check("when the game closes it is not found, even with a window "
          "titled about it still open", gone and res[0][2] == {"found": False}
          and res[1][2] == {"found": False}, str(res))
    check("and nothing else was brought to the front for it",
          not wins(b).get("org.wctest.Browser", {}).get("minimized"))
    b.request(op="stop", name="browser")

n, bad = len(RESULTS), RESULTS.count(False)
print(f"\n{n - bad}/{n} passed")
sys.exit(1 if bad else 0)
