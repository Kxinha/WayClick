"""O Sober de verdade dentro da Doca, na caixa de kwin_nested.py.

A Doca abre o Sober pelo comando dela (flatpak run), com HOME, D-Bus e
runtime da caixa. A caixa não tem rede, então o Sober do teste nunca briga
com o Sober de quem roda pela porta fixa do 127.0.0.1, não vê a conta de
ninguém (dos arquivos do Sober de quem roda vêm só os APKs e a versão do
app, como em t_sober_kwin.py) e para na tela de erro dele ("Failed to fetch
remote manifest"). É ela que o teste usa: a Doca tem que mostrar a janela
do Sober, a captura da Doca tem que ver o botão Close, e um macro da Doca
tem que achá-lo pela imagem e clicar, e o Sober fecha. O Roblox em si (que
precisa de rede) fica para t_sober_kwin.py.

    nice -n 19 python3 -B tests/t_sober_dock.py      (pula se faltar algo)
"""
import json, os, shutil, subprocess, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kwin_nested as kn                                    # noqa: E402
import t_sober_kwin as S                                    # noqa: E402

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail
                                                     else ""))
    sys.stdout.flush()


PROBE = r'''
import json, os, shutil, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import wayclick as a
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
a.LANG = "en_US"
HOME = os.environ["HOME"]
VK = sys.argv[2]
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
        time.sleep(0.05)
    return cond()


class Hub:
    def add(self, s): pass
    def remove(self, s): pass
    def changed(self): pass
    def mods_active(self): return frozenset()


def log():
    try:
        with open(os.path.join(d.folder(), "dock.log"), "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def sober():
    return (d.windows.find(d.SOBER) or {}) if d.windows else {}


d = a.DOCK()
d.size = (1280, 720)
# o Vulkan e o EGL do Sober na GPU da caixa, como em t_sober_kwin.py
extra = ""
if VK:
    extra = f"--env=VK_LOADER_DRIVERS_SELECT={VK} "
    if VK != "*nvidia*":
        extra += ("--env=__EGL_VENDOR_LIBRARY_FILENAMES=/usr/lib/"
                  "x86_64-linux-gnu/GL/default/share/glvnd/egl_vendor.d/"
                  "50_mesa.json ")
d.command = "flatpak run " + extra + d.SOBER
ok = d.start(launch=True)
check("the Dock opens and starts Sober with its own command",
      ok and d.alive() and d.game_running(), f"{d.error} {d.note}")
up = until(lambda: sober().get("found"), 90)
check("Sober's window opens inside the Dock", up, log()[-300:])
check("with no network, Sober stops at its error screen",
      until(lambda: "remote manifest" in log(), 30), log()[-300:])
# o diálogo do Sober tem fundo claro e o botão Close cinza no meio dele
frame = None
end = time.monotonic() + 20
while time.monotonic() < end:
    if d.screen.ensure():
        f = d.screen.frames()[0]
        if (f.rgb[233, 185] < 240).all() and (f.rgb[20, 185] > 240).all():
            frame = f
            break
    app.processEvents()
    time.sleep(0.2)
check("the capture of the Dock sees Sober's dialog", frame is not None,
      d.screen.error)
if frame is not None:
    with open(HOME + "/sober.png", "wb") as fh:
        fh.write(a.png_bytes(frame.rgb))
    tpl = HOME + "/close.png"
    with open(tpl, "wb") as fh:
        fh.write(a.png_bytes(frame.rgb[218:250, 120:250]))
    eng = a.MacroEngine(lambda: (None, None), hub=Hub())
    eng.dock = d
    problems = []
    eng.report = problems.append
    m = a.normalize_macro({"name": "sober", "mode": "once", "dock": True,
                           "steps": [{"op": "image", "path": tpl,
                                      "conf": 0.9, "wait": 5000,
                                      "act": "left", "fail": "stop"}]})
    r = eng._start(a.CompiledMacro(m))
    until(lambda: r is None or not r.is_alive(), 20)
    check("a Dock macro finds Sober's Close button by its image",
          r is not None and not r.is_alive() and not problems,
          str(problems))
    gone = until(lambda: not d.game_running(), 20)
    check("and its click reaches Sober, which closes", gone,
          str(sober()) + log()[-200:])
d.stop()
until(lambda: d.state == "off", 10)
check("closing the Dock leaves nothing running",
      d.state == "off" and not d.game_running()
      and not os.path.exists(d.socket_path()))
print(json.dumps(R))
'''


def skip_reason():
    for tool in ("flatpak", "bwrap", "kwin_wayland", "dbus-daemon"):
        if not shutil.which(tool):
            return f"{tool} is not installed"
    if subprocess.run(["flatpak", "info", S.APP], capture_output=True,
                      timeout=30).returncode:
        return "Sober is not installed"
    if not any(rel.endswith("base.apk") for _p, rel in S.apk_files()):
        return "Sober has no Roblox APK yet (open Sober once)"
    if not S.user_app_version():
        return "Sober's state has no app_version (open Sober once)"
    return ""


why = skip_reason()
if why:
    print(f"SKIP  {why}")
    sys.exit(0)
with kn.Bench(outputs=1) as b:
    S.Sober._prepare(types.SimpleNamespace(home=b.home,
                                           version=S.user_app_version()))
    vk = {"0x1002": "*radeon*", "0x8086": "*intel*",
          "0x10de": "*nvidia*"}.get(b.vendor) or ""
    r = b.run([sys.executable, "-I", "-B", "-c", PROBE, kn.REPO, vk],
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
            check(label, ok, detail if not ok else "")
        if os.environ.get("WCTEST_KEEP_SHOT"):
            src = os.path.join(b.home, "sober.png")
            if os.path.exists(src):
                shutil.copy(src, os.environ["WCTEST_KEEP_SHOT"])

n, bad = len(RESULTS), RESULTS.count(False)
print(f"\n{n - bad}/{n} passed")
sys.exit(1 if bad else 0)
