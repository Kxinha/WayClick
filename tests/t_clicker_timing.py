"""Clicker e relay ao mesmo tempo, no kernel (ver harness.py).

O Clicker só gira à espera abaixo de 1 ms: daí para cima ele dorme, e a
thread do hub, que repassa o teclado capturado, não fica sem a GIL. Mede o
período real em 1 ms e 10 ms, que 0,1 ms ainda passa de 5.000 cliques/s, e o
atraso do relay de teclado com o Clicker rodando.
"""
import inspect, os, statistics, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h                                         # noqa: E402
from harness import a, check                                # noqa: E402

kbd = h.fake_keyboard()
hub = a.HUB()
h.wait_grabbed(hub, [kbd])
ck = h.clone_of(hub, kbd)
vm = a.VirtualMouse()
om = h.Recorder(vm.node)
LEFT, X = a.BTN["Left"], a.KEYS["X"]

for ms, low, high in ((10.0, 9.9, 10.1), (1.0, 0.98, 1.02)):
    om.clear()
    c = a.Clicker(vm, ms, LEFT)
    c.start()
    lat = []
    t_end = time.monotonic() + 1.0
    while time.monotonic() < t_end:
        ck.clear()
        t0 = time.time()
        kbd.key(X, True)
        ck.poll(0.03)
        d = ck.keys(X, 1)
        if d:
            lat.append((d[0][3] - t0) * 1000)
        kbd.key(X, False)
        time.sleep(0.02)
    c.stop()
    c.join()
    om.poll(0.05)
    downs = [e[3] for e in om.keys(LEFT, 1)]
    gaps = [(b - a_) * 1000 for a_, b in zip(downs, downs[1:])]
    med = statistics.median(gaps) if gaps else 0
    check(f"Clicker at {ms:g} ms keeps its period", low <= med <= high,
          f"median {med:.3f} ms over {len(gaps)} clicks")
    if lat:
        check(f"keyboard relay stays fast with it running",
              statistics.median(lat) < 2.0 and max(lat) < 15.0,
              f"median {statistics.median(lat):.3f} ms, max {max(lat):.2f} ms"
              f", {len(lat)} keys")

om.clear()
c = a.Clicker(vm, 0.1, LEFT)
c.start()
time.sleep(1.0)
c.stop()
c.join()
om.poll(0.05)
n = len(om.keys(LEFT, 1))
check("0.1 ms still clicks over 5,000 times a second", n > 5000, f"{n}/s")

# até o Python 3.12, Thread._stop é um método que join() e is_alive() chamam;
# um atributo com esse nome faz os dois darem TypeError (o AppImage e o
# Ubuntu 24.04 usam 3.12, então isto pega o erro mesmo rodando num 3.13+)
threads = [v for v in vars(a).values() if isinstance(v, type)
           and issubclass(v, a.threading.Thread) and v.__module__ == a.__name__]
bad = [t.__name__ for t in threads if "self._stop " in inspect.getsource(t)]
check("no thread class shadows Thread._stop", threads and not bad,
      f"{len(threads)} classes" + (f", bad {bad}" if bad else ""))

hub.stop()
vm.close()
kbd.close()
sys.exit(h.finish())
