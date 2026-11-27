"""Um KWin de verdade, aninhado e isolado, para testar o WayClick de ponta a
ponta com um programa de verdade na tela.

O que roda e onde:

* uma caixa bwrap sem rede, com home, /tmp e /run vazios, que só enxerga a
  pasta do WayClick (para leitura), a GPU escolhida, /dev/uinput e
  /dev/input. Dentro dela sobem um D-Bus de sessão próprio, que só ativa o
  portal (nada de helper do Flatpak, nada da sessão de quem roda), um
  PipeWire e um WirePlumber próprios, o kwin_wayland do sistema com o
  backend virtual (telas sem monitor, nada aparece na tela de verdade), o
  xdg-desktop-portal e o xdg-desktop-portal-kde de verdade, e o WayClick;
* a ponte: cada device virtual que o WayClick cria é capturado no instante
  em que nasce (EVIOCGRAB, como no harness), então o desktop de verdade
  nunca recebe um evento dele. O descritor capturado vai para a ponte, que
  o lê com o libinput, a mesma biblioteca e as mesmas contas do KWin
  (movimento absoluto esticado sobre a área de trabalho inteira), e entrega
  ao KWin aninhado pelo protocolo org_kde_kwin_fake_input;
* de fora, o teste fala só com os sockets da caixa (Wayland, D-Bus), por
  caminho explícito. Nada usa a sessão gráfica, o D-Bus ou o PipeWire de
  quem roda.

Este arquivo é a biblioteca; o roteiro está em t_sober_kwin.py.
"""
import ctypes, ctypes.util, fcntl, json, os, select, shutil, signal, socket
import struct, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
UID = os.getuid()
SOCKET = "wayland-wctest"
XDP = next((p for p in ("/usr/lib/xdg-desktop-portal",
                        "/usr/libexec/xdg-desktop-portal")
            if os.access(p, os.X_OK)), None)
XDP_KDE = next((p for p in ("/usr/lib/xdg-desktop-portal-kde",
                            "/usr/libexec/xdg-desktop-portal-kde")
                if os.access(p, os.X_OK)), None)
XPS = next((p for p in ("/usr/lib/xdg-permission-store",
                        "/usr/libexec/xdg-permission-store")
            if os.access(p, os.X_OK)), None)
EVIOCGRAB = (1 << 30) | (4 << 16) | (ord("E") << 8) | 0x90


# ================================================================ Wayland ==
class Wayland:
    """Cliente Wayland mínimo, só socket e struct: registro, bind, saídas
    (xdg_output) e pedidos crus. Basta para o fake_input e para saber a
    área de trabalho, sem depender de pywayland."""

    def __init__(self, path, outputs=False):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.next_id = 2
        self.buf = b""
        self.globals = {}
        self.handlers = {1: self._display_ev}
        self.errors = []
        self.outputs = {}            # wl_output id -> dict(x, y, w, h, name)
        self.reg = self.new_id()
        self.handlers[self.reg] = self._registry_ev
        self.send(1, 1, self.u(self.reg))
        self.roundtrip()
        if outputs:
            self._watch_outputs()

    # ---- formato do fio
    @staticmethod
    def u(v):
        return struct.pack("<I", v & 0xffffffff)

    @staticmethod
    def fixed(v):
        return struct.pack("<i", int(round(v * 256)))

    @staticmethod
    def string(t):
        b = t.encode() + b"\0"
        return struct.pack("<I", len(b)) + b + b"\0" * ((4 - len(b) % 4) % 4)

    @staticmethod
    def _str_at(p, off):
        n = struct.unpack_from("<I", p, off)[0]
        return p[off + 4:off + 4 + n - 1].decode("utf-8", "replace"), \
            off + 4 + n + (4 - n % 4) % 4

    def new_id(self):
        n = self.next_id
        self.next_id += 1
        return n

    def send(self, obj, opcode, payload=b""):
        self.s.sendall(struct.pack("<II", obj, ((8 + len(payload)) << 16)
                                   | opcode) + payload)

    def fileno(self):
        return self.s.fileno()

    # ---- eventos
    def _display_ev(self, op, p):
        if op == 0:
            oid, code = struct.unpack_from("<II", p)
            self.errors.append((oid, code, self._str_at(p, 8)[0]))

    def _registry_ev(self, op, p):
        if op == 0:
            name = struct.unpack_from("<I", p)[0]
            iface, off = self._str_at(p, 4)
            self.globals[name] = (iface, struct.unpack_from("<I", p, off)[0])
            if iface == "wl_output" and getattr(self, "_xom", None):
                self._add_output(name)
        elif op == 1:
            name = struct.unpack_from("<I", p)[0]
            self.globals.pop(name, None)
            for oid, o in list(self.outputs.items()):
                if o.get("global") == name:
                    del self.outputs[oid]

    def dispatch(self, timeout=0.0):
        if select.select([self.s], [], [], timeout)[0]:
            data = self.s.recv(1 << 16)
            if not data:
                raise ConnectionError("the compositor closed the connection")
            self.buf += data
        while len(self.buf) >= 8:
            obj, word = struct.unpack_from("<II", self.buf)
            size = word >> 16
            if len(self.buf) < size:
                break
            p, self.buf = self.buf[8:size], self.buf[size:]
            h = self.handlers.get(obj)
            if h:
                h(word & 0xffff, p)

    def roundtrip(self, timeout=5.0):
        cb, done = self.new_id(), []
        self.handlers[cb] = lambda op, p: done.append(1)
        self.send(1, 0, self.u(cb))
        end = time.monotonic() + timeout
        while not done and time.monotonic() < end:
            self.dispatch(0.05)
        self.handlers.pop(cb, None)
        return bool(done)

    def bind(self, iface, version=None, name=None):
        for gname, (i, v) in self.globals.items():
            if i == iface and (name is None or gname == name):
                ver = min(v, version or v)
                oid = self.new_id()
                self.send(self.reg, 0, self.u(gname) + self.string(iface)
                          + self.u(ver) + self.u(oid))
                return oid, ver
        return None, 0

    # ---- saídas: a área de trabalho, como o KWin a vê
    def _watch_outputs(self):
        self._xom, _v = self.bind("zxdg_output_manager_v1", 3)
        for gname, (iface, _v) in list(self.globals.items()):
            if iface == "wl_output":
                self._add_output(gname)
        self.roundtrip()

    def _add_output(self, gname):
        out, _v = self.bind("wl_output", 4, name=gname)
        xo = self.new_id()
        self.send(self._xom, 1, self.u(xo) + self.u(out))
        rec = {"global": gname, "x": 0, "y": 0, "w": 0, "h": 0, "name": ""}
        self.outputs[out] = rec

        def ev(op, p):
            if op == 0:
                rec["x"], rec["y"] = struct.unpack_from("<ii", p)
            elif op == 1:
                rec["w"], rec["h"] = struct.unpack_from("<ii", p)
            elif op == 3:
                rec["name"] = self._str_at(p, 0)[0]
        self.handlers[xo] = ev
        self.handlers[out] = lambda op, p: None

    def workspace(self):
        """Retângulo que cobre todas as telas (lógico): o workspace()->
        geometry() do KWin, sobre o qual ele estica o ponteiro absoluto."""
        outs = [o for o in self.outputs.values() if o["w"] and o["h"]]
        if not outs:
            return None
        x0 = min(o["x"] for o in outs)
        y0 = min(o["y"] for o in outs)
        x1 = max(o["x"] + o["w"] for o in outs)
        y1 = max(o["y"] + o["h"] for o in outs)
        return (x0, y0, x1 - x0, y1 - y0)


class FakeInput:
    """org_kde_kwin_fake_input: o teste (e a ponte) mexendo no KWin
    aninhado. Opcodes do fake-input.xml, versão 6."""
    AUTH, REL, BUTTON, AXIS, ABS, KEY = 0, 1, 2, 3, 9, 10

    def __init__(self, wl):
        self.wl = wl
        self.id, self.version = wl.bind("org_kde_kwin_fake_input", 6)
        if not self.id:
            raise RuntimeError("the compositor has no org_kde_kwin_fake_input")
        wl.send(self.id, self.AUTH, wl.string("wctest")
                + wl.string("isolated WayClick test"))

    def motion(self, dx, dy):
        self.wl.send(self.id, self.REL, self.wl.fixed(dx) + self.wl.fixed(dy))

    def absolute(self, x, y):
        self.wl.send(self.id, self.ABS, self.wl.fixed(x) + self.wl.fixed(y))

    def button(self, code, down):
        self.wl.send(self.id, self.BUTTON, self.wl.u(code)
                     + self.wl.u(1 if down else 0))

    def axis(self, axis, value):
        self.wl.send(self.id, self.AXIS, self.wl.u(axis) + self.wl.fixed(value))

    def key(self, code, down):
        self.wl.send(self.id, self.KEY, self.wl.u(code)
                     + self.wl.u(1 if down else 0))

    def click(self, x, y, code=0x110, hold=0.05):
        self.absolute(x, y)
        self.wl.roundtrip()
        time.sleep(0.05)
        self.button(code, True)
        self.wl.roundtrip()
        time.sleep(hold)
        self.button(code, False)
        self.wl.roundtrip()

    def tap(self, code, hold=0.04):
        self.key(code, True)
        self.wl.roundtrip()
        time.sleep(hold)
        self.key(code, False)
        self.wl.roundtrip()


# ============================================================= KWin D-Bus ==
PROBE_XML = """<node><interface name="org.wctest.Probe">
<method name="Report"><arg type="s" direction="in"/></method></interface></node>"""
NOTIFY_XML = """<node><interface name="org.freedesktop.Notifications">
<method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/>
<arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="as" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
<arg type="u" direction="out"/></method>
<method name="GetCapabilities"><arg type="as" direction="out"/></method>
<method name="GetServerInformation"><arg type="s" direction="out"/><arg type="s" direction="out"/>
<arg type="s" direction="out"/><arg type="s" direction="out"/></method>
<method name="CloseNotification"><arg type="u" direction="in"/></method>
</interface></node>"""


class KWinCtl:
    """O KWin aninhado pelo D-Bus da caixa: scripts que respondem de volta,
    screenshots (ScreenShot2) e um servidor de notificações que só anota."""

    def __init__(self, bus_addr, scripts_dir):
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.scripts_dir = scripts_dir
        self.bus = Gio.DBusConnection.new_for_address_sync(
            bus_addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.ctx = GLib.MainContext.default()
        self.reports = []
        self.notes = []
        probe = Gio.DBusNodeInfo.new_for_xml(PROBE_XML).interfaces[0]
        self.bus.register_object("/probe", probe, self._report, None, None)
        self._own("org.wctest.Probe")

    def _own(self, name):
        GLib = self.GLib
        self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                           "org.freedesktop.DBus", "RequestName",
                           GLib.Variant("(su)", (name, 4)), None,
                           self.Gio.DBusCallFlags.NONE, 5000, None)

    def _report(self, conn, sender, path, iface, method, params, inv):
        self.reports.append(json.loads(params.unpack()[0]))
        inv.return_value(None)

    def serve_notifications(self):
        info = self.Gio.DBusNodeInfo.new_for_xml(NOTIFY_XML).interfaces[0]
        V = self.GLib.Variant

        def call(conn, sender, path, iface, method, params, inv):
            if method == "Notify":
                a = params.unpack()
                self.notes.append({"app": a[0], "summary": a[3],
                                   "body": a[4], "t": time.monotonic()})
                inv.return_value(V("(u)", (len(self.notes),)))
            elif method == "GetCapabilities":
                inv.return_value(V("(as)", (["body"],)))
            elif method == "GetServerInformation":
                inv.return_value(V("(ssss)", ("wctest", "wctest", "1", "1.2")))
            else:
                inv.return_value(None)
        self.bus.register_object("/org/freedesktop/Notifications", info,
                                 call, None, None)
        self._own("org.freedesktop.Notifications")

    def pump(self, seconds=0.0):
        end = time.monotonic() + seconds
        while True:
            while self.ctx.pending():
                self.ctx.iteration(False)
            if time.monotonic() >= end:
                return
            time.sleep(0.01)

    def owned(self, name):
        try:
            return self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                             "org.freedesktop.DBus", "NameHasOwner", "(s)",
                             (name,), "(b)")[0]
        except self.GLib.Error:
            return False

    def call(self, dest, path, iface, method, sig=None, args=(), ret=None,
             timeout=10000):
        GLib = self.GLib
        v = self.bus.call_sync(dest, path, iface, method,
                               GLib.Variant(sig, tuple(args)) if sig else None,
                               GLib.VariantType.new(ret) if ret else None,
                               self.Gio.DBusCallFlags.NONE, timeout, None)
        return v.unpack() if v is not None else None

    def script(self, js, wait=3.0):
        """Roda um script do KWin; ele responde com report(obj)."""
        body = ("function report(o){callDBus('org.wctest.Probe','/probe',"
                "'org.wctest.Probe','Report',JSON.stringify(o));}\n" + js)
        fd, path = tempfile.mkstemp(suffix=".js", dir=self.scripts_dir)
        os.write(fd, body.encode())
        os.close(fd)
        name = "wctest" + os.urandom(4).hex()
        n0 = len(self.reports)
        try:
            sid = self.call("org.kde.KWin", "/Scripting",
                            "org.kde.kwin.Scripting", "loadScript", "(ss)",
                            (path, name), "(i)")[0]
            self.call("org.kde.KWin", f"/Scripting/Script{sid}",
                      "org.kde.kwin.Script", "run")
            end = time.monotonic() + wait
            while len(self.reports) == n0 and time.monotonic() < end:
                self.pump(0.02)
            self.pump(0.03)
        finally:
            try:
                self.call("org.kde.KWin", "/Scripting",
                          "org.kde.kwin.Scripting", "unloadScript", "(s)",
                          (name,), "(b)")
            except self.GLib.Error:
                pass
            os.remove(path)
        return self.reports[n0:]

    def cursor(self):
        r = self.script("report({x: workspace.cursorPos.x, "
                        "y: workspace.cursorPos.y});")
        return (r[0]["x"], r[0]["y"]) if r else None

    WINDOWS_JS = """
var o = []; var ws = workspace.windowList();
for (var i = 0; i < ws.length; i++) {
  var w = ws[i];
  if (!w.normalWindow && !w.dialog) continue;
  var g = w.frameGeometry, b = w.clientGeometry;
  o.push({id: String(w.internalId), caption: w.caption,
          cls: String(w.resourceClass), name: String(w.resourceName),
          x: g.x, y: g.y, w: g.width, h: g.height,
          cx: b.x, cy: b.y, cw: b.width, ch: b.height,
          fullscreen: w.fullScreen, active: w.active, minimized: w.minimized,
          output: w.output ? w.output.name : "", pid: w.pid});
}
report(o);"""

    def windows(self):
        r = self.script(self.WINDOWS_JS)
        return r[0] if r else []

    def window_js(self, match, body, wait=3.0):
        """body roda com `w` = a primeira janela cuja classe ou título contém
        match; report() o que quiser."""
        js = ("var ws = workspace.windowList(); var w = null;\n"
              "for (var i = 0; i < ws.length; i++) {\n"
              "  var c = String(ws[i].resourceClass) + ' ' + ws[i].caption;\n"
              f"  if (c.indexOf({json.dumps(match)}) >= 0 && "
              "(ws[i].normalWindow || ws[i].dialog)) { w = ws[i]; break; }\n}\n"
              "if (!w) { report({missing: true}); } else {\n" + body + "\n}")
        r = self.script(js, wait)
        return r[0] if r else None

    def screens(self):
        r = self.script("""
var o = []; var ss = workspace.screens;
for (var i = 0; i < ss.length; i++) { var g = ss[i].geometry;
  o.push({name: ss[i].name, x: g.x, y: g.y, w: g.width, h: g.height,
          scale: ss[i].devicePixelRatio}); }
var v = workspace.virtualScreenGeometry;
report({screens: o, ws: [v.x, v.y, v.width, v.height]});""")
        return r[0] if r else None

    def screenshot(self, area=None):
        """A área de trabalho inteira (ou area = x, y, w, h lógicos), em
        pixels nativos: (meta, rgb numpy)."""
        import numpy as np
        Gio, GLib = self.Gio, self.GLib
        r, w = os.pipe()
        fdl = Gio.UnixFDList.new()
        idx = fdl.append(w)
        os.close(w)
        opts = {"include-cursor": GLib.Variant("b", False),
                "native-resolution": GLib.Variant("b", True)}
        if area:
            method, sig, args = "CaptureArea", "(iiuua{sv}h)", \
                tuple(int(v) for v in area) + (opts, idx)
        else:
            method, sig, args = "CaptureWorkspace", "(a{sv}h)", (opts, idx)
        try:
            res, _out = self.bus.call_with_unix_fd_list_sync(
                "org.kde.KWin", "/org/kde/KWin/ScreenShot2",
                "org.kde.KWin.ScreenShot2", method, GLib.Variant(sig, args),
                GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE,
                20000, fdl, None)
        finally:
            for fd in fdl.steal_fds():
                os.close(fd)
        meta = res.unpack()[0]
        chunks = []
        while True:
            c = os.read(r, 1 << 20)
            if not c:
                break
            chunks.append(c)
        os.close(r)
        W, H, stride = meta["width"], meta["height"], meta["stride"]
        a = np.frombuffer(b"".join(chunks), np.uint8)[:stride * H]
        a = a.reshape(H, stride)[:, :W * 4].reshape(H, W, 4)
        # QImage: RGB32/ARGB32 (4, 5, 6) são B, G, R, A na memória;
        # RGBX8888/RGBA8888 (16, 17, 18) são R, G, B, A
        rgb = a[..., [2, 1, 0]] if meta["format"] in (4, 5, 6) else a[..., :3]
        return meta, np.ascontiguousarray(rgb)


def save_png(rgb, path):
    import zlib
    import numpy as np
    h, w = rgb.shape[:2]
    raw = np.zeros((h, w * 3 + 1), np.uint8)
    raw[:, 1:] = np.ascontiguousarray(rgb, np.uint8).reshape(h, w * 3)

    def chunk(k, d):
        return (struct.pack(">I", len(d)) + k + d
                + struct.pack(">I", zlib.crc32(k + d) & 0xffffffff))
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n"
                 + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                 + chunk(b"IDAT", zlib.compress(raw.tobytes(), 3))
                 + chunk(b"IEND", b""))


def load_png(path):
    """PNG RGB/RGBA 8 bits sem entrelaçamento (os que o WayClick e este
    arquivo gravam) -> numpy RGB."""
    import zlib
    import numpy as np
    with open(path, "rb") as fh:
        data = fh.read()
    pos, idat, w = 8, b"", 0
    while pos < len(data):
        n, kind = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack_from(">IIBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    ch = {2: 3, 6: 4}[ctype]
    raw = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(
        h, w * ch + 1)
    out = np.zeros((h, w * ch), np.int32)
    prev = np.zeros(w * ch, np.int32)
    for y in range(h):
        f, line = raw[y, 0], raw[y, 1:].astype(np.int32)
        if f == 0:
            cur = line
        elif f == 2:
            cur = (line + prev) & 255
        else:
            cur = np.zeros_like(line)
            for x in range(w * ch):
                a = cur[x - ch] if x >= ch else 0
                b = prev[x]
                c = prev[x - ch] if x >= ch else 0
                if f == 1:
                    cur[x] = (line[x] + a) & 255
                elif f == 3:
                    cur[x] = (line[x] + (a + b) // 2) & 255
                else:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                    cur[x] = (line[x] + pr) & 255
        out[y], prev = cur, cur
    return out.reshape(h, w, ch)[..., :3].astype(np.uint8)


# ================================================================ a ponte ==
LI = {"DEVICE_ADDED": 1, "DEVICE_REMOVED": 2, "KEY": 300, "MOTION": 400,
      "MOTION_ABS": 401, "BUTTON": 402, "WHEEL": 404}


def _libinput():
    lib = ctypes.util.find_library("input")
    if not lib:
        raise RuntimeError("libinput not found")
    L = ctypes.CDLL(lib)
    P = ctypes.c_void_p
    sig = {
        "libinput_path_create_context": (P, [P, P]),
        "libinput_path_add_device": (P, [P, ctypes.c_char_p]),
        "libinput_path_remove_device": (None, [P]),
        "libinput_get_fd": (ctypes.c_int, [P]),
        "libinput_dispatch": (ctypes.c_int, [P]),
        "libinput_get_event": (P, [P]),
        "libinput_event_get_type": (ctypes.c_int, [P]),
        "libinput_event_get_device": (P, [P]),
        "libinput_event_get_pointer_event": (P, [P]),
        "libinput_event_get_keyboard_event": (P, [P]),
        "libinput_event_pointer_get_dx": (ctypes.c_double, [P]),
        "libinput_event_pointer_get_dy": (ctypes.c_double, [P]),
        "libinput_event_pointer_get_absolute_x_transformed":
            (ctypes.c_double, [P, ctypes.c_uint32]),
        "libinput_event_pointer_get_absolute_y_transformed":
            (ctypes.c_double, [P, ctypes.c_uint32]),
        "libinput_event_pointer_get_button": (ctypes.c_uint32, [P]),
        "libinput_event_pointer_get_button_state": (ctypes.c_int, [P]),
        "libinput_event_pointer_has_axis": (ctypes.c_int, [P, ctypes.c_int]),
        "libinput_event_pointer_get_scroll_value":
            (ctypes.c_double, [P, ctypes.c_int]),
        "libinput_event_pointer_get_scroll_value_v120":
            (ctypes.c_double, [P, ctypes.c_int]),
        "libinput_event_keyboard_get_key": (ctypes.c_uint32, [P]),
        "libinput_event_keyboard_get_key_state": (ctypes.c_int, [P]),
        "libinput_event_destroy": (None, [P]),
        "libinput_device_get_name": (ctypes.c_char_p, [P]),
        "libinput_device_get_sysname": (ctypes.c_char_p, [P]),
        "libinput_device_has_capability": (ctypes.c_int, [P, ctypes.c_int]),
    }
    for name, (res, args) in sig.items():
        fn = getattr(L, name)
        fn.restype, fn.argtypes = res, args
    return L


def _udev_ready(node, timeout=3.0):
    """Espera o udev do sistema terminar de classificar o device: o libinput
    escolhe o tipo (mouse, teclado) pelas propriedades que ele grava."""
    try:
        st = os.stat(node)
    except OSError:
        return False
    path = f"/run/udev/data/c{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with open(path) as fh:
                if "E:ID_INPUT=1" in fh.read():
                    return True
        except OSError:
            pass
        time.sleep(0.01)
    return False


def bridge_main(sock_path, log_path, wl_path):
    """Recebe descritores já capturados (SCM_RIGHTS), lê com o libinput e
    entrega ao KWin aninhado pelo fake_input. Cada evento entregue vai para
    log_path, para o teste conferir o que o compositor recebeu."""
    L = _libinput()
    wl = Wayland(wl_path, outputs=True)
    fi = FakeInput(wl)
    wl.roundtrip()
    fds = {}                       # nó -> fd capturado
    OPEN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                            ctypes.c_void_p)
    CLOSE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)

    def _open(path, flags, data):
        fd = fds.get(path.decode())
        return os.dup(fd) if fd is not None else -13

    def _close(fd, data):
        os.close(fd)

    class Iface(ctypes.Structure):
        _fields_ = [("open_restricted", OPEN), ("close_restricted", CLOSE)]
    keep = (OPEN(_open), CLOSE(_close))
    iface = Iface(*keep)
    li = L.libinput_path_create_context(ctypes.byref(iface), None)
    lfd = L.libinput_get_fd(li)
    log = open(log_path, "a", buffering=1)

    def note(**kw):
        kw["t"] = round(time.monotonic(), 6)
        log.write(json.dumps(kw) + "\n")

    if os.path.exists(sock_path):
        os.remove(sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(8)
    conns = []
    names = {}
    note(k="ready", ws=wl.workspace())

    def add(conn):
        try:
            msg, anc, _f, _a = conn.recvmsg(1024, socket.CMSG_SPACE(4))
        except OSError:
            msg, anc = b"", []
        if not msg:
            conns.remove(conn)
            conn.close()
            return
        node = msg.decode().strip()
        got = [struct.unpack("i", d[:4])[0] for lvl, typ, d in anc
               if lvl == socket.SOL_SOCKET and typ == socket.SCM_RIGHTS]
        if not got:
            conn.sendall(b"no fd\n")
            return
        fds[node] = got[0]
        ready = _udev_ready(node)
        dev = L.libinput_path_add_device(li, node.encode())
        name = L.libinput_device_get_name(dev).decode() if dev else "?"
        names[node] = name
        note(k="dev", node=node, name=name, udev=ready, added=bool(dev))
        conn.sendall(b"ok\n" if dev else b"rejected\n")

    def events():
        L.libinput_dispatch(li)
        while True:
            ev = L.libinput_get_event(li)
            if not ev:
                return
            t = L.libinput_event_get_type(ev)
            d = L.libinput_event_get_device(ev)
            dn = L.libinput_device_get_name(d).decode() if d else ""
            if t == LI["MOTION_ABS"]:
                ws = wl.workspace() or (0, 0, 1, 1)
                pe = L.libinput_event_get_pointer_event(ev)
                x = L.libinput_event_pointer_get_absolute_x_transformed(
                    pe, ws[2])
                y = L.libinput_event_pointer_get_absolute_y_transformed(
                    pe, ws[3])
                fi.absolute(x, y)
                note(k="abs", dev=dn, x=x, y=y, ws=ws)
            elif t == LI["MOTION"]:
                pe = L.libinput_event_get_pointer_event(ev)
                dx = L.libinput_event_pointer_get_dx(pe)
                dy = L.libinput_event_pointer_get_dy(pe)
                fi.motion(dx, dy)
                note(k="rel", dev=dn, dx=dx, dy=dy)
            elif t == LI["BUTTON"]:
                pe = L.libinput_event_get_pointer_event(ev)
                b = L.libinput_event_pointer_get_button(pe)
                s = L.libinput_event_pointer_get_button_state(pe)
                fi.button(b, s)
                note(k="btn", dev=dn, code=b, down=s)
            elif t == LI["WHEEL"]:
                pe = L.libinput_event_get_pointer_event(ev)
                for axis in (0, 1):
                    if L.libinput_event_pointer_has_axis(pe, axis):
                        v = L.libinput_event_pointer_get_scroll_value(pe, axis)
                        v120 = L.libinput_event_pointer_get_scroll_value_v120(
                            pe, axis)
                        fi.axis(axis, v)
                        note(k="wheel", dev=dn, axis=axis, value=v,
                             v120=v120)
            elif t == LI["KEY"]:
                ke = L.libinput_event_get_keyboard_event(ev)
                c = L.libinput_event_keyboard_get_key(ke)
                s = L.libinput_event_keyboard_get_key_state(ke)
                fi.key(c, s)
                note(k="key", dev=dn, code=c, down=s)
            elif t == LI["DEVICE_REMOVED"]:
                note(k="gone", dev=dn)
            L.libinput_event_destroy(ev)

    while True:
        rd = select.select([srv, lfd, wl] + conns, [], [], 1.0)[0]
        if srv in rd:
            c, _ = srv.accept()
            conns.append(c)
        for c in [c for c in conns if c in rd]:
            add(c)
        if lfd in rd:
            events()
        if wl in rd:
            wl.dispatch(0)
            if wl.errors:
                note(k="wl-error", errors=wl.errors)
                wl.errors = []


# ============================================================= supervisor ==
BUS_CONF = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <keep_umask/>
  <listen>unix:path={path}</listen>
  <servicedir>{services}</servicedir>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
  <limit name="reply_timeout">300000</limit>
</busconfig>
"""


def inside_main():
    """Dentro da caixa: D-Bus, PipeWire, WirePlumber, KWin, a ponte; depois
    atende o teste por um socket de controle (uma linha JSON por pedido)."""
    if os.environ.get("WCTEST_SANDBOX") != "1":
        sys.exit("--inside only runs in the sandbox")
    home, rt = os.environ["HOME"], os.environ["XDG_RUNTIME_DIR"]
    logs = os.path.join(home, "logs")
    os.makedirs(logs, exist_ok=True)
    procs = {}

    def spawn(name, argv, env=None, cwd=None):
        out = open(os.path.join(logs, name + ".log"), "ab")
        e = dict(os.environ)
        e.update(env or {})
        p = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=e, cwd=cwd or home)
        procs[name] = p
        return p

    def until(cond, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    # D-Bus que só sabe ativar o portal: nada de helper do Flatpak nem de
    # portal de documentos, que devolveriam caminhos desta caixa para fora
    services = os.path.join(home, "dbus-services")
    os.makedirs(services, exist_ok=True)
    for name, exe in (("org.freedesktop.portal.Desktop", XDP),
                      ("org.freedesktop.impl.portal.desktop.kde", XDP_KDE),
                      ("org.freedesktop.impl.portal.PermissionStore", XPS)):
        with open(os.path.join(services, name + ".service"), "w") as fh:
            fh.write(f"[D-BUS Service]\nName={name}\nExec={exe}\n")
    bus_path = os.path.join(rt, "bus")
    conf = os.path.join(home, "session.conf")
    with open(conf, "w") as fh:
        fh.write(BUS_CONF.format(path=bus_path, services=services))
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"
    spawn("dbus", ["dbus-daemon", f"--config-file={conf}", "--nofork",
                   "--nopidfile", "--nosyslog"])
    if not until(lambda: os.path.exists(bus_path), 10):
        sys.exit("dbus-daemon did not start")
    # saída e entrada de som mudas: programa que exige áudio (o Roblox
    # aborta sem) toca para o nada, e o som de quem roda nem é visto
    conf_d = os.path.join(home, ".config", "pipewire", "pipewire.conf.d")
    os.makedirs(conf_d, exist_ok=True)
    with open(os.path.join(conf_d, "wctest-null.conf"), "w") as fh:
        fh.write("context.objects = [\n  { factory = adapter\n    args = {\n"
                 "      factory.name = support.null-audio-sink\n"
                 '      node.name = "wctest-null"\n'
                 '      node.description = "wctest null output"\n'
                 '      media.class = "Audio/Sink"\n'
                 "      audio.position = [ FL FR ]\n"
                 "      object.linger = true\n    }\n  }\n"
                 # e um microfone mudo: o Roblox abre a entrada ao iniciar
                 "  { factory = adapter\n    args = {\n"
                 "      factory.name = support.null-audio-sink\n"
                 '      node.name = "wctest-mic"\n'
                 '      node.description = "wctest silent microphone"\n'
                 '      media.class = "Audio/Source/Virtual"\n'
                 "      audio.position = [ MONO ]\n"
                 "      object.linger = true\n    }\n  }\n]\n")
    spawn("pipewire", ["pipewire"])
    if not until(lambda: os.path.exists(os.path.join(rt, "pipewire-0")), 15):
        sys.exit("pipewire did not start")
    spawn("wireplumber", ["wireplumber", "-p", "policy"])
    if shutil.which("pipewire-pulse"):
        spawn("pipewire-pulse", ["pipewire-pulse"])
    w, h = os.environ.get("WCTEST_SIZE", "1920x1080").split("x")
    n = os.environ.get("WCTEST_OUTPUTS", "2")
    xw = []
    if os.environ.get("WCTEST_XWAYLAND") == "1":
        # o KWin põe o socket do X aqui, mas não cria a pasta (o systemd do
        # desktop é que cria); o /tmp da caixa é dela, vazio
        os.makedirs("/tmp/.X11-unix", exist_ok=True)
        os.chmod("/tmp/.X11-unix", 0o1777)
        xw = ["--xwayland"]
    spawn("kwin", ["kwin_wayland", "--virtual", "--socket", SOCKET,
                   "--width", w, "--height", h, "--output-count", n,
                   "--no-lockscreen", "--no-global-shortcuts",
                   "--no-kactivities"] + xw)
    if not until(lambda: os.path.exists(os.path.join(rt, SOCKET)), 20):
        sys.exit("kwin_wayland did not start")
    bsock = os.path.join(rt, "wctest-bridge.sock")
    spawn("bridge", [sys.executable, "-I", "-B", os.path.abspath(__file__),
                     "--bridge", bsock, os.path.join(logs, "bridge.jsonl"),
                     os.path.join(rt, SOCKET)])
    if not until(lambda: os.path.exists(bsock), 15):
        sys.exit("the input bridge did not start")

    ctl_path = os.path.join(rt, "wctest-ctl.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(ctl_path)
    srv.listen(2)

    def handle(req):
        op = req.get("op")
        if op == "spawn":
            old = procs.get(req["name"])
            if old is not None and old.poll() is None:
                return {"ok": False, "error": "already running"}
            p = spawn(req["name"], req["argv"], req.get("env"))
            return {"ok": True, "pid": p.pid}
        if op == "stop":
            p = procs.get(req["name"])
            if p is None:
                return {"ok": True, "code": None}
            if p.poll() is None:
                p.send_signal(req.get("signal", signal.SIGTERM))
                try:
                    p.wait(req.get("timeout", 10))
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            return {"ok": True, "code": p.returncode}
        if op == "alive":
            p = procs.get(req["name"])
            return {"ok": True, "alive": p is not None and p.poll() is None,
                    "code": None if p is None else p.returncode}
        if op == "run":
            e = dict(os.environ)
            e.update(req.get("env") or {})
            try:
                r = subprocess.run(req["argv"], capture_output=True,
                                   timeout=req.get("timeout", 60), env=e,
                                   cwd=home, stdin=subprocess.DEVNULL)
                return {"ok": True, "code": r.returncode,
                        "out": r.stdout.decode("utf-8", "replace")[-20000:],
                        "err": r.stderr.decode("utf-8", "replace")[-20000:]}
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "timeout"}
        if op == "procs":
            return {"ok": True, "procs": {k: p.poll() for k, p in
                                          procs.items()}}
        return {"ok": False, "error": f"unknown op {op}"}

    try:
        while True:
            c, _ = srv.accept()
            f = c.makefile("rwb", buffering=0)
            for line in f:
                req = json.loads(line)
                if req.get("op") == "quit":
                    f.write(b'{"ok": true}\n')
                    raise SystemExit(0)
                try:
                    resp = handle(req)
                except Exception as e:             # noqa: BLE001
                    resp = {"ok": False, "error": repr(e)}
                f.write((json.dumps(resp) + "\n").encode())
            c.close()
    finally:
        for name in ("wayclick", "bridge", "kwin", "pipewire-pulse",
                     "wireplumber", "pipewire", "dbus"):
            p = procs.pop(name, None)
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        for p in procs.values():
            if p.poll() is None:
                p.kill()


# ========================================================= o lado de fora ==
def render_node():
    """O render node de uma GPU sem monitor ligado, se houver: o KWin
    aninhado e o programa testado rodam nela, e a GPU que desenha o desktop
    de quem roda (e o jogo dele) fica de fora."""
    idle, busy = [], []
    for card in sorted(os.listdir("/sys/class/drm")):
        if not card.startswith("card") or "-" in card:
            continue
        dev = os.path.realpath(f"/sys/class/drm/{card}/device")
        nodes = [n for n in os.listdir(f"{dev}/drm") if n.startswith("render")]
        if not nodes:
            continue
        lit = False
        for conn in os.listdir("/sys/class/drm"):
            if conn.startswith(card + "-"):
                try:
                    with open(f"/sys/class/drm/{conn}/status") as fh:
                        lit = lit or fh.read().strip() == "connected"
                except OSError:
                    pass
        try:
            with open(f"{dev}/vendor") as fh:
                vendor = fh.read().strip()
        except OSError:
            vendor = ""
        (busy if lit else idle).append((f"/dev/dri/{nodes[0]}", vendor))
    return (idle or busy or [(None, "")])[0]


class Bench:
    """Sobe a caixa e fala com ela. Use com `with Bench() as b:`."""

    def __init__(self, size="1920x1080", outputs=2, keep=False,
                 xwayland=False):
        """xwayland: o KWin da caixa sobe um Xwayland também (o X dele fica
        no /tmp e na rede da caixa, longe do X de quem roda); display()
        diz qual é."""
        self.size, self.n_outputs, self.keep = size, outputs, keep
        self.xwayland = xwayland
        self.work = tempfile.mkdtemp(prefix="wctest-kwin-")
        self.rt = os.path.join(self.work, "rt")
        self.home = os.path.join(self.work, "home")
        self.scripts = os.path.join(self.work, "scripts")
        for d in (self.rt, self.home, self.scripts):
            os.makedirs(d)
        os.chmod(self.rt, 0o700)
        self.logs = os.path.join(self.home, "logs")
        self.proc = None
        self.kwin = self.wl = self.fake = None
        self.node, self.vendor = render_node()

    # ---- ciclo de vida
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def env(self):
        h = "/home/wctest"
        return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": h,
                "USER": "wctest", "LOGNAME": "wctest", "LANG": "en_US.UTF-8",
                "XDG_RUNTIME_DIR": f"/run/user/{UID}",
                "XDG_CONFIG_HOME": f"{h}/.config",
                "XDG_DATA_HOME": f"{h}/.local/share",
                "XDG_STATE_HOME": f"{h}/.local/state",
                "XDG_CACHE_HOME": f"{h}/.cache",
                "XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_DESKTOP": "KDE",
                "XDG_SESSION_TYPE": "wayland", "KDE_FULL_SESSION": "true",
                "KDE_SESSION_VERSION": "6", "WAYLAND_DISPLAY": SOCKET,
                "QT_QPA_PLATFORM": "wayland", "PYTHONDONTWRITEBYTECODE": "1",
                "GIO_USE_VFS": "local", "WCTEST_SANDBOX": "1",
                "WCTEST_SIZE": self.size,
                "WCTEST_OUTPUTS": str(self.n_outputs),
                "WCTEST_XWAYLAND": "1" if getattr(self, "xwayland", False)
                else "0"}

    def start(self):
        if self.node is None:
            raise RuntimeError("no GPU render node")
        cmd = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
               "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
               "--tmpfs", "/tmp", "--tmpfs", "/var/tmp", "--tmpfs", "/run",
               "--tmpfs", "/home", "--perms", "1777", "--tmpfs", "/dev/shm",
               "--dev-bind", self.node, self.node,
               "--dev-bind", "/dev/uinput", "/dev/uinput",
               "--dev-bind", "/dev/input", "/dev/input",
               "--ro-bind", "/run/udev", "/run/udev",
               "--dir", "/run/user", "--bind", self.rt, f"/run/user/{UID}",
               "--dir", "/home/wctest", "--bind", self.home, "/home/wctest",
               "--bind", self.scripts, self.scripts,
               "--ro-bind", REPO, REPO, "--chdir", "/home/wctest",
               "--clearenv"]
        for k, v in self.env().items():
            cmd += ["--setenv", k, v]
        cmd += ["nice", "-n", "19", sys.executable, "-I", "-B",
                os.path.abspath(__file__), "--inside"]
        self._out = open(os.path.join(self.work, "inside.log"), "wb")
        self.proc = subprocess.Popen(cmd, stdout=self._out,
                                     stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL,
                                     start_new_session=True)
        ctl = os.path.join(self.rt, "wctest-ctl.sock")
        end = time.monotonic() + 60
        while not os.path.exists(ctl):
            if self.proc.poll() is not None or time.monotonic() > end:
                raise RuntimeError("the sandbox did not come up:\n"
                                   + self.tail("inside", 3000)
                                   + self.tail("kwin", 3000))
            time.sleep(0.1)
        self._connect()

    def _connect(self):
        self.ctl = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.ctl.connect(os.path.join(self.rt, "wctest-ctl.sock"))
        self._ctlf = self.ctl.makefile("rwb", buffering=0)
        self.kwin = KWinCtl(f"unix:path={self.rt}/bus", self.scripts)
        end = time.monotonic() + 20
        while not self.kwin.owned("org.kde.KWin"):
            if time.monotonic() > end:
                raise RuntimeError("KWin is not on the bus")
            time.sleep(0.1)
        self.wl = Wayland(os.path.join(self.rt, SOCKET), outputs=True)
        self.fake = FakeInput(self.wl)
        self.wl.roundtrip()

    @classmethod
    def attach(cls, work):
        """Uma caixa que já está de pé (kwin_nested.py --hold), para depurar
        à mão: o mesmo objeto, sem dono do ciclo de vida."""
        b = cls.__new__(cls)
        b.work, b.keep, b.proc = work, True, None
        b.rt, b.home = os.path.join(work, "rt"), os.path.join(work, "home")
        b.scripts = os.path.join(work, "scripts")
        b.logs = os.path.join(b.home, "logs")
        b.node, b.vendor = render_node()
        b._connect()
        return b

    def detach(self):
        for f in ("_ctlf", "ctl"):
            try:
                getattr(self, f).close()
            except (AttributeError, OSError):
                pass
        try:
            self.kwin.bus.close_sync(None)
            self.wl.s.close()
        except (AttributeError, OSError):
            pass

    def stop(self):
        try:
            if self.proc is not None and self.proc.poll() is None:
                try:
                    self.request(op="quit")
                except OSError:
                    pass
                try:
                    self.proc.wait(15)
                except subprocess.TimeoutExpired:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                    self.proc.wait()
        finally:
            if not self.keep:
                shutil.rmtree(self.work, ignore_errors=True)

    def request(self, **req):
        self._ctlf.write((json.dumps(req) + "\n").encode())
        line = self._ctlf.readline()
        if not line:
            raise OSError("the sandbox closed the control socket")
        return json.loads(line)

    def run(self, argv, timeout=60, env=None):
        return self.request(op="run", argv=argv, timeout=timeout, env=env)

    def display(self, timeout=20.0):
        """O DISPLAY do Xwayland da caixa (":0"...), ou None. O KWin só
        sobe o Xwayland quando um cliente X chega, mas o socket já existe
        desde o começo."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r = self.run(["sh", "-c", "ls /tmp/.X11-unix 2>/dev/null"])
            names = sorted(n for n in (r.get("out") or "").split()
                           if n.startswith("X") and n[1:].isdigit())
            if names:
                return ":" + names[0][1:]
            time.sleep(0.3)
        return None

    def tail(self, name, n=4000):
        p = os.path.join(self.work, "inside.log") if name == "inside" \
            else os.path.join(self.logs, name + ".log")
        try:
            with open(p, "rb") as fh:
                return f"\n----- {name} -----\n" + fh.read()[-n:].decode(
                    "utf-8", "replace")
        except OSError:
            return ""

    # ---- leitura da ponte
    def bridge_events(self, since=0.0, kinds=None):
        out = []
        try:
            with open(os.path.join(self.logs, "bridge.jsonl")) as fh:
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

    def layout(self, spec):
        """kscreen-doctor dentro da caixa, ex.: output.Virtual-0.position.0,339"""
        return self.run(["kscreen-doctor"] + list(spec), timeout=30,
                        env={"KSCREEN_BACKEND_INPROCESS": "1"})


# ================================================== o programa e a ponte ==
def wayclick_hook(sock_path):
    """Gancho para o WayClick dentro da caixa: captura cada device virtual
    dele ao nascer (o harness faz isso) e manda o descritor para a ponte."""
    import harness as h

    def hook(node):
        h._hook(node)                       # abre e captura, antes de tudo
        fd = h.READERS[node]
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(sock_path)
            s.sendmsg([node.encode()], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                         struct.pack("i", fd))])
            s.settimeout(5)
            s.recv(64)
        except OSError:
            pass
        finally:
            s.close()
    return hook


def hold_main(size, outputs):
    """Sobe a caixa e a mantém de pé até SIGTERM ou Ctrl+C; imprime a pasta
    para Bench.attach()."""
    b = Bench(size=size, outputs=int(outputs), keep=True)
    b.start()
    b.detach()
    print(b.work, flush=True)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    while not stop and b.proc.poll() is None:
        time.sleep(0.5)
    if b.proc.poll() is None:
        b.ctl = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        b.ctl.connect(os.path.join(b.rt, "wctest-ctl.sock"))
        b._ctlf = b.ctl.makefile("rwb", buffering=0)
    b.keep = False
    b.stop()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--inside"]:
        inside_main()
    elif sys.argv[1:2] == ["--bridge"]:
        bridge_main(sys.argv[2], sys.argv[3], sys.argv[4])
    elif sys.argv[1:2] == ["--hold"]:
        hold_main(*(sys.argv[2:4] + ["1920x1080", "2"][len(sys.argv[2:4]):]))
    else:
        sys.exit("library: run t_sober_kwin.py")
