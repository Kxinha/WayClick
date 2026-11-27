"""O WayClick de ponta a ponta contra o Sober (Roblox), num KWin isolado.

Tudo roda na caixa de kwin_nested.py: o KWin aninhado (duas telas, a da
esquerda mais baixa), o portal de captura de tela de verdade
(xdg-desktop-portal-kde, com o diálogo de compartilhar respondido por cliques
do teste), o WayClick com a janela dele, e um Sober novo, sem conta, numa
pasta própria. Nada toca o desktop, o som, o D-Bus ou o Sober de quem roda:

* o Sober do teste tem HOME próprio em /var/tmp (dos arquivos do Sober de
  quem roda vêm só os APKs do Roblox e a versão do app; nenhum cookie, nada
  de conta), toca no som mudo da caixa, desenha no KWin aninhado, fica num
  escopo systemd próprio com CPU e disco na menor prioridade, e tem rede
  própria pelo pasta (passt): o Sober usa uma porta fixa em 127.0.0.1 como
  trava de instância, e na rede de quem roda ele brigaria com o Sober aberto;
* o teclado do teste (wctest-kbd) só aperta capturado pelo WayClick da
  caixa: antes de cada evento o WayClick da caixa tem que ter dito, há menos
  de 3 s, que segura esse device, e um EVIOCGRAB de prova tem que dar EBUSY.
  A captura é exclusiva, então nenhum outro programa (um WayClick aberto
  fora da caixa, por exemplo) recebe o que o teste aperta. E antes de cada
  WayClick da caixa abrir, o teclado tem que estar livre;
* cada device virtual do WayClick é capturado ao nascer e vai para a ponte,
  que o entrega só ao KWin aninhado;
* o que conferimos: o que a ponte entregou (teclas, cliques, posição exata
  do cursor), o que o KWin mostrou (prints) e o que o Roblox recebeu (o texto
  do campo de usuário, lido de volta pela área de transferência da caixa).

O Sober aborta sozinho nesta caixa entre 90 e 150 s depois de abrir (OpenSL,
"status != SL_RESULT_SUCCESS", mesmo parado e com qualquer saída de som),
então cada sessão é curta e abre um Sober novo; a sessão em que ele morreu
roda de novo, uma vez.

    python3 tests/t_sober_kwin.py          (pula sozinho se faltar algo)
    WCTEST_PASTA=/caminho/do/pasta python3 tests/t_sober_kwin.py
"""
import errno, fcntl, json, os, shutil, signal, subprocess, sys, tempfile
import threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
APP = "org.vinegarhq.Sober"
UID = os.getuid()
USER_SOBER = os.path.expanduser(f"~/.var/app/{APP}/data/sober")
USER_INSTANCES = f"/run/user/{UID}/.flatpak"
BOX_HOME = "/home/wctest"                   # o HOME de dentro da caixa
REC = BOX_HOME + "/rec"
IMG = BOX_HOME + "/.local/share/wayclick/images/signin.png"
SHARE = "Screen Sharing Requested"
MAIN = APP + " Sober"           # classe e título da janela do jogo, não a
                                # de avisos que o Sober também abre
# o diálogo do xdg-desktop-portal-kde com duas telas: pontos a partir do
# canto da janela (as duas telas, a área de trabalho inteira, "sem perguntar
# da próxima vez", Compartilhar e Cancelar)
DLG_SIZE = (738, 766)
DLG = {"v0": (343, 166), "v1": (708, 166), "ws": (343, 405),
       "allow": (18, 741), "share": (598, 741), "cancel": (689, 741)}
# o jogo fica inteiro na tela de baixo à esquerda (v0 no diálogo); a outra
# (v1) tem a janela do WayClick e o ponteiro parado
GAME, GAME_OUTPUT = (0, 339, 1920, 1080), "Virtual-0"
ON_GAME_TITLE = (300, 352)          # na barra de título do jogo, sem botão
EVIOCGRAB = (1 << 30) | (4 << 16) | (ord("E") << 8) | 0x90
HELD_MAX_AGE = 3.0
# teclas (códigos do evdev)
BKSP, A, B, C, E, J = 14, 30, 48, 46, 18, 36
CTRL, SHIFT, ALT, CAPS, GRAVE = 29, 42, 56, 58, 41
F1, F4, F5, F6, F8, F9, F10, F11 = 59, 62, 63, 64, 66, 67, 68, 87
END, INSERT = 107, 110
HOTKEYS = {CAPS, J, GRAVE, F1, F4, F5, F6, F8, F9, F10, F11, INSERT}
AHK = ["remap.ahk", "SpamE F1.ahk", "keywait.ahk", "combo.ahk",
       "stacked.ahk"]


# ===================================================== dentro da caixa ==
def _log(rec_dir, kind, **kw):
    kw.update(k=kind, t=round(time.monotonic(), 6))
    with open(os.path.join(rec_dir, "events.jsonl"), "a") as fh:
        fh.write(json.dumps(kw) + "\n")


def wayclick_main(bridge_sock, rec_dir):
    """O WayClick de verdade, janela no KWin aninhado. Os devices virtuais
    dele vão para a ponte; webhook e abrir link só anotam (nada sai da
    caixa), e avisos, macros ligando e o clique armando vão para um log.
    A cada 200 ms ele diz quais devices segura (held.json)."""
    import harness as h
    import kwin_nested as K
    a = h.a
    a.NEW_NODE_HOOK = K.wayclick_hook(bridge_sock)
    os.makedirs(rec_dir, exist_ok=True)
    held_path = os.path.join(rec_dir, "held.json")
    lock, count = threading.Lock(), [0]

    def webhook(url, text, images=()):
        with lock:
            count[0] += 1
            n = count[0]
        names = []
        for i, png in enumerate(images):
            p = os.path.join(rec_dir, f"hook{n}-{i}.png")
            with open(p, "wb") as fh:
                fh.write(png)
            names.append(p)
        _log(rec_dir, "webhook", url=url, text=text, images=names)
        return 204

    a.WEBHOOK = webhook
    a.OPENER = lambda url: _log(rec_dir, "open", url=url)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication(["wayclick"])
    w = a.App()
    w.engine.problem.connect(lambda t: _log(rec_dir, "problem", text=t))
    w.engine.message.connect(
        lambda title, t: _log(rec_dir, "message", title=title, text=t))
    w.engine.toggled.connect(lambda on: _log(rec_dir, "toggled", on=on))
    last, beat = [None], [0.0]

    def tick():
        on = bool(w.running)            # o clique automático armou?
        if on != last[0]:
            last[0] = on
            _log(rec_dir, "running", on=on)
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

    def quitting():                 # antes de soltar as capturas
        timer.stop()
        try:
            os.remove(held_path)
        except OSError:
            pass
    app.aboutToQuit.connect(quitting)
    app.aboutToQuit.connect(w.cleanup)
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    w.show()
    QTimer.singleShot(0, lambda: _log(
        rec_dir, "ready", macros=len(w.macros), workspace=w.engine.workspace,
        watcher=w.watcher.ok, arm=w.hk.code))
    sys.exit(app.exec())


if __name__ == "__main__" and sys.argv[1:2] == ["--wayclick"]:
    wayclick_main(sys.argv[2], sys.argv[3])


# ============================================================ o Sober ==
class SoberDied(Exception):
    pass


# dentro do namespace de rede novo: espera o pasta ligar a rede, põe o DNS
# dele no lugar do resolv.conf (só para este processo) e volta ao usuário
# de verdade antes do flatpak
NETNS_SH = r'''
set -e
i=0
while [ ! -e "$WCTEST_NET_READY" ]; do
  sleep 0.1; i=$((i+1)); [ $i -gt 300 ] && { echo "no network" >&2; exit 99; }
done
mount --bind "$WCTEST_RESOLV" "$(readlink -f /etc/resolv.conf)"
exec unshare --user --map-user="$WCTEST_UID" --map-group="$WCTEST_GID" -- "$@"
'''


def user_app_version():
    """Só a versão do Roblox que o Sober de quem roda baixou: com ela e os
    APKs o Sober do teste abre sem baixar nada."""
    try:
        with open(os.path.join(USER_SOBER, "state")) as fh:
            v = json.load(fh).get("v1", {}).get("app_version")
        return v if isinstance(v, str) and v else None
    except (OSError, ValueError, AttributeError):
        return None


def apk_files():
    src = os.path.join(USER_SOBER, "packages")
    out = []
    for root, _dirs, files in os.walk(src):
        for f in files:
            if f.endswith(".apk"):
                p = os.path.join(root, f)
                out.append((p, os.path.relpath(p, src)))
    return out


def user_instances():
    try:
        return set(os.listdir(USER_INSTANCES))
    except OSError:
        return set()


def stop_unit(unit):
    """Para um escopo do systemd do usuário; se não sair por bem, KILL."""
    try:
        subprocess.run(["systemctl", "--user", "stop", unit],
                       capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        subprocess.run(["systemctl", "--user", "kill", "-s", "SIGKILL", unit],
                       capture_output=True, timeout=10)
        subprocess.run(["systemctl", "--user", "stop", unit],
                       capture_output=True, timeout=20)


class Sober:
    """Um Sober por sessão, todos na mesma pasta descartável."""

    def __init__(self, bench, pasta, version):
        self.b, self.pasta, self.version = bench, pasta, version
        self.home = tempfile.mkdtemp(prefix="wctest-sober-", dir="/var/tmp")
        self.rt = tempfile.mkdtemp(prefix="wctest-sober-rt-")
        os.chmod(self.rt, 0o700)
        self.log = os.path.join(self.rt, "sober.log")
        self.proc, self.unit, self.n, self.t0 = None, None, 0, None
        # o Vulkan do Sober na GPU da caixa (a sem monitor), não na do jogo
        self.vk = {"0x1002": "*radeon*", "0x8086": "*intel*",
                   "0x10de": "*nvidia*"}.get(bench.vendor)
        self._prepare()

    def _prepare(self):
        app = os.path.join(self.home, ".var", "app", APP)
        data = os.path.join(app, "data", "sober")
        for src, rel in apk_files():
            dst = os.path.join(data, "packages", rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)      # cópia: nada escreve nos do usuário
        # o que o próprio Sober grava depois da primeira abertura (sem a
        # versão ele baixaria o Roblox, sem r1_enabled mostra a tela de boas
        # vindas); roblox_uid 0 é ninguém logado
        with open(os.path.join(data, "state"), "w") as fh:
            json.dump({
                "v1": {"app_version": self.version, "fullscreen": False},
                "v2": {"has_seen_onboarding": True, "r1_enabled": True},
                "v3": {"enable_texture_quality_override_for_graphics_oom_"
                       "workaround": False},
                "v4": {"migrate__ensure_discord_rpc_disabled_by_default":
                       True, "tag__onboarding_home_impression": 8},
                "v5": {"graphics_oom_workaround_stage": 0},
                "v6": {"roblox_uid": 0}}, fh, indent=4)
        conf = os.path.join(app, "config", "sober")
        os.makedirs(conf, exist_ok=True)
        with open(os.path.join(conf, "config.json"), "w") as fh:
            json.dump({"allow_gamepad_permission": False,
                       "close_on_leave": False, "discord_rpc_enabled": False,
                       "discord_rpc_show_join_button": False,
                       "enable_gamemode": False, "enable_hidpi": False,
                       "enable_mobile_home_screen": False,
                       "fflags": {"FFlagExample": True},
                       "graphics_optimization_mode": "balanced",
                       "server_location_indicator_enabled": False,
                       "touch_mode": "off", "use_console_experience": False,
                       "use_libsecret": False, "use_opengl": False}, fh,
                      indent=4)

    def _read_log(self):
        try:
            with open(self.log, "rb") as fh:
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def tail(self, n=600):
        return self._read_log()[-n:]

    def age(self):
        return time.monotonic() - self.t0 if self.t0 else 0.0

    def start(self):
        self.stop()
        self.n += 1
        unit = f"wctest-sober-{os.getpid()}-{self.n}"
        ready = os.path.join(self.rt, "net-ready")
        if os.path.exists(ready):
            os.remove(ready)
        resolv = os.path.join(self.rt, "resolv.conf")
        with open(resolv, "w") as fh:
            fh.write("nameserver 169.254.1.1\noptions edns0\n")
        os.makedirs(os.path.join(self.rt, "pulse"), exist_ok=True)
        link = os.path.join(self.rt, "pulse", "native")
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(os.path.join(self.b.rt, "pulse", "native"), link)
        with open(self.log, "wb"):
            pass
        # o KWin abre a janela, e o Sober se maximiza de novo logo depois,
        # na tela do ponteiro: ele fica na tela do jogo até ela parar
        self.b.fake.absolute(*ON_GAME_TITLE)
        self.b.wl.roundtrip()
        import kwin_nested as K
        env = {"HOME": self.home, "USER": os.environ.get("USER", "wctest"),
               "LOGNAME": os.environ.get("USER", "wctest"),
               "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8",
               "XDG_RUNTIME_DIR": self.rt,
               "WAYLAND_DISPLAY": os.path.join(self.b.rt, K.SOCKET),
               "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.b.rt}/bus",
               "XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland",
               "WCTEST_NET_READY": ready, "WCTEST_RESOLV": resolv,
               "WCTEST_UID": str(UID), "WCTEST_GID": str(os.getgid())}
        flat = ["flatpak", "run", "--nosocket=x11", "--nosocket=fallback-x11"]
        if self.vk:
            flat.append(f"--env=VK_LOADER_DRIVERS_SELECT={self.vk}")
            if self.vk != "*nvidia*":
                flat.append("--env=__EGL_VENDOR_LIBRARY_FILENAMES=/usr/lib/"
                            "x86_64-linux-gnu/GL/default/share/glvnd/"
                            "egl_vendor.d/50_mesa.json")
        flat.append(APP)
        cmd = (["systemd-run", "--user", "--scope", "--quiet", f"--unit={unit}",
                "-p", "CPUWeight=1", "-p", "IOWeight=1", "-p", "MemoryMax=4G",
                "-p", "TimeoutStopSec=5", "env", "-i"]
               + [f"{k}={v}" for k, v in env.items()]
               + ["nice", "-n", "19", "unshare", "--user", "--map-root-user",
                  "--net", "--mount", "sh", "-c", NETNS_SH, "sh"] + flat)
        with open(self.log, "ab") as out:
            self.proc = subprocess.Popen(cmd, stdout=out,
                                         stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL,
                                         start_new_session=True)
        self.unit = unit
        mine = os.readlink("/proc/self/ns/net")
        end = time.monotonic() + 15
        while True:
            try:
                if os.readlink(f"/proc/{self.proc.pid}/ns/net") != mine:
                    break
            except OSError:
                pass
            if self.proc.poll() is not None or time.monotonic() > end:
                raise RuntimeError("the Sober launcher never got its own "
                                   "network: " + self.tail())
            time.sleep(0.05)
        r = subprocess.run(
            ["nice", "-n", "19", self.pasta, "--config-net", "-q",
             "-t", "none", "-u", "none", "-T", "none", "-U", "none",
             "--map-host-loopback", "none", "--dns-forward", "169.254.1.1",
             "--pid", os.path.join(self.rt, "pasta.pid"),
             str(self.proc.pid)], capture_output=True, text=True, timeout=30)
        if r.returncode:
            raise RuntimeError("pasta failed: " + r.stderr[-400:])
        open(ready, "w").close()
        self.t0 = time.monotonic()
        # a primeira vez o Sober ainda desempacota o Roblox
        end = self.t0 + (240 if self.n == 1 else 90)
        while True:
            s = self._read_log()
            if "FATAL" in s:
                raise SoberDied("aborted while starting")
            if "did_handle_app_startup" in s:
                break
            if "starting onboarding flow" in s:
                raise RuntimeError("Sober opened its first run screen "
                                   "instead of Roblox")
            if self.proc.poll() is not None:
                raise RuntimeError("Sober quit while starting: " + self.tail())
            if time.monotonic() > end:
                raise RuntimeError("Sober was not ready in time: "
                                   + self.tail())
            time.sleep(0.3)
        end = time.monotonic() + 20
        while self.window() is None:
            if time.monotonic() > end:
                raise RuntimeError("Sober opened no window in the nested KWin")
            time.sleep(0.3)
        self.place()
        self.activate()

    def window(self):
        for w in self.b.kwin.windows():
            if "vinegarhq" in w["cls"].lower() and w["caption"] == "Sober":
                return w
        return None

    def where(self):
        w = self.window()
        return w and ((w["x"], w["y"], w["w"], w["h"]), w["output"])

    def placed(self):
        return self.where() == (GAME, GAME_OUTPUT)

    def place(self, timeout=15.0, still=2.0):
        """Na tela de baixo à esquerda, inteira, como um jogo, e parada lá
        por `still` segundos: o Sober maximizado volta para a tela do
        ponteiro quando se maximiza de novo."""
        end = time.monotonic() + timeout
        since = None
        while True:
            self.ensure()
            if self.placed():
                since = since or time.monotonic()
                if time.monotonic() - since >= still:
                    return
            else:
                since = None
                self.b.kwin.window_js(
                    MAIN, "w.frameGeometry = {x: %d, y: %d, width: %d, "
                    "height: %d}; report({});" % GAME)
            if time.monotonic() > end:
                raise RuntimeError("Sober's window does not stay on the "
                                   f"lower left screen: {self.where()}")
            time.sleep(0.25)

    def activate(self):
        self.b.kwin.window_js(MAIN, "workspace.activeWindow = w; "
                              "report({});")

    def alive(self):
        return (self.proc is not None and self.proc.poll() is None
                and "FATAL" not in self._read_log())

    def ensure(self):
        if not self.alive():
            raise SoberDied((self.tail(300).strip().splitlines() or
                             ["gone"])[-1][:160])

    def _mine(self):
        """As instâncias do Flatpak deste Sober: as que estão no runtime
        dele, nunca uma que também esteja no de quem roda."""
        try:
            ours = [d for d in os.listdir(os.path.join(self.rt, ".flatpak"))
                    if d.isdigit()]
        except OSError:
            return []
        theirs = user_instances()
        return [d for d in ours if d not in theirs]

    def stop(self):
        for i in self._mine():
            stop_unit(f"app-flatpak-{APP}-{i}.scope")
        if self.unit:
            stop_unit(self.unit + ".scope")
            self.unit = None
        if self.proc is not None:
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                pass
            self.proc = None
        pid_file = os.path.join(self.rt, "pasta.pid")
        try:
            with open(pid_file) as fh:
                pid = int(fh.read().strip())
            with open(f"/proc/{pid}/comm") as fh:
                if fh.read().strip().startswith(("pasta", "passt")):
                    os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        try:
            os.remove(pid_file)
        except OSError:
            pass
        end = time.monotonic() + 10
        while self.window() is not None and time.monotonic() < end:
            time.sleep(0.2)

    def close(self):
        try:
            self.stop()
        finally:
            shutil.rmtree(self.home, ignore_errors=True)
            shutil.rmtree(self.rt, ignore_errors=True)


# ========================================================= o teclado ==
def grab_probe(node):
    """EBUSY: alguém segura o device com EVIOCGRAB."""
    try:
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        fcntl.ioctl(fd, EVIOCGRAB, 1)
    except OSError as e:
        return e.errno == errno.EBUSY
    else:
        fcntl.ioctl(fd, EVIOCGRAB, 0)
        return False
    finally:
        os.close(fd)


class Keyboard:
    """wctest-kbd. Cada evento só sai com o device capturado pelo WayClick
    da caixa (ele diz que segura, e a prova dá EBUSY); sem isso o teste para
    em vez de apertar."""

    def __init__(self, h, held_path):
        self.dev = h.fake_keyboard()
        self.held_path = held_path

    def held(self):
        try:
            with open(self.held_path) as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            return False
        return (time.monotonic() - float(st.get("t", 0)) < HELD_MAX_AGE
                and self.dev.node in st.get("held", []))

    def safe(self):
        return self.held() and grab_probe(self.dev.node)

    def wait_free(self, timeout=10.0):
        """Ninguém segura o teclado: a próxima captura será a do WayClick
        da caixa que vai abrir."""
        end = time.monotonic() + timeout
        while grab_probe(self.dev.node):
            if time.monotonic() > end:
                raise RuntimeError("something outside the sandbox holds "
                                   "wctest-kbd; refusing to type")
            time.sleep(0.1)

    def wait_grabbed(self, timeout=20.0):
        end = time.monotonic() + timeout
        while not self.safe():
            if time.monotonic() > end:
                raise RuntimeError("the WayClick in the sandbox never grabbed "
                                   "wctest-kbd")
            time.sleep(0.1)

    def key(self, code, down):
        if not self.safe():
            raise RuntimeError("wctest-kbd is not held by the WayClick in "
                               "the sandbox; refusing to type")
        self.dev.key(code, down)

    def tap(self, code, hold=0.04, gap=0.03):
        self.key(code, True)
        time.sleep(hold)
        self.key(code, False)
        time.sleep(gap)

    def chord(self, mod, *codes):
        self.key(mod, True)
        time.sleep(0.05)
        for c in codes:
            self.tap(c)
        self.key(mod, False)
        time.sleep(0.05)

    def close(self):
        self.dev.close()


# ================================================= a tela inicial =====
def _runs(mask):
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1))
            start = None
    return out


def find_buttons(rgb):
    """Na tela inicial do Roblox (área cliente do Sober): o Create Account,
    branco, e logo abaixo o Sign In, só contorno. Procura nas faixas entre
    as bordas e o texto dos botões, onde só há botão."""
    import numpy as np
    a = rgb.astype(np.int16)
    H, W, _ = a.shape
    c = W // 2
    if W < 600 or H < 400:
        return None
    band = np.concatenate([a[:, c - 170:c - 100], a[:, c + 100:c + 170]],
                          axis=1).min(axis=2)
    white = (band > 230).mean(axis=1) > 0.95
    ca = next(((t, b) for t, b in _runs(white) if 30 <= b - t <= 80), None)
    if ca is None:
        return None
    light = (band > 140).mean(axis=1) > 0.9
    rows = [y for y in range(ca[1] + 1, min(H, ca[1] + 120)) if light[y]]
    if len(rows) < 2 or not 30 <= rows[-1] - rows[0] <= 70:
        return None
    top, bottom = rows[0], rows[-1]
    mid = (top + bottom) // 2
    xs = [x for x in range(c - 260, c + 260) if a[mid, x].min() > 140]
    ca_y = (ca[0] + ca[1]) // 2
    cx = [x for x in range(c - 260, c + 260) if a[ca_y, x].min() > 230]
    if not xs or not cx:
        return None
    lo, hi = xs[0], xs[-1]
    if (hi - lo + 1) % 2:
        hi -= 1
    if (bottom - top + 1) % 2:
        bottom -= 1
    return {"signin": (lo, top, hi - lo + 1, bottom - top + 1),
            "white": (cx[0] + 30, ca_y)}


# ============================================== a config dos macros ====
def stopper(x, y):
    """Passo que só para o macro: cor qualquer (tolerância 255) no ponto;
    vista ou não, os dois caminhos param."""
    return {"op": "pixel", "x": x, "y": y, "color": "#000000", "tol": 255,
            "want": "seen", "wait": 0, "act": "none", "ok": "stop",
            "fail": "stop"}


def screen_macros(white, white_rgb):
    wx, wy = white
    seen = "#%02x%02x%02x" % tuple(white_rgb)
    sign_in = {
        "name": "sign in", "code": F9, "mode": "once", "repeat": False,
        "steps": [
            {"op": "image", "path": IMG, "want": "seen", "wait": 6000,
             "act": "left", "ok": "next", "fail": "jump", "fail_to": 5},
            {"op": "image", "path": IMG, "want": "gone", "wait": 6000,
             "act": "none", "ok": "next", "fail": "jump", "fail_to": 7},
            {"op": "notify", "text": "opened"},
            stopper(wx, wy),
            {"op": "notify", "text": "not found"},
            stopper(wx, wy),
            {"op": "notify", "text": "still there"}]}

    def pixel(name, code, color, yes, no):
        return {"name": name, "code": code, "mode": "once", "repeat": False,
                "steps": [
                    {"op": "pixel", "x": wx, "y": wy, "color": color,
                     "tol": 24, "want": "seen", "wait": 1500, "act": "none",
                     "ok": "next", "fail": "jump", "fail_to": 4},
                    {"op": "notify", "text": yes},
                    stopper(wx, wy),
                    {"op": "notify", "text": no}]}
    report = {"name": "report", "code": INSERT, "mode": "once",
              "repeat": False, "steps": [
                  {"op": "notify", "url": "https://discord.com/api/webhooks/"
                   "1/wctest", "text": "wctest report", "shot": True},
                  {"op": "open", "url": "https://example.com/wctest"}]}
    # a cor do botão como "Escolher na tela" pegaria (medida no print do
    # KWin), tolerância 8: a captura tem que trazer a cor exata da tela
    white = pixel("pixel white", F10, seen, "white", "not white")
    white["steps"][0]["tol"] = 8
    return [sign_in, white,
            pixel("pixel black", F11, "#000000", "black", "not black"),
            report]


# ============================================================ o roteiro ==
class Trial:
    def __init__(self, b, h, pasta, version, base):
        self.b, self.h, self.a = b, h, h.a
        self.base = base                 # a config depois dos --import
        self.sober = Sober(b, pasta, version)
        self.kb = None
        self.rec = os.path.join(b.home, "rec", "events.jsonl")
        self.held = os.path.join(b.home, "rec", "held.json")
        self.cfg = os.path.join(b.home, ".config", "wayclick.json")
        self.token = os.path.join(b.home, ".config", "wayclick",
                                  "screen-token")
        self.img = os.path.join(b.home, ".local", "share", "wayclick",
                                "images", "signin.png")
        self.pending = []
        self.target = self.white = self.white_rgb = self.field = None
        self.n_sent = 0

    # ---- resultados: só valem de uma sessão em que o Sober ficou de pé
    def ok(self, label, cond, detail=""):
        self.pending.append((label, bool(cond), detail))

    def session(self, name, fn):
        for attempt in (1, 2):
            self.pending = []
            try:
                fn()
                self.sober.ensure()
            except SoberDied as e:
                print(f"      ({name}: Sober aborted on its own, {e}; "
                      + ("running the session again)" if attempt == 1
                         else "giving up)"), flush=True)
                continue
            except Exception as e:                  # noqa: BLE001
                for label, cond, detail in self.pending:
                    self.h.check(label, cond, detail)
                self.h.check(f"{name} ran to the end", False, repr(e)[:400])
                return
            for label, cond, detail in self.pending:
                self.h.check(label, cond, detail)
            print(f"      ({name}: Sober was up {self.sober.age():.0f} s)",
                  flush=True)
            return
        self.h.check(f"{name}: Sober stayed up for the session", False,
                     "aborted twice")

    # ---- o que o WayClick anotou e o que a ponte entregou
    def events(self, since=0.0, kinds=None):
        out = []
        try:
            with open(self.rec) as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e["t"] >= since and (kinds is None or e["k"] in kinds):
                        out.append(e)
        except OSError:
            pass
        return out

    def keys(self, since):
        return [(e["code"], e["down"], e["t"])
                for e in self.b.bridge_events(since, ("key",))]

    def wait_event(self, since, kind, match=lambda e: True, timeout=10.0):
        end = time.monotonic() + timeout
        while True:
            for e in self.events(since, (kind,)):
                if match(e):
                    return e
            if time.monotonic() > end:
                return None
            self.b.kwin.pump(0.1)

    def message(self, since, text, timeout=10.0):
        return self.wait_event(since, "message", lambda e: e["text"] == text,
                               timeout)

    def note(self, body, timeout=5.0):
        """A notificação que o servidor falso da caixa recebeu."""
        end = time.monotonic() + timeout
        while True:
            self.b.kwin.pump(0.05)
            got = [n for n in self.b.kwin.notes if n["body"] == body]
            if got or time.monotonic() > end:
                return got[-1] if got else None

    # ---- o WayClick na caixa
    def wayclick_stop(self):
        self.b.request(op="stop", name="wayclick")
        try:
            os.remove(self.held)
        except OSError:
            pass

    def wayclick_start(self, fresh_token=False):
        self.wayclick_stop()
        self.kb.wait_free()
        if fresh_token and os.path.exists(self.token):
            os.remove(self.token)
        t = time.monotonic()
        r = self.b.request(op="spawn", name="wayclick", argv=[
            "python3", "-I", "-B", os.path.abspath(__file__), "--wayclick",
            f"/run/user/{UID}/wctest-bridge.sock", REC])
        if not r.get("ok"):
            raise RuntimeError(f"WayClick did not spawn: {r}")
        ready = self.wait_event(t, "ready", timeout=40)
        if ready is None:
            raise RuntimeError("WayClick did not start in the sandbox"
                               + self.b.tail("wayclick"))
        # a janela na tela da direita, longe do jogo
        self.b.kwin.window_js("WayClick", "w.frameGeometry = {x: 2400, y: 100,"
                              " width: w.frameGeometry.width, height: "
                              "w.frameGeometry.height}; report({});")
        self.kb.wait_grabbed()
        if self.sober.alive():
            self.sober.activate()
        time.sleep(0.5)
        return ready

    def write_config(self):
        c = dict(self.base)
        c["macros"] = list(self.base.get("macros", [])) \
            + screen_macros(self.white, self.white_rgb)
        with open(self.cfg + ".tmp", "w") as fh:
            json.dump(c, fh)
        os.replace(self.cfg + ".tmp", self.cfg)

    def begin(self, fresh_token=False):
        """Um Sober novo na tela inicial, a config com os pontos desta
        tela, e o WayClick aberto por cima (ele grava a config ao sair,
        então fecha antes)."""
        self.wayclick_stop()
        self.sober.start()
        self.start_screen()
        self.write_config()
        self.wayclick_start(fresh_token)
        return time.monotonic()

    # ---- o diálogo de compartilhar do KDE
    def dialog(self, timeout=10.0):
        end = time.monotonic() + timeout
        while True:
            for w in self.b.kwin.windows():
                if w["caption"] == SHARE:
                    return w
            if time.monotonic() > end:
                return None
            time.sleep(0.2)

    def picked(self, dlg, key):
        x, y = DLG[key]
        _m, rgb = self.b.kwin.screenshot(area=(dlg["x"] + x - 6,
                                               dlg["y"] + y - 6, 13, 13))
        m = rgb.reshape(-1, 3).mean(axis=0)
        return bool((m[2] - m[0]) > 25)      # marcado: azul do Breeze

    def answer(self, dlg, picks, share=True, allow=True):
        if (dlg["w"], dlg["h"]) != DLG_SIZE:
            raise RuntimeError(f"the share dialog is {dlg['w']}x{dlg['h']}, "
                               f"the test knows {DLG_SIZE}")
        for k in picks:
            x, y = DLG[k]
            self.b.fake.click(dlg["x"] + x, dlg["y"] + y)
            time.sleep(0.4)
        if share and self.picked(dlg, "allow") != allow:
            x, y = DLG["allow"]
            self.b.fake.click(dlg["x"] + x, dlg["y"] + y)
            time.sleep(0.4)
        state = {k: self.picked(dlg, k) for k in list(picks) + ["allow"]}
        x, y = DLG["share" if share else "cancel"]
        self.b.fake.click(dlg["x"] + x, dlg["y"] + y)
        return state

    # ---- a tela do Roblox
    def park(self):
        """O ponteiro na tela da direita, longe do jogo e do WayClick: com
        ele em cima de um botão, o Roblox pinta o botão mais escuro."""
        self.b.fake.absolute(3700, 1000)
        self.b.wl.roundtrip()
        time.sleep(0.3)

    def start_screen(self, timeout=25.0):
        """Acha os botões, recorta o Sign In desta sessão como modelo (o
        fundo de arte pode mudar) e guarda os pontos que os macros usam."""
        import kwin_nested as K
        self.park()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.sober.ensure()
            if not self.sober.placed():
                self.sober.place()
                self.park()
                continue
            w = self.sober.window()
            if w:
                _m, rgb = self.b.kwin.screenshot(
                    area=(w["cx"], w["cy"], w["cw"], w["ch"]))
                found = find_buttons(rgb)
                if found:
                    x, y, tw, th = found["signin"]
                    os.makedirs(os.path.dirname(self.img), exist_ok=True)
                    K.save_png(rgb[y:y + th, x:x + tw].copy(), self.img)
                    self.target = (w["cx"] + x + tw // 2,
                                   w["cy"] + y + th // 2)
                    self.white = (w["cx"] + found["white"][0],
                                  w["cy"] + found["white"][1])
                    self.white_rgb = tuple(int(v) for v in rgb[
                        found["white"][1], found["white"][0]])
                    self.field = (self.target[0], self.target[1] - 74)
                    return True
            time.sleep(0.5)
        raise RuntimeError("the Roblox start screen never showed up")

    def landed(self, since):
        """O clique do passo de imagem: posição na ponte, cursor no KWin e
        o botão esquerdo descendo e subindo."""
        abs_ = self.b.bridge_events(since, ("abs",))
        btn = [(e["code"], e["down"])
               for e in self.b.bridge_events(since, ("btn",))]
        cur = self.b.kwin.cursor()
        tx, ty = self.target
        last = abs_[-1] if abs_ else None
        good = (last is not None and abs(last["x"] - tx) <= 1.5
                and abs(last["y"] - ty) <= 1.5 and cur is not None
                and abs(cur[0] - tx) <= 1 and abs(cur[1] - ty) <= 1
                and btn[:2] == [(272, 1), (272, 0)])
        where = f"({last['x']:.1f}, {last['y']:.1f})" if last else "none"
        return good, (f"target {self.target}, bridge {where}, cursor {cur}, "
                      f"buttons {btn[:4]}")

    def wait_click(self, since, timeout=15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(e["code"] == 272 and not e["down"]
                   for e in self.b.bridge_events(since, ("btn",))):
                return True
            if self.events(since, ("problem",)):
                return False
            self.b.kwin.pump(0.1)
        return False

    # ---- o campo de usuário do Roblox, lido pela área de transferência
    def paste(self):
        r = self.b.run(["sh", "-c", "timeout 2 wl-paste --no-newline "
                        "2>/dev/null"], timeout=10)
        return r.get("out") if r.get("ok") and r.get("code") == 0 else None

    def sentinel(self):
        """Algo que o Roblox nunca copiaria; o wl-copy fica servindo a
        seleção até o Roblox tomar, então a saída vai para o nada."""
        self.n_sent += 1
        s = f"wctest-sentinel-{self.n_sent}"
        self.b.run(["sh", "-c", f"wl-copy {s} >/dev/null 2>&1 </dev/null"],
                   timeout=10)
        return s

    def wait_paste(self, sent, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            v = self.paste()
            if v is not None and v != sent:
                return v
            time.sleep(0.15)
        return None

    def click_field(self):
        self.b.fake.click(*self.field)
        time.sleep(0.3)

    def field_text(self, timeout=6.0):
        """O Roblox lê uma tecla por quadro: o Ctrl+C entra na fila atrás do
        que ainda não foi lido, e o timeout cobre isso."""
        self.sober.ensure()
        s = self.sentinel()
        self.click_field()
        self.kb.chord(CTRL, A, C)
        return self.wait_paste(s, timeout)

    def clear_field(self):
        self.click_field()
        self.kb.chord(CTRL, A)
        self.kb.tap(BKSP)
        time.sleep(0.3)

    def no_leak(self, since, label):
        leaked = sorted({c for c, _d, _t in self.keys(since) if c in HOTKEYS})
        self.ok(label, not leaked, f"leaked {leaked}" if leaked else "")

    # ============================================================ sessões ==
    def warm_up(self):
        """O primeiro Sober desempacota o Roblox; depois disso cada sessão
        abre em segundos."""
        self.wayclick_stop()
        self.sober.start()
        self.start_screen()
        self.ok("the isolated Sober opens Roblox's start screen in the "
                "nested KWin, on the lower left screen",
                self.target is not None and self.sober.placed(),
                f"window {self.sober.where()}, Sign In at {self.target}, "
                f"white point {self.white} {self.white_rgb}")

    def first_use(self):
        """Sem token: o diálogo pergunta, as duas telas, o clique no Sign In,
        e com o formulário aberto, o remap.ahk e o F8."""
        t_session = self.begin(fresh_token=True)
        t0 = time.monotonic()
        self.kb.tap(F9)
        dlg = self.dialog()
        self.ok("first use: KDE's share dialog asks", dlg is not None)
        if dlg is None:
            return
        state = self.answer(dlg, ["v0", "v1"])
        self.ok("dialog: both screens picked, 'allow without asking' on",
                all(state.values()), str(state))
        self.wait_click(t0)
        good, detail = self.landed(t0)
        self.ok("image step finds Sign In on the shared screens and clicks "
                "its centre", good, detail)
        self.ok("wait-until-gone sees the button vanish (Roblox opened "
                "Sign In)", self.message(t0, "opened") is not None)
        self.ok("the portal's restore token is saved",
                os.path.exists(self.token))
        self.remap_and_f8(t_session)

    def remap_and_f8(self, t_session):
        self.clear_field()
        for k in (A, B, C):
            self.kb.tap(k)
        got = self.field_text()
        self.ok("the Roblox field reads back what the relay typed",
                got == "abc", repr(got))
        # ^j::Send, Hello World{Enter}
        self.clear_field()
        t0 = time.monotonic()
        self.kb.chord(CTRL, J)
        time.sleep(1.0)
        ks = self.keys(t0)
        got = self.field_text()
        ctrl = [d for c, d, _t in ks if c == CTRL]
        self.ok("remap.ahk Ctrl+J types Hello World{Enter} into Roblox",
                got == "Hello World", repr(got))
        self.ok("and lets Ctrl go first, without J reaching Roblox",
                not any(c == J for c, _d, _t in ks) and ctrl
                and ctrl[-1] == 0, str([(c, d) for c, d, _t in ks][:6]))
        # CapsLock::Ctrl, copiando o campo com CapsLock+A, CapsLock+C
        s = self.sentinel()
        self.click_field()
        t0 = time.monotonic()
        self.kb.chord(CAPS, A, C)
        got = self.wait_paste(s, 6.0)
        self.ok("remap.ahk CapsLock works as Ctrl in Roblox (it copied the "
                "field)", got == "Hello World"
                and not any(c == CAPS for c, _d, _t in self.keys(t0)),
                repr(got))
        # !a:: Send ^c, Sleep 50, Send ^v, com o cursor no fim
        self.click_field()
        self.kb.tap(END)
        self.kb.chord(ALT, A)
        time.sleep(0.8)
        got = self.field_text()
        self.ok("remap.ahk Alt+A copies and pastes in Roblox",
                got == "Hello WorldHello World", repr(got))
        # F8: a tecla de armar de fábrica, e o F8 do stacked.ahk
        self.clear_field()
        t0 = time.monotonic()
        self.kb.tap(F8)
        time.sleep(0.8)
        armed = [e for e in self.events(t0, ("running",)) if e["on"]]
        clicks = [e for e in self.b.bridge_events(t0, ("btn",))
                  if e["code"] == 272]
        got = self.field_text()
        self.ok("F8 runs stacked.ahk's F8 (types x) and the clicker, armed "
                "by F8 by default, stays off", got == "x" and not armed
                and not clicks, f"field {got!r}, armed {len(armed)}, "
                f"clicks {len(clicks)}")
        self.no_leak(t_session, "no macro hotkey ever reached Roblox")

    def restore_and_steps(self):
        """Com o token: nada de diálogo. Cor (então e senão), webhook com
        prints, abrir link, o clique de novo, e com o formulário aberto o
        SpamE, o keywait e o combo."""
        t_session = self.begin()
        self.park()
        t0 = time.monotonic()
        self.kb.tap(F10)
        e = self.message(t0, "white", 8)
        self.ok("restored: no dialog, and the pixel step sees Create Account's "
                "exact color (then branch, tolerance 8)", e is not None
                and self.dialog(0.1) is None,
                f"{self.white_rgb} at {self.white}: "
                + (e["text"] if e else str(self.events(t0, ("message",)))))
        n = self.note("white")
        self.ok("the notify step reached the desktop notifications",
                n is not None and n["summary"] == "pixel white"
                and n["app"] == "WayClick", str(n))
        t0 = time.monotonic()
        self.kb.tap(F11)
        e = self.message(t0, "not black", 8)
        self.ok("the same pixel is not black (else branch, and only it)",
                e is not None and self.message(t0, "black", 0.2) is None)
        t0 = time.monotonic()
        self.kb.tap(INSERT)
        hook = self.wait_event(t0, "webhook", timeout=10)
        opened = self.wait_event(t0, "open", timeout=5)
        self.check_shots(hook)
        self.ok("open link step", opened is not None
                and opened["url"] == "https://example.com/wctest",
                str(opened))
        t0 = time.monotonic()
        self.kb.tap(F9)
        dlg = self.dialog(3.0)
        self.wait_click(t0)
        good, detail = self.landed(t0)
        self.ok("restored: the image step clicks Sign In with no dialog",
                good and dlg is None, detail)
        self.ok("and Roblox opened Sign In", self.message(t0, "opened")
                is not None)
        self.timing(t_session)

    def check_shots(self, hook):
        """Uma PNG por tela compartilhada, igual ao que o KWin mostra."""
        import numpy as np
        import kwin_nested as K
        if hook is None:
            self.ok("webhook step with screenshots", False, "no webhook")
            return
        shots = []
        for p in hook["images"]:
            host = os.path.join(self.b.home, os.path.relpath(p, BOX_HOME))
            shots.append(K.load_png(host))
        screens = self.b.kwin.screens()["screens"]
        views = [self.b.kwin.screenshot(area=(s["x"], s["y"], s["w"], s["h"]))
                 [1] for s in screens]
        diffs, exact = [], []
        for img in shots:
            best, same = 1.0, 0.0
            for v in views:
                if v.shape == img.shape:
                    d = np.abs(v.astype(np.int16) - img.astype(np.int16))
                    d = d.max(axis=2)
                    if float((d > 24).mean()) < best:
                        best = float((d > 24).mean())
                        same = float((d == 0).mean())
            diffs.append(round(best, 4))
            exact.append(round(same, 4))
        self.ok("webhook step with screenshots: one PNG per shared screen, "
                "each matching what KWin shows", len(shots) == len(screens)
                and all(d < 0.05 for d in diffs) and hook["text"]
                == "wctest report", f"{len(shots)} PNGs, pixels off by more "
                f"than 24: {diffs}, identical: {exact}")

    def timing(self, t_session):
        self.clear_field()
        for k in (A, B, C):
            self.kb.tap(k)
        got = self.field_text()
        self.ok("the Roblox field reads back what the relay typed (again)",
                got == "abc", repr(got))
        # SpamE F1.ahk: F1 liga, 1 s, F1 desliga
        self.clear_field()
        t0 = time.monotonic()
        self.kb.tap(F1)
        time.sleep(1.0)
        self.kb.tap(F1)
        time.sleep(0.3)
        downs = [t for c, d, t in self.keys(t0) if c == E and d == 1]
        gaps = sorted(b - a for a, b in zip(downs, downs[1:]))
        med = gaps[len(gaps) // 2] * 1000 if gaps else 0
        rate = (len(downs) - 1) / (downs[-1] - downs[0]) if gaps else 0
        self.ok("SpamE F1.ahk: F1 starts and stops E every 15.6 ms, like on "
                "Windows", 55 <= rate <= 72 and 15.0 <= med <= 16.3,
                f"{len(downs)} presses, {rate:.1f}/s, median gap {med:.2f} ms")
        t_read = time.monotonic()
        got = self.field_text(timeout=30)
        self.ok("Roblox got every E the bridge delivered, none lost",
                got == "e" * len(downs), f"{len(got or '')} of {len(downs)}"
                f", the field caught up {time.monotonic() - t_read:.1f} s "
                "after F1 off")
        # keywait.ahk: sc029::Send {vk41}
        self.clear_field()
        t0 = time.monotonic()
        self.kb.tap(GRAVE)
        time.sleep(0.4)
        ks = [(c, d) for c, d, _t in self.keys(t0)]
        got = self.field_text()
        self.ok("keywait.ahk: ` types a in Roblox, and ` stays hidden",
                got == "a" and ks == [(A, 1), (A, 0)], f"{got!r} {ks}")
        # keywait.ahk: F4 segura o botão direito até soltar o F4
        t0 = time.monotonic()
        self.kb.key(F4, True)
        time.sleep(0.6)
        t_up = time.monotonic()
        self.kb.key(F4, False)
        time.sleep(0.3)
        btn = [(e["code"], e["down"], e["t"])
               for e in self.b.bridge_events(t0, ("btn",))]
        self.ok("keywait.ahk: F4 holds the right button until F4 is let go",
                [(c, d) for c, d, _t in btn] == [(273, 1), (273, 0)]
                and btn[0][2] - t0 < 0.1 and 0 <= btn[1][2] - t_up < 0.1,
                str([(c, d, round(t - t0, 3)) for c, d, t in btn]))
        # combo.ahk F5: 3 cliques direitos, Shift+a, MouseMove 10, 0 relativo
        self.clear_field()
        t0 = time.monotonic()
        self.kb.tap(F5)
        time.sleep(1.2)
        evs = self.b.bridge_events(t0)
        downs = [e["t"] for e in evs if e["k"] == "btn" and e["code"] == 273
                 and e["down"]]
        gaps = [b - a for a, b in zip(downs, downs[1:])]
        ks = [(e["code"], e["down"]) for e in evs if e["k"] == "key"]
        rel = [e for e in evs if e["k"] == "rel"]
        got = self.field_text()
        self.ok("combo.ahk F5: three right clicks 100 ms apart",
                len(downs) == 3 and all(0.09 <= g <= 0.16 for g in gaps),
                str([round(g, 3) for g in gaps]))
        self.ok("then Shift+a, typed by Roblox as A",
                ks == [(SHIFT, 1), (A, 1), (A, 0), (SHIFT, 0)]
                and got == "A", f"{got!r} {ks}")
        self.ok("then a relative move to the right", rel and all(
            e["dx"] > 0 and e["dy"] == 0 for e in rel),
            str([(round(e["dx"], 2), e["dy"]) for e in rel]))
        # combo.ahk *F6::Send {WheelDown 3}
        t0 = time.monotonic()
        self.kb.tap(F6)
        time.sleep(0.6)
        wh = self.b.bridge_events(t0, ("wheel",))
        self.ok("combo.ahk F6: three wheel notches down", len(wh) == 3
                and all(e["axis"] == 0 and e["v120"] == 120
                        and e["value"] > 0 for e in wh),
                str([(e["axis"], e["value"], e["v120"]) for e in wh]))
        self.no_leak(t_session, "no macro hotkey ever reached Roblox "
                                "(second session)")

    def dialogs(self):
        """Sem token de novo: Cancelar, só a outra tela, a área inteira."""
        self.begin(fresh_token=True)
        t0 = time.monotonic()
        self.kb.tap(F9)
        dlg = self.dialog()
        if dlg is None:
            self.ok("the dialog asks again without a token", False)
            return
        self.answer(dlg, [], share=False)
        p = self.wait_event(t0, "problem", timeout=10)
        self.ok("Cancel: the step says sharing was not allowed, and clicks "
                "nothing", p is not None and "Screen sharing was not allowed."
                in p["text"] and not self.b.bridge_events(t0, ("btn",)),
                p["text"] if p else "no report")
        time.sleep(1.0)
        t0 = time.monotonic()
        self.kb.tap(F9)
        dlg = self.dialog()
        self.ok("after Cancel the next run asks again", dlg is not None)
        if dlg is None:
            return
        game = self.sober.where()           # v1 só é a outra se o jogo
        self.answer(dlg, ["v1"])            # está na v0
        e = self.message(t0, "not found", 15)
        self.ok("sharing only the other screen: Sign In is not seen, the "
                "else branch runs, nothing is clicked", e is not None
                and game == (GAME, GAME_OUTPUT)
                and not self.b.bridge_events(t0, ("btn",))
                and not self.events(t0, ("problem",)),
                f"game {game}, problems "
                f"{str(self.events(t0, ('problem',)))[:200]}")
        self.wayclick_start(fresh_token=True)
        t0 = time.monotonic()
        self.kb.tap(F9)
        dlg = self.dialog()
        if dlg is None:
            self.ok("the dialog asks for the full workspace run", False)
            return
        state = self.answer(dlg, ["ws"])
        self.wait_click(t0)
        good, detail = self.landed(t0)
        self.ok("sharing the full workspace: found and clicked",
                good and all(state.values()), f"{detail} {state}")
        self.ok("and Roblox opened Sign In (full workspace)",
                self.message(t0, "opened") is not None)


# ============================================================= preparo ==
def pasta_path():
    p = os.environ.get("WCTEST_PASTA") or shutil.which("pasta")
    return p if p and os.access(p, os.X_OK) else None


def preflight():
    """O que falta para rodar, ou None."""
    for tool in ("flatpak", "bwrap", "kwin_wayland", "systemd-run", "unshare",
                 "wl-copy", "wl-paste", "kscreen-doctor"):
        if not shutil.which(tool):
            return f"{tool} is not installed"
    if subprocess.run(["flatpak", "info", APP], capture_output=True,
                      timeout=60).returncode:
        return f"Sober ({APP}) is not installed"
    if not any(rel.endswith("base.apk") for _p, rel in apk_files()):
        return "Sober has not downloaded Roblox yet (open Sober once)"
    if not user_app_version():
        return "Sober's state has no app_version (open Sober once)"
    if not pasta_path():
        return ("pasta (package passt) is not installed; the test Sober "
                "needs its own network so it never meets the Sober you may "
                "have open. Set WCTEST_PASTA=/path/to/pasta to use another")
    if not os.access("/dev/uinput", os.W_OK):
        return "no write access to /dev/uinput"
    try:
        import numpy  # noqa: F401
        import gi  # noqa: F401
    except ImportError as e:
        return f"python module missing: {e.name}"
    import kwin_nested as K
    if not (K.XDP and K.XDP_KDE and K.XPS):
        return "xdg-desktop-portal and xdg-desktop-portal-kde are needed"
    return None


def setup_config(b):
    """Config nova na caixa: a tecla de armar de fábrica (F8) e os .ahk
    pelo --import. Devolve os relatórios e a config resultante."""
    cfg = os.path.join(b.home, ".config", "wayclick.json")
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    with open(cfg, "w") as fh:
        json.dump({"language": "en_US", "update_check": False}, fh)
    reports = {}
    for name in AHK:
        r = b.run(["python3", "-I", "-B", os.path.join(REPO, "wayclick.py"),
                   "--import", os.path.join(HERE, "ahk", name)], timeout=60)
        reports[name] = (r.get("code"), r.get("out", "") + r.get("err", ""))
    with open(cfg) as fh:
        return reports, json.load(fh)


def main():
    why = preflight()
    if why:
        print(f"SKIP  {why}")
        return 0
    import harness as h
    import kwin_nested as K
    theirs = user_instances()
    with K.Bench() as b:
        b.layout(["output.Virtual-0.position.0,339",
                  "output.Virtual-1.position.1920,0"])
        scr = b.kwin.screens()
        h.check("the nested KWin has two screens, the left one lower",
                scr and scr["ws"] == [0, 0, 3840, 1419],
                str(scr and scr["ws"]))
        b.kwin.serve_notifications()
        reports, base = setup_config(b)
        h.check("wayclick --import takes the five scripts",
                all(code == 0 for code, _o in reports.values())
                # 13: desde a 1.13.0 o :*:btw:: do combo.ahk vira macro
                and len(base.get("macros", [])) == 13,
                f"{ {k: v[0] for k, v in reports.items()} }, "
                f"{len(base.get('macros', []))} macros")
        h.check("--import warns that stacked.ahk's F8 is the arm hotkey",
                "F8 is also the arm/disarm hotkey" in reports["stacked.ahk"][1])
        h.check("--import says SpamE's SetTimer 1 runs every 15.6 ms",
                "every 15.6 ms" in reports["SpamE F1.ahk"][1])
        t = Trial(b, h, pasta_path(), user_app_version(), base)
        try:
            t.kb = Keyboard(h, t.held)
            t.session("warm-up", t.warm_up)
            t.session("first use", t.first_use)
            t.session("restored token", t.restore_and_steps)
            t.session("dialog choices", t.dialogs)
            problems = [e["text"] for e in t.events(0, ("problem",))
                        if "not allowed" not in e["text"]]
            h.check("WayClick reported no other problem", not problems,
                    "; ".join(problems)[:300])
        finally:
            t.sober.close()
            t.wayclick_stop()
            if t.kb is not None:
                t.kb.close()
    h.check("the Sober instances of whoever runs this are untouched",
            theirs <= user_instances(),
            f"before {sorted(theirs)}, after {sorted(user_instances())}")
    left = [p for p in os.listdir("/var/tmp") if p.startswith("wctest-sober-")]
    h.check("nothing of the test Sober is left behind", not left, str(left))
    return h.finish()


if __name__ == "__main__":
    sys.exit(main())
