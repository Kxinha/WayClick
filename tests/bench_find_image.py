"""A busca de imagem em telas sintéticas: acha no lugar exato, e em quanto
tempo. Só numpy, sem tela nem device nenhum.

Quatro tipos de tela 1920x1080 (interface em blocos, traços finos como texto,
ruído, cena de jogo com degradê e círculos), três de cada, e recortes
aleatórios de 16x16 a 300x150 tirados dela. Conta como achado o lugar exato
ou uma cópia idêntica; o que sobra é listado, com o score do lugar escolhido.

    nice -n 19 python3 tests/bench_find_image.py
"""
import importlib.util, os, sys, time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "wayclick", os.path.join(HERE, "..", "wayclick.py"))
wc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wc)
import numpy as np                                          # noqa: E402

rng = np.random.default_rng(7)
H, W = 1080, 1920


def ui():
    cell = int(rng.integers(8, 33))
    base = rng.integers(0, 256, (H // cell + 1, W // cell + 1, 3))
    img = np.kron(base, np.ones((cell, cell, 1)))[:H, :W] \
        + rng.normal(0, 3, (H, W, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def text():
    img = np.full((H, W, 3), rng.integers(0, 256, 3), np.float64)
    for _ in range(6000):
        x, y = int(rng.integers(0, W - 12)), int(rng.integers(0, H - 12))
        c = rng.integers(0, 256, 3)
        if rng.random() < 0.5:
            img[y, x:x + int(rng.integers(2, 10))] = c
        else:
            img[y:y + int(rng.integers(2, 10)), x] = c
    return img.astype(np.uint8)


def noise():
    img = rng.integers(0, 40, (H, W, 3)).astype(np.uint8)
    for _ in range(300):
        x, y = rng.integers(0, W - 80), rng.integers(0, H - 40)
        img[y:y + rng.integers(8, 40), x:x + rng.integers(8, 80)] = \
            rng.integers(0, 255, 3)
    return img


def game():
    yy, xx = np.mgrid[0:H, 0:W]
    img = np.stack([xx / W * 255, yy / H * 255, (xx + yy) / (W + H) * 255],
                   -1)
    for _ in range(80):
        cx, cy, r = rng.integers(0, W), rng.integers(0, H), rng.integers(10, 80)
        img[(xx - cx) ** 2 + (yy - cy) ** 2 < r * r] = rng.integers(0, 256, 3)
    img += rng.normal(0, 6, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


SIZES = [(16, 16), (24, 32), (40, 60), (64, 64), (80, 120), (150, 300)]
total_ok = total = 0
for name, make in (("ui", ui), ("text", text), ("noise", noise),
                   ("game", game)):
    ok = n = 0
    times, misses = [], []
    for _scene in range(3):
        img = make()
        for th, tw in SIZES:
            for _ in range(4):
                y0 = int(rng.integers(0, H - th))
                x0 = int(rng.integers(0, W - tw))
                tpl = img[y0:y0 + th, x0:x0 + tw].copy()
                t0 = time.perf_counter()
                hit = wc.find_image(img, tpl, 0.85)
                times.append(time.perf_counter() - t0)
                n += 1
                good = hit is not None and (
                    hit[:2] == (x0, y0) or np.array_equal(
                        img[hit[1]:hit[1] + th, hit[0]:hit[0] + tw], tpl))
                ok += good
                if not good:
                    flat = float(wc._gray(np, tpl).std()) < wc.FLAT_STD
                    misses.append(f"{tw}x{th} at {x0},{y0} -> "
                                  + (f"{hit[0]},{hit[1]} ({hit[2]:.3f})"
                                     if hit else "none")
                                  + (", flat" if flat else ""))
    times.sort()
    total_ok += ok
    total += n
    print(f"{name:6s} {ok}/{n} found, median {times[len(times) // 2] * 1000:.0f}"
          f" ms, max {times[-1] * 1000:.0f} ms")
    for m in misses:
        print(f"         {m}")
print(f"{total_ok}/{total} at the exact spot")
