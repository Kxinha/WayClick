"""Um .ahk importado de ponta a ponta, num KWin de verdade (aninhado e
isolado, ver kwin_nested.py), com duas capturas da tela da caixa.

Na caixa: um alvo Qt (sem moldura) que anota e mostra as teclas e os cliques
que recebe, e o WayClick de verdade com a janela dele, que importa
tests/ahk/kwin_e2e.ahk (F7::Suspend, o bhop de segurar Espaço mandando F9,
um ImageSearch que clica numa figura e um PixelSearch v1 em BGR que clica no
ponto vermelho). As capturas de tela do WayClick vêm do ScreenShot2 do KWin
da caixa (a mesma DockScreen da Doca); o portal já é coberto pelo
t_sober_kwin.py. Os devices virtuais do WayClick são capturados ao nascer e
vão só para o KWin aninhado; o teclado do teste (wctest-kbd) só aperta com o
WayClick da caixa segurando ele com EVIOCGRAB (a prova dá EBUSY), então nada
chega ao desktop de quem roda.

    nice -n 19 python3 -B tests/t_ahk_kwin.py
    WCTEST_SHOTS=/pasta nice -n 19 python3 -B tests/t_ahk_kwin.py
"""
import json, os, shutil, signal, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
UID = os.getuid()
BOX_HOME = "/home/wctest"
REC = BOX_HOME + "/rec"
SPACE, F4, F6, F7, F9 = 57, 62, 64, 65, 67
# o alvo: janela sem moldura em (10, 10); a figura e o ponto vermelho em
# coordenadas da janela
TW, TH, TX, TY = 1100, 700, 10, 10
SPRITE_AT, SPRITE = (760, 250), 48
RED_AT, RED = (300, 470), 10

SPRITE_PY = r"""
import numpy as np
_rng = np.random.default_rng(20261127)
SPR = (_rng.integers(40, 216, (12, 12, 3)).astype(np.uint8)
       .repeat(4, 0).repeat(4, 1))
"""

TARGET = SPRITE_PY + r"""
import json, os, statistics, sys, time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget
TW, TH, SX, SY, SS, RX, RY, RS = (int(v) for v in sys.argv[2:10])
log = open(sys.argv[1], "a", buffering=1)
NAMES = {57: "Espaço", 62: "F4", 64: "F6", 65: "F7", 67: "F9"}
app = QApplication(sys.argv[:1])
QGuiApplication.setDesktopFileName("org.wctest.Alvo")
SPI = QImage(np.ascontiguousarray(SPR).data, SPR.shape[1], SPR.shape[0],
             3 * SPR.shape[1], QImage.Format_RGB888).copy()


class Alvo(QWidget):
    def __init__(s):
        super().__init__(None, Qt.FramelessWindowHint)
        s.setWindowTitle("Alvo AHK")
        s.setFixedSize(TW, TH)
        s.f9 = []                       # instantes do F9 da rajada atual
        s.space = 0
        s.other = []
        s.img_hit = s.red_hit = None
        s.last = "nada ainda"

    def note(s, **kw):
        kw["t"] = round(time.monotonic(), 6)
        log.write(json.dumps(kw) + "\n")

    def keyPressEvent(s, e):
        if e.isAutoRepeat():
            return
        code = e.nativeScanCode() - 8
        now = time.monotonic()
        s.note(k="key", code=code, down=1)
        if code == 67:
            if not s.f9 or now - s.f9[-1] > 0.25:
                s.f9 = []
            s.f9.append(now)
        elif code == 57:
            s.space += 1
        else:
            s.other.append(NAMES.get(code, str(code)))
        s.last = "tecla " + NAMES.get(code, str(code))
        s.update()

    def keyReleaseEvent(s, e):
        if not e.isAutoRepeat():
            s.note(k="key", code=e.nativeScanCode() - 8, down=0)

    def mousePressEvent(s, e):
        x, y = int(e.position().x()), int(e.position().y())
        hit = None
        if SX <= x < SX + SS and SY <= y < SY + SS:
            hit, s.img_hit = "image", (x, y)
        elif RX <= x < RX + RS and RY <= y < RY + RS:
            hit, s.red_hit = "red", (x, y)
        s.note(k="click", x=x, y=y, btn=int(e.button().value), hit=hit)
        s.last = f"clique em {x}, {y}"
        s.update()

    def paintEvent(s, e):
        p = QPainter(s)
        p.fillRect(s.rect(), QColor(24, 28, 34))
        p.drawImage(SX, SY, SPI)
        p.fillRect(RX, RY, RS, RS, QColor(255, 0, 0))
        green = QColor(80, 230, 120)
        if s.img_hit:
            p.setPen(green)
            p.drawRect(SX - 6, SY - 6, SS + 11, SS + 11)
        if s.red_hit:
            p.setPen(green)
            p.drawEllipse(RX - 12, RY - 12, RS + 24, RS + 24)
        p.setPen(QColor(235, 235, 235))
        f = QFont()
        f.setPixelSize(30)
        f.setBold(True)
        p.setFont(f)
        p.drawText(30, 50, "Alvo do teste AHK do WayClick")
        f.setPixelSize(19)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(170, 180, 195))
        p.drawText(30, 82, "KWin aninhado e sem tela, numa caixa bwrap sem "
                           "rede. tests/ahk/kwin_e2e.ahk importado.")
        p.setPen(QColor(235, 235, 235))
        f.setPixelSize(24)
        p.setFont(f)
        n = len(s.f9)
        gaps = [(b - a) * 1000 for a, b in zip(s.f9, s.f9[1:])]
        mid = statistics.median(gaps) if gaps else 0.0
        rate = (n - 1) / (s.f9[-1] - s.f9[0]) if n > 1 else 0.0
        lines = [
            f"F9 recebidos na rajada: {n}",
            f"intervalo mediano {mid:.1f} ms, {rate:.1f} por segundo",
            f"Espaço que chegou aqui: {s.space}",
            "outras teclas: " + (", ".join(s.other) or "nenhuma"),
            "ImageSearch: " + (f"clicou em {s.img_hit[0]}, {s.img_hit[1]} "
                               "dentro da figura" if s.img_hit
                               else "ainda não"),
            "PixelSearch: " + (f"clicou em {s.red_hit[0]}, {s.red_hit[1]} "
                               "no ponto vermelho" if s.red_hit
                               else "ainda não"),
            "último evento: " + s.last]
        for i, t in enumerate(lines):
            p.drawText(30, 150 + 44 * i, t)
        f.setPixelSize(17)
        p.setFont(f)
        p.setPen(QColor(150, 160, 175))
        p.drawText(SX - 20, SY + SS + 30, "figura (ImageSearch)")
        p.drawText(RX - 20, RY + RS + 34, "vermelho (PixelSearch)")
        p.end()


w = Alvo()
w.show()
QTimer.singleShot(0, lambda: w.note(k="ready"))
app.exec()
"""

AHK = os.path.join(HERE, "ahk", "kwin_e2e.ahk")


# ===================================================== dentro da caixa ==
def _log(kind, **kw):
    kw.update(k=kind, t=round(time.monotonic(), 6))
    with open(os.path.join(REC, "events.jsonl"), "a") as fh:
        fh.write(json.dumps(kw) + "\n")


def wayclick_main(bridge_sock, ahk):
    """O WayClick de verdade na caixa: importa o .ahk pelo mesmo caminho do
    botão Importar AHK, olha a tela pelo ScreenShot2 do KWin da caixa e diz
    a cada 200 ms quais devices segura (held.json)."""
    import harness as h
    import kwin_nested as K
    a = h.a
    a.NEW_NODE_HOOK = K.wayclick_hook(bridge_sock)
    os.makedirs(REC, exist_ok=True)
    held_path = os.path.join(REC, "held.json")

    class BoxDock:                  # o que a DockScreen pede de uma Doca
        out_size = None

        def alive(self):
            return True

        def bus_address(self):
            return f"unix:path={os.environ['XDG_RUNTIME_DIR']}/bus"

    a._SCREEN = a.DockScreen(BoxDock())
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication(["wayclick"])
    w = a.App()
    w.sound.setChecked(False)
    w.engine.problem.connect(lambda t: _log("problem", text=t))
    w.engine.message.connect(lambda ti, t: _log("message", title=ti, text=t))
    w.macro_box.toggled.connect(lambda on: _log("macros", on=on))
    n = w.import_ahk_path(ahk, ask=False)
    w.tabs.setCurrentIndex(2)           # a aba Macros, com o Suspend
    beat = [0.0]

    def tick():
        now = time.monotonic()
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
    QTimer.singleShot(0, lambda: _log(
        "ready", imported=n, macros=[m.get("name") for m in w.macros],
        suspend=w.suspend_key.text(), on=w.macro_box.isChecked(),
        steps=[[s.get("op") for s in m.get("steps", [])] for m in w.macros]))
    sys.exit(app.exec())


if __name__ == "__main__" and sys.argv[1:2] == ["--wayclick"]:
    wayclick_main(sys.argv[2], sys.argv[3])
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


def main():
    import harness as h
    from t_sober_kwin import Keyboard
    import numpy as np
    shots = os.environ.get("WCTEST_SHOTS") or "/tmp/wayclick-ahk-kwin"
    os.makedirs(shots, exist_ok=True)
    ns = {}
    exec(SPRITE_PY, ns)
    spr = ns["SPR"]
    with kn.Bench(size="1920x1080", outputs=1) as b:
        ahk_dir = os.path.join(b.home, "ahk")
        os.makedirs(ahk_dir)
        shutil.copyfile(AHK, os.path.join(ahk_dir, "kwin_e2e.ahk"))
        kn.save_png(spr, os.path.join(ahk_dir, "alvo.png"))
        with open(os.path.join(b.home, "alvo.py"), "w") as fh:
            fh.write(TARGET)
        cfg = os.path.join(b.home, ".config")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "wayclick.json"), "w") as fh:
            json.dump({"sound": False, "language": "pt_BR", "delay": 0,
                       "click_enabled": False}, fh)
        tlog = os.path.join(b.home, "alvo.log")
        evlog = os.path.join(b.home, "rec", "events.jsonl")

        def target():
            return jsonl(tlog)

        def events(kind=None):
            return [e for e in jsonl(evlog) if kind is None or e["k"] == kind]

        r = b.request(op="spawn", name="alvo", argv=[
            "python3", "-I", "-B", BOX_HOME + "/alvo.py", BOX_HOME
            + "/alvo.log", str(TW), str(TH), str(SPRITE_AT[0]),
            str(SPRITE_AT[1]), str(SPRITE), str(RED_AT[0]), str(RED_AT[1]),
            str(RED)])
        check("the target window opens in the nested KWin", r.get("ok")
              and until(lambda: any(e["k"] == "ready" for e in target()), 30),
              f"{r} {b.tail('alvo')}")
        b.kwin.window_js("Alvo AHK", f"w.frameGeometry = {{x: {TX}, y: {TY},"
                         f" width: {TW}, height: {TH}}}; report({{}});")
        kb = Keyboard(h, os.path.join(b.home, "rec", "held.json"))
        try:
            kb.wait_free()
            r = b.request(op="spawn", name="wayclick", argv=[
                "python3", "-I", "-B", os.path.abspath(__file__),
                "--wayclick", f"/run/user/{UID}/wctest-bridge.sock",
                BOX_HOME + "/ahk/kwin_e2e.ahk"])
            ready = until(lambda: events("ready"), 40)
            ready = ready[-1] if ready else {}
            check("WayClick opens in the box and imports kwin_e2e.ahk",
                  r.get("ok") and ready.get("imported") == 3
                  and ready.get("suspend") == "F7" and ready.get("on"),
                  f"{ready} {'' if ready else b.tail('wayclick', 3000)}")
            check("the three hotkeys became WayClick macros: hold, image "
                  "and pixel steps",
                  ready.get("steps") and ready["steps"][1][0] == "image"
                  and ready["steps"][2][0] == "pixel",
                  str(ready.get("steps")))
            until(lambda: any(x.get("caption") == "WayClick"
                              for x in b.kwin.windows()), 15, 0.2)
            b.kwin.window_js("WayClick", "w.frameGeometry = {x: 1120, y: 0, "
                             "width: 800, height: 1080}; report({});")
            kb.wait_grabbed()
            time.sleep(0.5)
            b.kwin.window_js("Alvo AHK", "workspace.activeWindow = w; "
                             "report({});")
            time.sleep(0.5)
            wins = {x.get("caption"): x for x in b.kwin.windows()}
            geo = wins.get("Alvo AHK") or {}
            check("the target sits at its place and has the focus",
                  (geo.get("cx"), geo.get("cy")) == (TX, TY)
                  and geo.get("active"), str(list(wins.values()))[:600])

            # ----------------------------------------------- 1: o bhop --
            n0 = len(target())
            kb.key(SPACE, True)
            time.sleep(1.2)
            _m, rgb = b.kwin.screenshot()
            p1 = os.path.join(shots, "1-bhop-espaco-segurado.png")
            kn.save_png(rgb, p1)
            time.sleep(0.3)
            kb.key(SPACE, False)
            time.sleep(0.3)
            got = target()[n0:]
            f9 = [e["t"] for e in got if e["k"] == "key"
                  and e["code"] == F9 and e["down"]]
            ups = [e for e in got if e["k"] == "key" and e["code"] == F9
                   and not e["down"]]
            gaps = [(y - x) * 1000 for x, y in zip(f9, f9[1:])]
            mid = statistics.median(gaps) if gaps else 0
            rate = (len(f9) - 1) / (f9[-1] - f9[0]) if len(f9) > 1 else 0
            check("holding Space: the target gets F9 about 32 a second, "
                  "like the Linux bhop script",
                  len(f9) >= 35 and 28.0 <= mid <= 36.0,
                  f"{len(f9)} F9 in 1.5 s, median {mid:.1f} ms, "
                  f"{rate:.1f}/s")
            check("each F9 is let go, and Space never reaches the target",
                  len(ups) == len(f9) and not [
                      e for e in got if e["k"] == "key"
                      and e["code"] == SPACE], f"{len(ups)} releases")

            # ------------------------------------------- 2: o Suspend --
            n0, e0 = len(target()), len(events("macros"))
            kb.tap(F7)
            off = until(lambda: events("macros")[e0:], 3)
            check("F7 suspends the hotkeys", off and off[-1]["on"] is False,
                  str(off))
            kb.key(SPACE, True)
            time.sleep(0.4)
            kb.key(SPACE, False)
            time.sleep(0.3)
            got = target()[n0:]
            keys = [(e["code"], e["down"]) for e in got if e["k"] == "key"]
            check("suspended, Space reaches the target as a plain key and "
                  "no F9 goes out", (SPACE, 1) in keys and (SPACE, 0) in keys
                  and not [k for k in keys if k[0] == F9], str(keys))
            check("and F7 itself never reaches it",
                  not [k for k in keys if k[0] == F7], str(keys))
            e0 = len(events("macros"))
            kb.tap(F7)
            on = until(lambda: events("macros")[e0:], 3)
            check("F7 again turns them back on", on and on[-1]["on"] is True,
                  str(on))
            n0 = len(target())
            kb.key(SPACE, True)
            time.sleep(0.4)
            kb.key(SPACE, False)
            time.sleep(0.3)
            f9 = [e for e in target()[n0:] if e["k"] == "key"
                  and e["code"] == F9 and e["down"]]
            check("and the bhop works again", len(f9) >= 9, f"{len(f9)} F9")

            # ------------------------------ 3: ImageSearch e PixelSearch --
            n0 = len(target())
            kb.tap(F6)
            hit = until(lambda: [e for e in target()[n0:]
                                 if e["k"] == "click"], 5)
            want = (SPRITE_AT[0] + SPRITE // 2, SPRITE_AT[1] + SPRITE // 2)
            c = hit[0] if hit else {}
            check("F6: ImageSearch finds the picture and clicks it",
                  c.get("hit") == "image" and c.get("btn") == 1
                  and abs(c["x"] - want[0]) <= 1
                  and abs(c["y"] - want[1]) <= 1,
                  f"{c} want about {want}; {events('problem')}")
            check("and no N was sent (the else branch did not run)",
                  not [e for e in target()[n0:] if e["k"] == "key"
                       and e["code"] == 49])
            n0 = len(target())
            kb.tap(F4)
            hit = until(lambda: [e for e in target()[n0:]
                                 if e["k"] == "click"], 5)
            c = hit[0] if hit else {}
            check("F4: v1 PixelSearch 0x0000FF (BGR) finds the red dot, "
                  "leftmost top pixel, and clicks it",
                  c.get("hit") == "red" and (c["x"], c["y"]) == RED_AT,
                  f"{c}; {events('problem')}")
            time.sleep(0.4)
            _m, rgb = b.kwin.screenshot()
            p2 = os.path.join(shots, "2-suspend-imagesearch-pixelsearch.png")
            kn.save_png(rgb, p2)
            check("two Wayland captures saved", os.path.getsize(p1) > 10000
                  and os.path.getsize(p2) > 10000, f"{p1} {p2}")
            check("WayClick reported no problem", not events("problem"),
                  str(events("problem")))
            # a figura e o vermelho estão na captura onde o alvo diz
            ox, oy = geo.get("cx", TX), geo.get("cy", TY)
            sx, sy = ox + SPRITE_AT[0], oy + SPRITE_AT[1]
            seen = rgb[sy:sy + SPRITE, sx:sx + SPRITE].astype(int)
            check("the capture shows the picture where the click landed",
                  np.abs(seen - spr.astype(int)).max() <= 8,
                  f"max diff {np.abs(seen - spr.astype(int)).max()}")
        finally:
            b.request(op="stop", name="wayclick")
            b.request(op="stop", name="alvo")
            kb.close()
            if not all(RESULTS):
                print(b.tail("wayclick", 3000))
                print(b.tail("alvo", 1500))
    n, bad = len(RESULTS), RESULTS.count(False)
    print(f"\n{n - bad}/{n} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
