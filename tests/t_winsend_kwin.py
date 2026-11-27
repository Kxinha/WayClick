"""Mandar tecla para várias janelas, de ponta a ponta, num KWin de verdade
(aninhado e isolado, ver kwin_nested.py), com Xwayland.

Na caixa: a janela de quem usa o computador (com o foco, onde o teclado do
teste digita o tempo todo), dois KWrite (o bloco de notas, um Wayland e um
X11), quatro "jogos" de cor própria (Wayland minimizado, Wayland coberto,
Wayland em outra área de trabalho, X11 minimizado) e o WayClick de verdade
com a janela dele, com uma linha por janela. Confere que cada janela recebe
só a tecla dela, no intervalo dela; que tudo o que se digita chega, na
ordem, só na janela de quem digita; que o foco, a área de trabalho, a ordem
das janelas e a minimizada voltam como estavam; e que nenhuma janela
escondida aparece na tela (capturas o tempo todo, contando os pixels das
cores dos jogos). Depois a Doca (a tecla vai lá dentro, o foco daqui nem
pisca) e uma janela que um WayClick que caiu deixou invisível.

Os devices virtuais do WayClick são capturados ao nascer e vão só para o
KWin aninhado; o teclado do teste (wctest-kbd) só aperta com o WayClick da
caixa segurando ele com EVIOCGRAB (a prova dá EBUSY).

    nice -n 19 python3 -B tests/t_winsend_kwin.py
    WCTEST_SHOTS=/pasta nice -n 19 python3 -B tests/t_winsend_kwin.py
"""
import json, os, signal, statistics, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
UID = os.getuid()
BOX_HOME = "/home/wctest"
REC = BOX_HOME + "/rec"
K_A, K_B, K_E, K_Q, K_W, SPACE, CTRL, K_S = 30, 48, 18, 16, 17, 57, 29, 31
# o que "quem usa o computador" digita: nenhuma letra das teclas dos alvos
TYPED = "zxcvnmlkjhgfdsrtyuiop"

LOGGER = r"""
import json, sys, time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel
log = open(sys.argv[1], "a", buffering=1)
title, appid, color = sys.argv[2], sys.argv[3], sys.argv[4]
app = QApplication(sys.argv[:1])
QGuiApplication.setDesktopFileName(appid)


class W(QLabel):
    def note(s, **kw):
        kw["t"] = round(time.monotonic(), 6)
        log.write(json.dumps(kw) + "\n")

    def keyPressEvent(s, e):
        s.note(k="key", code=e.nativeScanCode() - 8, down=1,
               rep=e.isAutoRepeat())

    def keyReleaseEvent(s, e):
        s.note(k="key", code=e.nativeScanCode() - 8, down=0,
               rep=e.isAutoRepeat())

    def focusInEvent(s, e):
        s.note(k="focus", on=1)

    def focusOutEvent(s, e):
        s.note(k="focus", on=0)


w = W("")
w.setWindowTitle(title)
w.setFocusPolicy(Qt.StrongFocus)
w.setAutoFillBackground(True)
w.setStyleSheet("background-color: %s;" % color)
w.resize(500, 300)
w.show()
QTimer.singleShot(0, lambda: w.note(k="ready"))
app.exec()
"""

# os jogos, cada um de uma cor que nada mais na tela tem
GAMES = {"Game min": ("org.wctest.GameMin", (255, 0, 255), "wayland"),
         "Game covered": ("org.wctest.GameCov", (0, 255, 255), "wayland"),
         "Game desk2": ("org.wctest.GameDesk", (255, 255, 0), "wayland"),
         "X game": ("org.wctest.XGame", (255, 128, 0), "xcb")}
USER = ("User window", "org.wctest.User", (60, 60, 70))
DOCKG = ("Dock game", "org.wctest.DockGame", (40, 120, 40))


# ===================================================== dentro da caixa ==
def _log(kind, **kw):
    kw.update(k=kind, t=round(time.monotonic(), 6))
    with open(os.path.join(REC, "events.jsonl"), "a") as fh:
        fh.write(json.dumps(kw) + "\n")


def wayclick_main(bridge_sock):
    """O WayClick de verdade na caixa. Recebe pedidos do teste por
    rec/cmd.jsonl (uma linha por pedido) e responde em events.jsonl."""
    import harness as h
    import kwin_nested as K
    a = h.a
    a.NEW_NODE_HOOK = K.wayclick_hook(bridge_sock)
    os.makedirs(REC, exist_ok=True)
    if os.environ.get("WCTEST_DUMP"):
        import faulthandler
        faulthandler.dump_traceback_later(
            float(os.environ["WCTEST_DUMP"]), repeat=True,
            file=open(os.path.join(REC, "dump.txt"), "w"))
    held_path = os.path.join(REC, "held.json")
    cmd_path = os.path.join(REC, "cmd.jsonl")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication(["wayclick"])
    w = a.App()
    w.sound.setChecked(False)
    w.tabs.setCurrentIndex(3)           # a aba Janela
    beat = [0.0]
    try:                                # pedidos de um WayClick anterior
        with open(cmd_path) as fh:
            done = [len(fh.read().splitlines())]
    except OSError:
        done = [0]

    def rows():
        out = []
        for r in w.win_rows:
            d = r.data() or {}
            out.append({"title": d.get("title"), "cls": d.get("cls"),
                        "way": r.mark, "xid": bool(d.get("xid")),
                        "state": r.state.text(),
                        "items": [r.win.itemText(i)
                                  for i in range(r.win.count())]})
        return out

    def sender(s=None):
        s = s or w.win_sender
        if s is None:
            return None
        return {"rounds": s.rounds, "flash_ms": s.flash_ms,
                "log": list(s.round_log),
                "targets": [{k: t.get(k) for k in
                             ("row", "kind", "title", "hits", "error", "ms")}
                            for t in s.targets]}

    def do(c):
        op = c.get("op")
        if op == "rows":
            # uma linha por pedido, escolhendo a janela pelo título
            while len(w.win_rows) > 1:
                w._remove_win_row(w.win_rows[-1])
            w.refresh_windows()
            for i, spec in enumerate(c["rows"]):
                row = w.win_rows[0] if i == 0 else w._add_win_row()
                idx = next((j for j in range(row.win.count())
                            if spec["match"] in row.win.itemText(j)), -1)
                row.win.setCurrentIndex(idx)
                row.catcher.set_key(spec["code"], spec["key"])
                row.every.setValue(spec["every"])
            return {"rows": rows()}
        if op == "start":
            w.target_box.setChecked(True)
            w.set_running(True)
            return {"on": w.win_sender is not None}
        if op == "stop":
            s = w.win_sender
            w.set_running(False)
            w.target_box.setChecked(False)
            end = time.monotonic() + 5
            while s is not None and s.is_alive() and time.monotonic() < end:
                app.processEvents()
                s.join(0.01)
            return {"sender": sender(s), "alive": bool(s and s.is_alive())}
        if op == "state":
            return {"sender": sender(), "rows": rows(),
                    "status": w.status.text()}
        if op == "dock":
            w.dock.size = (800, 600)
            w.dock.command = c["command"]
            ok = w.dock.start(launch=True)
            return {"ok": ok, "error": w.dock.error}
        if op == "dock_windows":
            kw = w.dock.windows
            return {"wins": kw.windows() if kw is not None and kw.ok else []}
        if op == "dock_stop":
            w.dock.stop()
            return {}
        return {"error": "unknown op"}

    def tick():
        now = time.monotonic()
        try:
            with open(cmd_path) as fh:
                lines = fh.read().splitlines()
        except OSError:
            lines = []
        for line in lines[done[0]:]:
            done[0] += 1
            try:
                c = json.loads(line)
                _log("answer", n=c.get("n"), **do(c))
            except Exception as e:              # noqa: BLE001
                _log("answer", n=done[0], error=repr(e))
        if now - beat[0] < 0.2:
            return
        beat[0] = now
        hub = w.watcher.hub
        with hub.lock:
            held = sorted(p for p, d in hub.devs.items()
                          if d.grabbed and not d.dead)
        with open(held_path + ".tmp", "w") as fh:
            json.dump({"t": now, "held": held}, fh)
        os.replace(held_path + ".tmp", held_path)

    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(tick)
    timer.start()

    def quitting():
        timer.stop()
        try:
            os.remove(held_path)
        except OSError:
            pass
    app.aboutToQuit.connect(quitting)
    app.aboutToQuit.connect(w.cleanup)
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    w.show()
    QTimer.singleShot(0, lambda: _log("ready"))
    sys.exit(app.exec())


if __name__ == "__main__" and sys.argv[1:2] == ["--wayclick"]:
    wayclick_main(sys.argv[2])
    sys.exit(0)


# ============================================================ o teste ==
import kwin_nested as kn                                    # noqa: E402

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail
                                                     else ""))
    sys.stdout.flush()


def jsonl(path):
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def until(cond, seconds, step=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return cond()


STATE_JS = """
var o = {}; var ws = workspace.stackingOrder;
var order = [];
for (var i = 0; i < ws.length; i++) { var w = ws[i];
  if (!w.normalWindow) continue;
  order.push(w.caption);
  var d = [];
  for (var j = 0; j < w.desktops.length; j++) d.push(w.desktops[j].id);
  o[w.caption] = {min: w.minimized, opa: w.opacity, all: w.onAllDesktops,
                  desk: d, active: w.active};
}
report({wins: o, order: order, cur: workspace.currentDesktop.id});"""


def main():
    import harness as h
    from t_sober_kwin import Keyboard
    import numpy as np
    shots = os.environ.get("WCTEST_SHOTS") or "/tmp/wayclick-winsend-kwin"
    os.makedirs(shots, exist_ok=True)
    with kn.Bench(size="1920x1080", outputs=1, xwayland=True) as b:
        disp = b.display()
        check("the box KWin has its own Xwayland", bool(disp), str(disp))
        with open(os.path.join(b.home, "logger.py"), "w") as fh:
            fh.write(LOGGER)
        cfg = os.path.join(b.home, ".config")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "wayclick.json"), "w") as fh:
            json.dump({"sound": False, "macro_sound": False,
                       "language": "en_US", "delay": 0,
                       "click_enabled": False}, fh)
        evlog = os.path.join(b.home, "rec", "events.jsonl")
        cmdlog = os.path.join(b.home, "rec", "cmd.jsonl")
        os.makedirs(os.path.dirname(evlog), exist_ok=True)
        nreq = [0]

        def ask(timeout=20, **c):
            nreq[0] += 1
            c["n"] = nreq[0]
            with open(cmdlog, "a") as fh:
                fh.write(json.dumps(c) + "\n")
            r = until(lambda: [e for e in jsonl(evlog)
                               if e["k"] == "answer" and e.get("n") == c["n"]],
                      timeout)
            return r[-1] if r else {}

        def logpath(title):
            return os.path.join(b.home, title.replace(" ", "_") + ".log")

        def spawn_logger(title, appid, color, qpa="wayland", env=None):
            e = dict(env or {})
            if qpa == "xcb":
                e.update(QT_QPA_PLATFORM="xcb", DISPLAY=disp)
            b.request(op="spawn", name=title.replace(" ", "_"), argv=[
                "python3", "-I", "-B", BOX_HOME + "/logger.py",
                BOX_HOME + "/" + title.replace(" ", "_") + ".log", title,
                appid, "rgb(%d,%d,%d)" % color], env=e)

        def keys(title, down=1):
            return [e["code"] for e in jsonl(logpath(title))
                    if e["k"] == "key" and e["down"] == down
                    and not e.get("rep")]

        spawn_logger(*USER)
        for title, (appid, color, qpa) in GAMES.items():
            spawn_logger(title, appid, color, qpa)
        for n in ("wl", "x11"):
            open(os.path.join(b.home, f"notes-{n}.txt"), "w").close()
        b.request(op="spawn", name="kw-wl", argv=[
            "kwrite", BOX_HOME + "/notes-wl.txt"])
        b.request(op="spawn", name="kw-x11", argv=[
            "kwrite", BOX_HOME + "/notes-x11.txt"],
            env={"QT_QPA_PLATFORM": "xcb", "DISPLAY": disp})
        want = [USER[0], "notes-wl", "notes-x11"] + list(GAMES)
        wins = until(lambda: (lambda ws: ws if all(
            any(x in c["caption"] for c in ws) for x in want) else None)(
                b.kwin.windows()), 60, 0.3)
        check("the user window, two KWrite (Wayland and X11) and four games "
              "open", bool(wins), str([c["caption"] for c in
                                       (wins or b.kwin.windows())]))
        b.kwin.script("workspace.createDesktop(1, 'two'); report({});")
        place = {USER[0]: (0, 0, 900, 700),
                 "Game covered": (60, 80, 500, 300),
                 "notes-wl": (920, 0, 480, 340),
                 "notes-x11": (920, 360, 480, 340),
                 "Game min": (100, 720, 400, 300),
                 "Game desk2": (500, 720, 400, 300),
                 "X game": (950, 720, 400, 300)}
        for m, (x, y, ww, hh) in place.items():
            b.kwin.window_js(m, f"w.frameGeometry = {{x: {x}, y: {y}, "
                             f"width: {ww}, height: {hh}}}; report({{}});")
        b.kwin.window_js("Game min", "w.minimized = true; report({});")
        b.kwin.window_js("X game", "w.minimized = true; report({});")
        b.kwin.window_js("Game desk2", "w.desktops = [workspace.desktops[1]];"
                         " report({});")
        b.kwin.window_js("Game covered", "workspace.activeWindow = w; "
                         "report({});")
        time.sleep(0.3)
        kb = Keyboard(h, os.path.join(b.home, "rec", "held.json"))
        try:
            kb.wait_free()
            r = b.request(op="spawn", name="wayclick", argv=[
                "python3", "-X", "faulthandler", "-I", "-B",
                os.path.abspath(__file__),
                "--wayclick", f"/run/user/{UID}/wctest-bridge.sock"],
                env={"DISPLAY": disp})
            ready = until(lambda: [e for e in jsonl(evlog)
                                   if e["k"] == "ready"], 40)
            check("WayClick opens in the box", r.get("ok") and ready,
                  "" if ready else b.tail("wayclick", 3000))
            until(lambda: any(x.get("caption") == "WayClick"
                              for x in b.kwin.windows()), 15, 0.2)
            b.kwin.window_js("WayClick", "w.frameGeometry = {x: 1410, y: 0,"
                             " width: 510, height: 1080}; report({});")
            kb.wait_grabbed()
            rows = [
                {"match": "notes-wl", "key": "A", "code": K_A, "every": 1.0},
                {"match": "notes-x11", "key": "B", "code": K_B, "every": 0.4},
                {"match": "Game min", "key": "Space", "code": SPACE,
                 "every": 1.5},
                {"match": "Game covered", "key": "E", "code": K_E,
                 "every": 1.5},
                {"match": "Game desk2", "key": "Q", "code": K_Q,
                 "every": 2.0},
                {"match": "X game", "key": "Space", "code": SPACE,
                 "every": 0.3},
                {"match": "Game min", "key": "W", "code": K_W,
                 "every": 1.5}]
            ans = ask(op="rows", rows=rows)
            got = ans.get("rows") or []
            check("seven rows, each on its window",
                  len(got) == 7 and all(
                      rows[i]["match"] in (g["title"] or "")
                      for i, g in enumerate(got)),
                  str([(g["title"], g["way"]) for g in got]))
            check("X11 windows are found in the X of the box (⌨), Wayland "
                  "ones borrow the focus (◐), even two KWrite of one app",
                  [g["way"] for g in got] == ["◐", "⌨", "◐", "◐", "◐", "⌨",
                                              "◐"],
                  str([(g["title"], g["way"], g["xid"]) for g in got]))
            check("WayClick does not list its own window",
                  not any("WayClick" in it for it in got[0]["items"])
                  if got else False, str(got[0]["items"] if got else ""))

            b.kwin.window_js(USER[0], "workspace.activeWindow = w; "
                             "report({});")
            time.sleep(0.5)
            before = b.kwin.script(STATE_JS)[0]
            colors = [np.array(c, np.int16) for _a, c, _q in GAMES.values()]

            def hidden_px(rgb):
                img = rgb.astype(np.int16)
                return int(sum(int((np.abs(img - c).sum(2) < 12).sum())
                               for c in colors))
            _m, base = b.kwin.screenshot()
            kn.save_png(base, os.path.join(shots, "before.png"))
            check("the games are out of sight before (minimized, covered, "
                  "other desktop)", hidden_px(base) == 0, str(hidden_px(base)))
            user0 = len(jsonl(logpath(USER[0])))
            marks = {t: len(jsonl(logpath(t))) for t in GAMES}
            # capturas o tempo todo, numa thread
            stop_shots = threading.Event()
            seen = {"n": 0, "max": 0, "worst": None}

            def shooter():
                while not stop_shots.is_set():
                    try:
                        _mm, img = b.kwin.screenshot()
                    except Exception:               # noqa: BLE001
                        continue
                    px = hidden_px(img)
                    seen["n"] += 1
                    if px > seen["max"]:
                        seen["max"], seen["worst"] = px, img
            th = threading.Thread(target=shooter, daemon=True)
            t_start = time.monotonic()
            ans = ask(op="start")
            check("Start turns the sending on", ans.get("on"), str(ans))
            th.start()
            typed = []
            for _rep in range(3):
                for ch in TYPED:
                    code = h.a.KEYS[ch.upper()]
                    kb.tap(code, 0.04, 0.06)
                    typed.append(code)
            time.sleep(1.5)
            st = ask(op="state")
            el = time.monotonic() - t_start
            ans = ask(op="stop")
            time.sleep(0.8)
            stop_shots.set()
            th.join(5)
            snd = ans.get("sender") or {}
            tg = snd.get("targets") or []
            hits = [t.get("hits", 0) for t in tg]
            errs = [t.get("error") for t in tg if t.get("error")]
            exp = [el / r_["every"] for r_ in rows]
            check("every row sent its key, at its own interval, without "
                  "errors", len(hits) == 7 and not errs and all(
                      abs(hits[i] - exp[i]) <= 2 + 0.1 * exp[i]
                      for i in range(7)),
                  f"{hits} expected {[round(x, 1) for x in exp]} "
                  f"in {el:.1f} s {errs}")
            lg = snd.get("log") or []
            for kind in ("focus", "x11"):
                ms = [m for k_, _n, m in lg if k_ == kind]
                by_n = {}
                for k_, n_, m in lg:
                    if k_ == kind:
                        by_n.setdefault(n_, []).append(m)
                print(f"      {kind} rounds: " + ", ".join(
                    f"{n_} rows {statistics.median(v):.0f} ms median "
                    f"(max {max(v):.0f}, {len(v)}x)"
                    for n_, v in sorted(by_n.items())))
            check("X11 rows run in their own lane: Space every 0.3 s to the "
                  "X game while the focus rounds go on",
                  hits[5] >= 0.8 * exp[5], f"{hits[5]} of {exp[5]:.1f}")
            check("the row states and the status line counted",
                  st.get("rows") and all(g["state"].startswith("×")
                                         for g in st["rows"])
                  and "×" in (st.get("status") or ""),
                  f"{[g['state'] for g in st.get('rows') or []]} "
                  f"{st.get('status')}")
            fl = snd.get("flash_ms") or 0
            check("a borrowed focus round (5 Wayland rows, 4 windows) takes "
                  "well under a second", 0 < fl < 900, f"{fl:.0f} ms")
            ulog = jsonl(logpath(USER[0]))[user0:]
            udown = [e["code"] for e in ulog if e["k"] == "key"
                     and e["down"] == 1 and not e.get("rep")]
            check("everything typed meanwhile reached the user window, "
                  "once each and in order", udown == typed,
                  f"{len(udown)}/{len(typed)} "
                  f"{[c for c in udown if c not in typed][:8]}")
            outs = [e for e in ulog if e["k"] == "focus"]
            check("the user window lost the focus once a round and got it "
                  "back each time", outs and outs[-1]["on"] == 1
                  and sum(1 for e in outs if e["on"] == 0)
                  == sum(1 for e in outs if e["on"] == 1),
                  f"{len(outs)} focus events, rounds {snd.get('rounds')}")
            # tecla de quem digita guardada: o atraso máximo
            tdown = [e["t"] for e in ulog if e["k"] == "key"
                     and e["down"] == 1]
            gaps = [tdown[i + 1] - tdown[i] for i in range(len(tdown) - 1)]
            check("held typing arrives right after (the longest gap between "
                  "two typed keys stays under 0.7 s)",
                  gaps and max(gaps) < 0.7,
                  f"max {max(gaps):.3f} s, median "
                  f"{statistics.median(gaps):.3f} s" if gaps else "")
            want_keys = {"Game min": {SPACE, K_W}, "Game covered": {K_E},
                         "Game desk2": {K_Q}, "X game": {SPACE}}
            for t, ks in want_keys.items():
                got_k = [e["code"] for e in jsonl(logpath(t))[marks[t]:]
                         if e["k"] == "key" and e["down"] == 1]
                check(f"{t} got only its own keys "
                      f"({', '.join(sorted(map(str, ks)))})",
                      got_k and set(got_k) == ks,
                      f"{len(got_k)} {sorted(set(got_k))}")
            gm = [e["code"] for e in jsonl(logpath("Game min"))[marks[
                "Game min"]:] if e["k"] == "key" and e["down"] == 1]
            check("the two rows of Game min arrive in one focus, Space then W",
                  all(gm[i + 1] == K_W for i in range(len(gm) - 1)
                      if gm[i] == SPACE), str(gm[:12]))
            after = b.kwin.script(STATE_JS)[0]
            bw, aw = before["wins"], after["wins"]
            check("the user window has the focus at the end",
                  aw.get(USER[0], {}).get("active"))
            check("Game min and X game are minimized again, every window "
                  "is opaque again",
                  aw["Game min"]["min"] and aw["X game"]["min"]
                  and all(v["opa"] == 1 for v in aw.values()),
                  json.dumps({k: (v["min"], v["opa"]) for k, v in
                              aw.items()}))
            check("Game desk2 is back on its desktop only, and the current "
                  "desktop never changed",
                  aw["Game desk2"]["desk"] == bw["Game desk2"]["desk"]
                  and not aw["Game desk2"]["all"]
                  and after["cur"] == before["cur"],
                  f"{aw['Game desk2']} {after['cur']} {before['cur']}")
            norm = [x.replace(" *", "") for x in after["order"]]
            check("the stacking order is the one from before",
                  norm == before["order"],
                  f"{before['order']} -> {after['order']}")
            if "WayClick" not in after["wins"]:
                print(b.tail("wayclick", 4000))
            if seen["worst"] is not None:
                kn.save_png(seen["worst"], os.path.join(shots, "worst.png"))
            check("no hidden window ever showed on screen "
                  f"({seen['n']} screenshots while sending)",
                  seen["n"] >= 20 and seen["max"] == 0,
                  f"max {seen['max']} px of game colors")
            # o bloco de notas: salva com Ctrl+S e lê o arquivo
            texts = {}
            for n in ("wl", "x11"):
                b.kwin.window_js(f"notes-{n}", "workspace.activeWindow = w;"
                                 " report({});")
                time.sleep(0.4)
                b.fake.key(CTRL, True)
                b.fake.tap(K_S, 0.05)
                b.fake.key(CTRL, False)
                b.wl.roundtrip()
                until(lambda: os.path.getsize(os.path.join(
                    b.home, f"notes-{n}.txt")) > 0, 5)
                with open(os.path.join(b.home, f"notes-{n}.txt")) as fh:
                    texts[n] = fh.read()
            check("KWrite on Wayland typed one a per round, nothing else",
                  texts["wl"] and set(texts["wl"].strip()) == {"a"}
                  and len(texts["wl"].strip()) == hits[0],
                  f"{texts['wl']!r} hits {hits[0]}")
            check("KWrite on X11 typed one b per key, with no focus at all",
                  texts["x11"] and set(texts["x11"].strip()) == {"b"}
                  and len(texts["x11"].strip()) == hits[1],
                  f"{texts['x11']!r} hits {hits[1]}")

            # ------------------------------------------------- a Doca --
            dk = ask(op="dock", timeout=40, command=(
                f"python3 -I -B {BOX_HOME}/logger.py {BOX_HOME}/Dock_game.log"
                f" '{DOCKG[0]}' {DOCKG[1]} rgb(40,120,40)"))
            dw = until(lambda: [x for x in (ask(op="dock_windows").get("wins")
                                            or []) if x["title"] == DOCKG[0]],
                       40, 0.5)
            check("the Dock opens with a game inside", dk.get("ok") and dw,
                  str(dk))
            b.kwin.window_js(USER[0], "workspace.activeWindow = w; "
                             "report({});")
            time.sleep(0.5)
            ans = ask(op="rows", rows=[{"match": "Dock: " + DOCKG[0],
                                        "key": "Space", "code": SPACE,
                                        "every": 0.3}])
            got = ans.get("rows") or []
            check("the Dock window shows in the list (▣)",
                  got and got[0]["way"] == "▣", str(got)[:300])
            user0 = len(jsonl(logpath(USER[0])))
            ask(op="start")
            for ch in TYPED:
                kb.tap(h.a.KEYS[ch.upper()], 0.04, 0.06)
            time.sleep(0.6)
            ans = ask(op="stop")
            tg = (ans.get("sender") or {}).get("targets") or [{}]
            dkeys = keys(DOCKG[0])
            ulog = jsonl(logpath(USER[0]))[user0:]
            check("the game inside the Dock gets Space every 0.3 s",
                  tg[0].get("hits", 0) >= 5 and not tg[0].get("error")
                  and dkeys.count(SPACE) == tg[0]["hits"],
                  f"hits {tg[0].get('hits')} got {dkeys.count(SPACE)} "
                  f"{tg[0].get('error')}")
            check("and the desktop focus never moved: no focus event in the "
                  "user window, every typed key there",
                  not [e for e in ulog if e["k"] == "focus"]
                  and [e["code"] for e in ulog if e["k"] == "key"
                       and e["down"] == 1] == [h.a.KEYS[c.upper()]
                                               for c in TYPED],
                  f"{len([e for e in ulog if e['k'] == 'focus'])} focus")
            ask(op="dock_stop", timeout=20)

            # ------------------------- um WayClick que caiu no meio --
            ids = {x["caption"]: x["id"] for x in b.kwin.windows()}
            gid = ids.get("Game covered")
            b.kwin.window_js("Game covered", "w.opacity = 0; report({});")
            b.request(op="stop", name="wayclick")
            until(lambda: not os.path.exists(os.path.join(b.home, "rec",
                                                          "held.json")), 10)
            with open(os.path.join(b.rt, "wayclick-flash.json"), "w") as fh:
                json.dump([{"id": gid, "opa": 1}], fh)
            kb.wait_free()
            n_ready = len([e for e in jsonl(evlog) if e["k"] == "ready"])
            env2 = {"DISPLAY": disp}
            if os.environ.get("WCTEST_TIMELINE"):
                env2["WCTEST_DUMP"] = "4"
            b.request(op="spawn", name="wayclick2", argv=[
                "python3", "-X", "faulthandler", "-I", "-B",
                os.path.abspath(__file__),
                "--wayclick", f"/run/user/{UID}/wctest-bridge.sock"],
                env=env2)
            t_open = time.monotonic()
            until(lambda: len([e for e in jsonl(evlog)
                               if e["k"] == "ready"]) > n_ready, 40)
            t_ready = time.monotonic() - t_open
            flash = os.path.join(b.rt, "wayclick-flash.json")
            tl = []

            def restored():
                o = (b.kwin.window_js("Game covered",
                                      "report({o: w.opacity});") or {}).get("o")
                f = os.path.exists(flash)
                js = [x for x in os.listdir(b.rt) if x.endswith(".js")]
                tl.append(f"{time.monotonic() - t_open:.1f}:o={o},f={int(f)},"
                          f"js={len(js)}")
                return o == 1 and not f
            back = until(restored, 15, 0.3)
            if os.environ.get("WCTEST_TIMELINE"):
                print("      " + " ".join(tl))
                try:
                    print(open(os.path.join(b.home, "rec", "dump.txt"))
                          .read()[:6000])
                except OSError:
                    pass
            check("a window a crashed WayClick left invisible is visible "
                  "again as soon as WayClick opens",
                  back, f"window {'opaque' if back else 'still hidden'} "
                  f"{time.monotonic() - t_open:.1f} s after launch (ready "
                  f"at {t_ready:.1f} s) "
                  f"{'' if back else b.tail('wayclick2', 1500)}")
            b.request(op="stop", name="wayclick2")
        finally:
            kb.close()
            if RESULTS.count(False):
                print(b.tail("wayclick", 3000))
    n, bad = len(RESULTS), RESULTS.count(False)
    print(f"\n{n - bad}/{n} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
