"""Captura de tela pelo portal de verdade, dentro de uma sessão de mentira.

Num bwrap sem rede, com home, /tmp e /run vazios e a pasta do WayClick só
para leitura, sobem um D-Bus de sessão, um PipeWire e um WirePlumber
próprios, o xdg-desktop-portal do sistema e, no lugar do backend do
desktop, um backend falso de ScreenCast que publica duas telas sintéticas no
PipeWire. O WayClick fala com o portal como falaria no desktop: pedido,
token, PipeWire, quadros. A tela de verdade nunca é lida, nenhum diálogo
aparece e nada da sessão de quem roda (D-Bus, PipeWire, /run/user) é visto.

    nice -n 19 python3 tests/t_screen_portal.py

Sem bwrap, PipeWire, WirePlumber, xdg-desktop-portal ou GStreamer, sai com
SKIP.
"""
import json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BACKEND = "org.freedesktop.impl.portal.desktop.wctest"
XDP = next((p for p in ("/usr/lib/xdg-desktop-portal",
                        "/usr/libexec/xdg-desktop-portal")
            if os.access(p, os.X_OK)), None)
# nome do nó, pixels (w, h), posição e tamanho lógicos: a 2ª está a 200%
SCREENS = (("wctest-screen-1", (320, 200), (0, 0), (320, 200)),
           ("wctest-screen-2", (400, 250), (320, 0), (200, 125)))
RESTORE = ("wctest", 1, "both screens")


def picture(i):
    import numpy as np
    w, h = SCREENS[i][1]
    rng = np.random.default_rng(100 + i)
    cells = rng.integers(0, 256, (h // 5 + 1, w // 5 + 1, 3))
    return np.kron(cells, np.ones((5, 5, 1)))[:h, :w].astype(np.uint8)


# ------------------------------------------------------------ fora --
def outer():
    missing = [b for b in ("bwrap", "dbus-run-session", "pipewire",
                           "wireplumber", "pw-dump") if not shutil.which(b)]
    if XDP is None:
        missing.append("xdg-desktop-portal")
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        missing += [e for e in ("pipewiresrc", "pipewiresink", "appsrc",
                                "videoconvert", "appsink")
                    if Gst.ElementFactory.find(e) is None]
    except (ImportError, ValueError):
        missing.append("PyGObject with GStreamer")
    try:
        import numpy                                        # noqa: F401
    except ImportError:
        missing.append("numpy")
    if missing:
        print("SKIP  needs " + ", ".join(missing))
        return 0
    uid = os.getuid()
    home = "/home/wctest"
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home,
           "USER": "wctest", "LANG": "C.UTF-8",
           "XDG_RUNTIME_DIR": f"/run/user/{uid}",
           "XDG_CONFIG_HOME": f"{home}/.config",
           "XDG_DATA_HOME": f"{home}/.local/share",
           "XDG_STATE_HOME": f"{home}/.local/state",
           "XDG_CACHE_HOME": f"{home}/.cache",
           "XDG_CURRENT_DESKTOP": "wctest",
           "XDG_DESKTOP_PORTAL_DIR": f"{home}/portals",
           "QT_QPA_PLATFORM": "offscreen", "PYTHONDONTWRITEBYTECODE": "1",
           "GIO_USE_VFS": "local", "WCTEST_SANDBOX": "1"}
    cmd = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
           "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
           "--tmpfs", "/tmp", "--tmpfs", "/var/tmp", "--tmpfs", "/run",
           "--tmpfs", "/home", "--dir", f"/run/user/{uid}",
           "--chmod", "0700", f"/run/user/{uid}", "--dir", home,
           "--ro-bind", REPO, REPO, "--chdir", home, "--clearenv"]
    for k, v in env.items():
        cmd += ["--setenv", k, v]
    cmd += ["dbus-run-session", "--", sys.executable, "-I", "-B",
            os.path.abspath(__file__), "--inside"]
    # o D-Bus da caixa fala muito (ativações, FUSE ausente): só aparece se
    # algo falhar
    r = subprocess.run(cmd, stderr=subprocess.PIPE)
    if r.returncode:
        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
    return r.returncode


# ------------------------------------------------ backend falso --
IMPL = """<node>
 <interface name="org.freedesktop.impl.portal.ScreenCast">
  <method name="CreateSession">
   <arg type="o" name="handle" direction="in"/>
   <arg type="o" name="session_handle" direction="in"/>
   <arg type="s" name="app_id" direction="in"/>
   <arg type="a{sv}" name="options" direction="in"/>
   <arg type="u" name="response" direction="out"/>
   <arg type="a{sv}" name="results" direction="out"/>
  </method>
  <method name="SelectSources">
   <arg type="o" name="handle" direction="in"/>
   <arg type="o" name="session_handle" direction="in"/>
   <arg type="s" name="app_id" direction="in"/>
   <arg type="a{sv}" name="options" direction="in"/>
   <arg type="u" name="response" direction="out"/>
   <arg type="a{sv}" name="results" direction="out"/>
  </method>
  <method name="Start">
   <arg type="o" name="handle" direction="in"/>
   <arg type="o" name="session_handle" direction="in"/>
   <arg type="s" name="app_id" direction="in"/>
   <arg type="s" name="parent_window" direction="in"/>
   <arg type="a{sv}" name="options" direction="in"/>
   <arg type="u" name="response" direction="out"/>
   <arg type="a{sv}" name="results" direction="out"/>
  </method>
  <property name="AvailableSourceTypes" type="u" access="read"/>
  <property name="AvailableCursorModes" type="u" access="read"/>
  <property name="version" type="u" access="read"/>
 </interface>
</node>"""
SESSION = """<node>
 <interface name="org.freedesktop.impl.portal.Session">
  <method name="Close"/>
  <signal name="Closed"/>
  <property name="version" type="u" access="read"/>
 </interface>
</node>"""


def backend(log_path, mode_path):
    """O papel do xdg-desktop-portal-kde: sem diálogo, publica as duas telas
    no PipeWire e responde como o KDE responderia. Cada chamada vai para
    log_path; mode_path diz se aceita, recusa ou fecha logo depois."""
    import numpy as np
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gio, GLib, Gst
    Gst.init(None)
    V = GLib.Variant

    def log(entry):
        with open(log_path, "a") as fh:
            fh.write(json.dumps(entry, default=list) + "\n")

    feeds, ids = [], {}
    for i, (name, (w, h), _pos, _size) in enumerate(SCREENS):
        pipe = Gst.parse_launch(
            "appsrc name=src is-live=true format=time do-timestamp=true "
            "max-buffers=2 leaky-type=downstream "
            f"caps=video/x-raw,format=RGBx,width={w},height={h},"
            "framerate=10/1 ! videoconvert ! pipewiresink mode=provide "
            f'stream-properties="props,node.name={name},'
            'media.class=Video/Source"')
        rgbx = np.zeros((h, w, 4), np.uint8)
        rgbx[..., :3] = picture(i)
        pipe.set_state(Gst.State.PLAYING)
        feeds.append((pipe, pipe.get_by_name("src"), rgbx.tobytes()))

    def push():
        for _pipe, src, data in feeds:
            src.emit("push-buffer", Gst.Buffer.new_wrapped(data))
        return True
    GLib.timeout_add(100, push)

    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    impl = Gio.DBusNodeInfo.new_for_xml(IMPL).interfaces[0]
    sess_info = Gio.DBusNodeInfo.new_for_xml(SESSION).interfaces[0]
    sessions = {}

    def mode():
        try:
            with open(mode_path) as fh:
                return fh.read().strip()
        except OSError:
            return "allow"

    def get_prop(_c, _sender, _path, iface, name):
        if iface == "org.freedesktop.impl.portal.Session":
            return V("u", 1)
        return V("u", {"AvailableSourceTypes": 3, "AvailableCursorModes": 7,
                       "version": 5}[name])

    def call(c, _sender, path, iface, method, params, inv):
        args = params.unpack()
        if iface == "org.freedesktop.impl.portal.Session":
            log({"call": "Session.Close", "session": path})
            reg = sessions.pop(path, None)
            if reg:
                c.unregister_object(reg)
            inv.return_value(None)
            return
        if method == "CreateSession":
            _handle, session, app_id, opts = args
            log({"call": method, "app_id": app_id, "options": opts})
            sessions[session] = c.register_object(session, sess_info, call,
                                                  get_prop, None)
            inv.return_value(V("(ua{sv})", (0, {"session_id": V(
                "s", session.rsplit("/", 1)[-1])})))
        elif method == "SelectSources":
            _handle, session, app_id, opts = args
            log({"call": method, "app_id": app_id, "options": opts})
            inv.return_value(V("(ua{sv})", (0, {})))
        else:
            _handle, session, app_id, _parent, opts = args
            how = mode()
            log({"call": method, "mode": how, "options": opts})
            if how == "deny":
                inv.return_value(V("(ua{sv})", (1, {})))
                return
            streams = [(ids[name], {"position": V("(ii)", pos),
                                    "size": V("(ii)", size),
                                    "source_type": V("u", 1),
                                    "id": V("s", name)})
                       for name, _px, pos, size in SCREENS]
            inv.return_value(V("(ua{sv})", (0, {
                "streams": V("a(ua{sv})", streams),
                "persist_mode": V("u", 2),
                "restore_data": V("(suv)", (RESTORE[0], RESTORE[1],
                                            V("s", RESTORE[2])))})))
            if how == "close":
                def closed():
                    log({"call": "emit Closed", "session": session})
                    c.emit_signal(None, session,
                                  "org.freedesktop.impl.portal.Session",
                                  "Closed", None)
                    return False
                GLib.timeout_add(1500, closed)

    conn.register_object("/org/freedesktop/portal/desktop", impl, call,
                         get_prop, None)

    def find_nodes():
        try:
            out = subprocess.run(["pw-dump"], capture_output=True,
                                 timeout=5).stdout
            objs = json.loads(out or b"[]")
        except (OSError, ValueError, subprocess.SubprocessError):
            return True
        names = {s[0] for s in SCREENS}
        for o in objs:
            props = (o.get("info") or {}).get("props") or {}
            if o.get("type") == "PipeWire:Interface:Node" and \
                    props.get("node.name") in names:
                ids[props["node.name"]] = o["id"]
        if len(ids) < len(SCREENS):
            return True
        log({"call": "nodes", "ids": ids})
        # o nome só aparece com as telas prontas: quem espera por ele já
        # pode pedir
        Gio.bus_own_name_on_connection(conn, BACKEND,
                                       Gio.BusNameOwnerFlags.NONE, None, None)
        return False
    GLib.timeout_add(200, find_nodes)
    GLib.MainLoop().run()


# ------------------------------------------------------------ dentro --
def inside():
    if os.environ.get("WCTEST_SANDBOX") != "1":
        sys.exit("--inside only runs in the sandbox")
    home, rt = os.environ["HOME"], os.environ["XDG_RUNTIME_DIR"]
    logs = os.path.join(home, "logs")
    os.makedirs(logs)
    pdir = os.environ["XDG_DESKTOP_PORTAL_DIR"]
    os.makedirs(pdir)
    with open(os.path.join(pdir, "wctest.portal"), "w") as fh:
        fh.write(f"[portal]\nDBusName={BACKEND}\nInterfaces="
                 "org.freedesktop.impl.portal.ScreenCast;\nUseIn=wctest\n")
    cdir = os.path.join(os.environ["XDG_CONFIG_HOME"], "xdg-desktop-portal")
    os.makedirs(cdir)
    with open(os.path.join(cdir, "wctest-portals.conf"), "w") as fh:
        fh.write("[preferred]\ndefault=wctest\n")
    mode_path = os.path.join(home, "backend-mode")
    blog = os.path.join(home, "backend.jsonl")
    with open(mode_path, "w") as fh:
        fh.write("allow")
    procs = []

    def spawn(name, argv):
        out = open(os.path.join(logs, name + ".log"), "wb")
        procs.append((name, subprocess.Popen(
            argv, stdout=out, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL)))

    def until(cond, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    import gi
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def owned(name):
        return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                             "org.freedesktop.DBus", "NameHasOwner",
                             GLib.Variant("(s)", (name,)),
                             GLib.VariantType.new("(b)"),
                             Gio.DBusCallFlags.NONE, 2000,
                             None).unpack()[0]

    sys.path.insert(0, HERE)
    import harness as h
    from harness import a, check
    a.LANG = "en_US"
    code = 1
    try:
        host = [n for n in os.listdir(rt)
                if n.startswith(("wayland", "pipewire", "pulse"))]
        check("nothing of the real session is visible (display, PipeWire, "
              "sound)", not host and "WAYLAND_DISPLAY" not in os.environ
              and "DISPLAY" not in os.environ, str(host))
        spawn("pipewire", ["pipewire"])
        if not until(lambda: os.path.exists(os.path.join(rt, "pipewire-0")),
                     15):
            raise RuntimeError("pipewire did not start")
        spawn("wireplumber", ["wireplumber", "-p", "policy"])
        spawn("backend", [sys.executable, "-I", "-B",
                          os.path.abspath(__file__), "--backend", blog,
                          mode_path])
        if not until(lambda: owned(BACKEND), 30):
            raise RuntimeError("the fake backend did not come up")
        spawn("portal", [XDP, "--verbose", "--replace"])
        if not until(lambda: owned("org.freedesktop.portal.Desktop"), 30):
            raise RuntimeError("xdg-desktop-portal did not come up")
        code = client(a, h, check, blog, mode_path, until)
    finally:
        for _name, p in reversed(procs):
            p.terminate()
        for _name, p in reversed(procs):
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()
        if code:
            for name, _p in procs:
                path = os.path.join(logs, name + ".log")
                with open(path, "rb") as fh:
                    tail = fh.read()[-3000:].decode("utf-8", "replace")
                print(f"\n----- {name} log (end) -----\n{tail}")
            if os.path.exists(blog):
                with open(blog) as fh:
                    print("----- backend calls -----\n" + fh.read())
    return code


def calls(blog, name):
    with open(blog) as fh:
        return [e for e in map(json.loads, fh) if e["call"] == name]


def client(a, h, check, blog, mode_path, until):
    import numpy as np
    tok = os.path.join(os.environ["XDG_CONFIG_HOME"], "wayclick",
                       "screen-token")
    cap = a.ScreenCapture(tok)
    t0 = time.monotonic()
    ok = cap.ensure(timeout=60)
    check("the portal hands over the screens (request, PipeWire, picture)",
          ok and cap.state == "on", cap.error
          or f"{time.monotonic() - t0:.1f} s")
    sel = calls(blog, "SelectSources")
    opts = sel[0]["options"] if sel else {}
    check("it asks for screens, all of them, without the cursor, and to "
          "remember the choice",
          opts.get("types") == 1 and opts.get("multiple") is True
          and opts.get("cursor_mode") == 1 and opts.get("persist_mode") == 2
          and "restore_data" not in opts, str(opts))
    frames = []
    until(lambda: len(cap.frames()) == 2, 10)
    frames = cap.frames()
    geo = [(f.x, f.y, f.w, f.h, f.rgb.shape) for f in frames]
    check("one frame per screen, placed where the compositor has it",
          geo == [(0, 0, 320, 200, (200, 320, 3)),
                  (320, 0, 200, 125, (250, 400, 3))], str(geo))
    check("and the pixels are exactly the ones published",
          len(frames) == 2 and all(np.array_equal(f.rgb, picture(i))
                                   for i, f in enumerate(frames)))
    pic = picture(1)
    tpl = pic[100:140, 200:260].copy()
    hit = a.locate(frames, tpl, 0.9)
    check("an image on the 200% screen is found at its logical place",
          hit is not None and abs(hit[0] - (320 + 115)) < 0.6
          and abs(hit[1] - 60) < 0.6, str(hit and hit[:2]))
    check("the color of a point is read through it too",
          a.pixel_at(frames, 330, 10) == tuple(int(c) for c in pic[21, 21]))
    first = cap.token
    mode = os.stat(tok).st_mode & 0o777 if os.path.exists(tok) else None
    check("the restore token is saved, readable only by the user",
          bool(first) and mode == 0o600 and open(tok).read() == first,
          f"mode {mode and oct(mode)}")
    cap.stop()
    check("stopping ends the portal session (the sharing icon goes away)",
          cap.state == "off" and cap.frames() == []
          and until(lambda: calls(blog, "Session.Close"), 5))

    cap = a.ScreenCapture(tok)
    ok = cap.ensure(timeout=60)
    sel = calls(blog, "SelectSources")
    opts = sel[-1]["options"] if sel else {}
    check("the next time the saved choice goes back to the desktop, so it "
          "does not ask again", ok and len(sel) == 2
          and opts.get("restore_data") == list(RESTORE), str(opts))
    # a documentação diz que o token vale uma vez; o xdg-desktop-portal
    # 1.22 devolve o mesmo ao restaurar. Vale o que voltar, sempre salvo
    check("and the token that comes back is the one kept for next time",
          cap.token and open(tok).read() == cap.token,
          "same token" if cap.token == first else "new token")
    cap.stop()

    with open(mode_path, "w") as fh:
        fh.write("deny")
    cap = a.ScreenCapture(None)
    ok = cap.ensure(timeout=60)
    check("saying no in the dialog gives a clear error",
          not ok and cap.state == "failed"
          and cap.error == "Screen sharing was not allowed.", cap.error)

    with open(mode_path, "w") as fh:
        fh.write("close")
    cap = a.ScreenCapture(None)
    ok = cap.ensure(timeout=60)
    ended = until(lambda: cap.state == "off", 10)
    check("when the desktop stops the sharing, capture ends and frames stop",
          ok and ended and cap.frames() == [], cap.state)

    with open(mode_path, "w") as fh:
        fh.write("allow")
    a.SCREEN_IDLE_S = 1.0
    cap = a.ScreenCapture(None)
    cap.ensure(timeout=60)
    cap.acquire()
    held = not until(lambda: cap.state != "on", 2.0)
    cap.release()
    idle = until(lambda: cap.state == "off", 5)
    check("while a macro uses it, it stays on; unused, it closes by itself",
          held and idle, f"held {held}, closed {idle}")
    return h.finish()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--backend"]:
        backend(sys.argv[2], sys.argv[3])
    elif sys.argv[1:2] == ["--inside"]:
        sys.exit(inside())
    else:
        sys.exit(outer())
