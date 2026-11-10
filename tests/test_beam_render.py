# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test of Scripts/beam_render.py: the pixel map against brute force, the values
# against the core, the drawing conventions. Run with a Python with numpy and
# scipy:
#
#   python tests/test_beam_render.py
#
# Reruns itself with the Python of a Code-Aster installation if needed.

from __future__ import print_function

import glob
import math
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(globals().get("__file__", sys.argv[0])))
sys.path.insert(0, os.path.join(_HERE, "..", "Scripts"))
sys.dont_write_bytecode = True
try:
    import numpy  # noqa: F401
    import scipy  # noqa: F401
except ImportError:
    pats = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "code_aster", "*", "*",
                                  "Python*", "python.exe"))
    if not pats:
        print("no Python with numpy and scipy found")
        sys.exit(2)
    sys.exit(subprocess.call([sorted(pats)[-1], os.path.abspath(sys.argv[0])]))

import numpy as np  # noqa: E402
import beam_render as br  # noqa: E402
import section_stress as ss  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (" ({0})".format(detail) if detail != "" else ""))
    if not ok:
        FAILED.append(name)


def near(a, b, tol):
    return abs(a - b) <= tol * max(abs(b), 1e-300)


def brute_force(T, width, height, tol=1e-7):
    """Every pixel against every triangle, with an edge-function test: another
    pixel test than the scanline of the rasteriser. Returns a bool map (covered)
    and the map of pixels near an edge, where either answer is right."""
    px = (np.arange(width * height) % width + 0.5)[None, :]
    py = (np.arange(width * height) // width + 0.5)[None, :]
    covered = np.zeros(width * height, dtype=bool)
    edge = np.zeros(width * height, dtype=bool)
    a, b, c = T[:, 0, :], T[:, 1, :], T[:, 2, :]
    area = ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) -
            (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))[:, None]
    for start in range(0, len(T), 256):
        i = slice(start, start + 256)
        e0 = ((b[i, 0, None] - a[i, 0, None]) * (py - a[i, 1, None]) -
              (b[i, 1, None] - a[i, 1, None]) * (px - a[i, 0, None])) / area[i]
        e1 = ((c[i, 0, None] - b[i, 0, None]) * (py - b[i, 1, None]) -
              (c[i, 1, None] - b[i, 1, None]) * (px - b[i, 0, None])) / area[i]
        e2 = ((a[i, 0, None] - c[i, 0, None]) * (py - c[i, 1, None]) -
              (a[i, 1, None] - c[i, 1, None]) * (px - c[i, 0, None])) / area[i]
        m = np.minimum(np.minimum(e0, e1), e2)
        covered |= (m >= 0.0).any(0)
        edge |= (np.abs(m) <= 1e-6).any(0)
    return covered, edge


# ------------------------------------------------------------------ rasteriser
rng = np.random.RandomState(7)
W, H = 120, 90
T = rng.uniform(-20, 140, size=(40, 3, 2))
pix, tri = br.raster_triangles(T, W, H)
mine = np.zeros(W * H, dtype=bool)
mine[pix] = True
want, edge = brute_force(T, W, H)
bad = int(np.count_nonzero((mine != want) & ~edge))
check("random triangles, partly outside: same pixels as brute force", bad == 0, bad)

# each pixel really lies in the triangle it was assigned to
a, b, c = T[tri, 0], T[tri, 1], T[tri, 2]
xp, yp = pix % W + 0.5, pix // W + 0.5
det = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
l1 = ((xp - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (yp - a[:, 1])) / det
l2 = ((b[:, 0] - a[:, 0]) * (yp - a[:, 1]) - (xp - a[:, 0]) * (b[:, 1] - a[:, 1])) / det
check("pixel assigned to a triangle that holds it",
      float(min(l1.min(), l2.min(), (1 - l1 - l2).min())) > -1e-6)

# ---------------------------------------------------------- a real mesh, pixel map
h, b_, tw, tf = 0.2, 0.1, 0.006, 0.01
loop = [[-b_ / 2, -h / 2], [b_ / 2, -h / 2], [b_ / 2, -h / 2 + tf], [tw / 2, -h / 2 + tf],
        [tw / 2, h / 2 - tf], [b_ / 2, h / 2 - tf], [b_ / 2, h / 2], [-b_ / 2, h / 2],
        [-b_ / 2, h / 2 - tf], [-tw / 2, h / 2 - tf], [-tw / 2, -h / 2 + tf],
        [-b_ / 2, -h / 2 + tf]]
field = ss.SectionField([loop], nu=0.3, n_target=1500)
units = br.UnitFields(field)
loops = [l - field.centroid for l in field.loops_in]
W, H = 320, 240
for view, name in ((None, "fit"), ({"zoom": 6.0, "cx": 0.02, "cy": 0.06}, "zoomed and clipped")):
    scene = br.Scene(field.xy, field.elements, loops, W, H, view)
    v_all = field.elements[:, br.SUB_TRIANGLES].reshape(-1, 3)
    pn = np.column_stack(scene.pixel_of(field.xy[:, 0], field.xy[:, 1]))
    mine = np.zeros(W * H, dtype=bool)
    mine[scene.pix] = True
    want, edge = brute_force(pn[v_all], W, H)
    bad = int(np.count_nonzero((mine != want) & ~edge))
    check("I section, {0}: same pixels as brute force".format(name), bad == 0 and scene.n_pixels > 500,
          (bad, scene.n_pixels))
    # linear field: the weights must give it back exactly, at the pixel centre
    q = (2.5 * field.xy[:, 0] - 1.5 * field.xy[:, 1] + 0.75).astype(np.float32)
    got = scene.P.dot(q)
    x, y = scene.pix % W + 0.5, scene.pix // W + 0.5
    exact = 2.5 * (x / scene.s + scene.x0) - 1.5 * (scene.y1 - y / scene.s) + 0.75
    err = float(np.abs(got - exact).max())
    check("I section, {0}: linear field interpolated exactly".format(name), err < 2e-5, err)
    pick = scene.pix[scene.n_pixels // 2]
    n, wg = scene.value_at(int(pick % W), int(pick // W))
    check("I section, {0}: value_at agrees with the map".format(name),
          near(float(np.dot(q[n], wg)), float(got[scene.n_pixels // 2]), 1e-5))

off = int(np.flatnonzero(scene.index < 0)[0])
check("value_at is None off the material", scene.value_at(off % W, off // W) is None)
check("value_at is None off the frame", scene.value_at(-1, 3) is None and scene.value_at(W, 3) is None)

# ----------------------------------------------------- values against the core
forces = {"N": 1e4, "Vv": 800.0, "Vw": -2500.0, "T": 40.0, "Mv": -700.0, "Mw": 1500.0}
ts, term = field.torque_at_shear_centre(forces)
a = [forces["N"], forces["Vv"], forces["Vw"], ts, forces["Mv"], forces["Mw"]]
sn, tv, tw_ = units.nodal(a)
core = field.stress(forces)
for key, got in (("sigma", sn), ("tau_v", tv), ("tau_w", tw_)):
    want = field.nodal(core[key])
    err = float(np.abs(got - want).max() / max(np.abs(want).max(), 1.0))
    check("unit fields sum to the core: {0}".format(key), err < 2e-6, err)
check("T unit field has no sigma", float(np.abs(units.sigma[3]).max()) == 0.0)
s = field.stress({"Vw": 1.0}, about_shear_centre=False)
ts_only, term_only = field.torque_at_shear_centre({"Vw": 1.0})
sn2, tv2, tw2 = units.nodal([0.0, 0.0, 1.0, ts_only, 0.0, 0.0])
check("Vw alone with its term equals the core",
      float(np.abs(tv2 - field.nodal(s["tau_v"])).max()) < 1e-3 * max(float(np.abs(tv2).max()), 1e-9))

# --------------------------------------------------------------- automatic scale
big = ss.SectionField([loop], nu=0.3, n_target=1500)
values = big.nodal(ss.measures(big.stress({"T": 100.0}))["tau"])
lo, hi = br.scale_range(values, units.outside)
peak = float(values.max())
check("auto scale leaves the corner peak out", hi < peak, (hi, peak))
check("corner zones exist on this section", len(field.corners) == 4 and not units.outside.all())
lo2, hi2 = br.scale_range(np.zeros(5, dtype=np.float32), np.ones(5, dtype=bool))
check("flat field gives a valid scale", hi2 > lo2, (lo2, hi2))

# --------------------------------------------------------------------- drawing
W, H = 240, 180
square = [[-0.05, -0.05], [0.05, -0.05], [0.05, 0.05], [-0.05, 0.05]]
fs = ss.SectionField([square], nu=0.0, n_target=600)
us = br.UnitFields(fs)
loops = [l - fs.centroid for l in fs.loops_in]
scene = br.Scene(fs.xy, fs.elements, loops, W, H, None, points={"G": (0.0, 0.0)})
q = (fs.xy[:, 1] / 0.05).astype(np.float32)        # -1 at the bottom, +1 on top
scene.paint(q, -1.0, 1.0)
frame = scene.frame.reshape(H, W)
col = W // 3
rows = np.flatnonzero(frame[:, col] != br.BACKGROUND)
top = frame[rows[0] + 3, col]
bottom = frame[rows[-1] - 3, col]
lut = scene.lut
check("W points up: high values on top row", abs(int((top >> 16) & 255) - 220) < 25 and int(top & 255) < 40,
      hex(int(top)))
check("low values at the bottom row", int(bottom & 255) > 150 and int((bottom >> 16) & 255) < 25,
      hex(int(bottom)))
check("corner of the frame is background", int(frame[0, 0]) == br.BACKGROUND)
check("every drawn colour is opaque", int((scene.frame >> 24).min()) == 255)

ramp = scene.lut
check("look-up table runs from the first to the last stop of the ramp",
      int(ramp[0]) == 0xFF0000C8 and int(ramp[-1]) == 0xFFDC1414, (hex(int(ramp[0])), hex(int(ramp[-1]))))

# clipping: a value above hi takes the top colour, below lo the bottom colour
scene.paint(q * 100.0, -1.0, 1.0)
inner = scene.frame[scene.pix]
inner = inner[inner != br.BLACK]
uniq = set(int(x) for x in np.unique(inner))
check("values beyond a fixed scale clip to the ends", {int(ramp[0]), int(ramp[-1])} <= uniq and
      len(uniq) < 60, len(uniq))

# outline black, in the corners of the shape
scene.paint(q, -1.0, 1.0)
x, y = scene.pixel_of(0.0, 0.05 - 0.0)
check("outline is black at the top edge",
      int(scene.frame.reshape(H, W)[int(y), int(x) + 20]) == br.BLACK or
      int(scene.frame.reshape(H, W)[int(y) - 1, int(x) + 20]) == br.BLACK)

# arrows: the shear points along +V: to the right of every seed
n = len(fs.xy)
tv = np.ones(n, dtype=np.float32)
tw_ = np.zeros(n, dtype=np.float32)
scene.paint(np.zeros(n, dtype=np.float32), -1.0, 1.0)
base = scene.frame.copy()
top = scene.paint(np.zeros(n, dtype=np.float32), -1.0, 1.0, tv, tw_, 1.0)
drawn = np.flatnonzero((scene.frame == br.BLACK) & (base != br.BLACK))
check("arrows are drawn and report the largest value", len(drawn) > 50 and near(top, 1.0, 1e-5), len(drawn))
# one arrow alone: its neighbours would hide which end carries the head
seed = int(np.argmin(np.hypot(scene.seed_x - W / 2, scene.seed_y - H / 2)))
grid = scene.P_seed, scene.seed_x, scene.seed_y
scene.P_seed = scene.P_seed[seed:seed + 1]
scene.seed_x, scene.seed_y = scene.seed_x[seed:seed + 1], scene.seed_y[seed:seed + 1]
sx, sy = float(scene.seed_x[0]), float(scene.seed_y[0])
scene.paint(np.zeros(n, dtype=np.float32), -1.0, 1.0, tv, tw_, 1.0)
drawn = np.flatnonzero((scene.frame == br.BLACK) & (base != br.BLACK))
xs, ys = drawn % W + 0.5, drawn // W + 0.5
barbs = xs[np.abs(ys - sy) > 1.5]
check("arrow head on the +V side", len(barbs) > 0 and float(barbs.mean()) > sx + 0.5,
      float(barbs.mean() - sx) if len(barbs) else "")
scene.paint(np.zeros(n, dtype=np.float32), -1.0, 1.0, tw_, tv, 1.0)
drawn = np.flatnonzero((scene.frame == br.BLACK) & (base != br.BLACK))
xs, ys = drawn % W + 0.5, drawn // W + 0.5
barbs = ys[np.abs(xs - sx) > 1.5]
check("arrow head on the +W side (up on the screen)", len(barbs) > 0 and float(barbs.mean()) < sy - 0.5)
scene.P_seed, scene.seed_x, scene.seed_y = grid
# length factor
scene.paint(np.zeros(n, dtype=np.float32), -1.0, 1.0, tv, tw_, 2.0)
longer = np.count_nonzero((scene.frame == br.BLACK) & (base != br.BLACK))
scene.paint(np.zeros(n, dtype=np.float32), -1.0, 1.0, tv, tw_, 0.5)
shorter = np.count_nonzero((scene.frame == br.BLACK) & (base != br.BLACK))
check("arrow length factor changes the length", longer > shorter, (longer, shorter))
check("arrow grid is regular", len(np.unique(scene.seed_x)) * len(np.unique(scene.seed_y)) == len(scene.seed_x))

# markers
scene.paint(q, -1.0, 1.0, markers=[((0.02, 0.02), "peak"), ((-0.02, -0.02), "max")])
fr = scene.frame.reshape(H, W)
x, y = scene.pixel_of(0.02, 0.02)
check("peak marker drawn at its place", int(fr[int(y), int(x)]) == br.PEAK)
x, y = scene.pixel_of(-0.02, -0.02)
ring = fr[int(y) - 9:int(y) + 10, int(x) - 9:int(x) + 10]
check("ring marker drawn at its place", (ring == br.BLACK).sum() > 10 and (ring == br.WHITE).sum() > 10)

# coarse
small = br.Scene(fs.xy, fs.elements, loops, W // br.COARSE, H // br.COARSE, None,
                 margin=br.MARGIN / br.COARSE)
small.paint(q, -1.0, 1.0)
out = np.zeros(W * H, dtype=np.uint32)
br.upscale(small.frame.reshape(small.height, small.width), W, H, out)
scene.paint(q, -1.0, 1.0)
inside = out[scene.pix]
drawn_part = np.count_nonzero(inside != br.BACKGROUND) / float(scene.n_pixels)
check("coarse frame covers the section", drawn_part > 0.8, drawn_part)
high = scene.pix[scene.frame[scene.pix] >> 16 & 255 > 200]
check("coarse frame has the high colours in the same place",
      np.count_nonzero((out[high] >> 16) & 255 > 150) > 0.7 * len(high))

# several sections
tall = (-0.01, -0.1, 0.01, 0.1)
wide = (-0.1, -0.01, 0.1, 0.01)
check("tall sections go side by side", br.tile_grid(4, 800, 400, tall, 20)[:2] == (4, 1),
      br.tile_grid(4, 800, 400, tall, 20))
check("wide sections go under each other", br.tile_grid(4, 400, 800, wide, 20)[:2] == (1, 4),
      br.tile_grid(4, 400, 800, wide, 20))
c, r, tw_px, th_px = br.tile_grid(5, 640, 480, (-1, -1, 1, 1), 20)
check("the grid holds them all", c * r >= 5 and c * tw_px <= 640 and r * th_px <= 480)
box = (-0.2, -0.2, 0.2, 0.2)
sa = br.Scene(fs.xy, fs.elements, loops, 120, 120, None, bounds=box, margin=br.TILE_MARGIN)
check("shared bounds give one scale of length", abs(sa.s - (120 - 2 * br.TILE_MARGIN) / 0.4) < 1e-9, sa.s)
n = len(fs.xy)
tv1 = np.ones(n, dtype=np.float32)
tw1 = np.zeros(n, dtype=np.float32)
check("arrow_top reads the grid", near(sa.arrow_top(tv1, tw1), 1.0, 1e-5))
sa.paint(np.zeros(n, dtype=np.float32), -1.0, 1.0)
base = sa.frame.copy()
sa.paint(np.zeros(n, dtype=np.float32), -1.0, 1.0, tv1, tw1, 1.0)
full = np.count_nonzero((sa.frame == br.BLACK) & (base != br.BLACK))
top = sa.paint(np.zeros(n, dtype=np.float32), -1.0, 1.0, tv1, tw1, 1.0, reference=8.0)
small_a = np.count_nonzero((sa.frame == br.BLACK) & (base != br.BLACK))
check("a higher reference gives shorter arrows", small_a < full and top == 8.0, (small_a, full))

# thick lines on device pixels
s2 = br.Scene(fs.xy, fs.elements, loops, W * 2, H * 2, None, thick=2)
s1 = br.Scene(fs.xy, fs.elements, loops, W, H, None, thick=1)
check("line width follows the device scale", len(s2.outline) > 3 * len(s1.outline),
      (len(s1.outline), len(s2.outline)))
sx1 = br.Scene(fs.xy, fs.elements, loops, 3, 2, None)
sx1.paint(q, -1.0, 1.0)
check("a tiny canvas does not fail", sx1.frame.size == 6)

# speed on a big mesh, outside the checks (the measured limit is the window's)
fb = ss.SectionField([loop], nu=0.3, n_target=12000)
ub = br.UnitFields(fb)
loops = [l - fb.centroid for l in fb.loops_in]
t0 = time.perf_counter()
sb = br.Scene(fb.xy, fb.elements, loops, 1200, 900, None)
t_map = time.perf_counter() - t0
times = []
for i in range(30):
    p = 0.05 * i
    t0 = time.perf_counter()
    sn, tv, tw_ = ub.nodal([5e4 * (1 + math.sin(p)), 2e3, 1e3, 30 * math.cos(p), 1e3, 4e3])
    vm = ss.measures({"sigma": sn, "tau_v": tv, "tau_w": tw_})["vonmises"]
    lo, hi = br.scale_range(vm, ub.outside)
    sb.paint(vm, lo, hi, tv, tw_)
    times.append(time.perf_counter() - t0)
times.sort()
print("speed: {0} nodes, 1200 x 900: pixel map {1:.0f} ms, frame median {2:.1f} ms, worst {3:.1f} ms".format(
    len(fb.nodes), t_map * 1e3, times[15] * 1e3, times[-1] * 1e3))


# ---------------------------------------------------------------- contour lines
def line_map(scene, q, lo, hi, **kw):
    """Which pixels the lines changed: the frame with them against the one without."""
    scene.paint(q, lo, hi, contours=False, **kw)
    plain = scene.frame.copy()
    scene.paint(q, lo, hi, contours=True, **kw)
    return (scene.frame != plain).reshape(scene.height, scene.width)


rect = [[(-0.05, -0.03), (0.05, -0.03), (0.05, 0.03), (-0.05, 0.03)]]
fc = ss.SectionField(rect, nu=0.3, n_target=800)
loops_c = [l - fc.centroid for l in fc.loops_in]
sc = br.Scene(fc.xy, fc.elements, loops_c, 500, 320)
qv = np.asarray(fc.xy[:, 0], dtype=np.float32) * 2.0e9     # a linear field along V
lo, hi = float(qv.min()), float(qv.max())
lines = line_map(sc, qv, lo, hi)
inner = lines.copy()
# away from the outline: the columns where the lines stand
y0 = int(sc.pixel_of(0.0, 0.03)[1]) + 4
y1 = int(sc.pixel_of(0.0, -0.03)[1]) - 4
x0, x1 = int(sc.pixel_of(-0.05, 0.0)[0]) + 4, int(sc.pixel_of(0.05, 0.0)[0]) - 4
cols = np.flatnonzero(inner[y0:y1, x0:x1].any(axis=0)) + x0
want = [sc.pixel_of(lo / 2.0e9 + (hi - lo) / 2.0e9 * i / 5.0, 0.0)[0] for i in range(1, 5)]
check("linear field: one straight line per level inside", len(cols) == 4, list(cols))
check("linear field: on the place of the level, within a pixel",
      len(cols) == 4 and all(abs(c + 1.0 - x) <= 1.0 for c, x in zip(cols, want)), (list(cols), want))
check("linear field: straight, from edge to edge",
      all(inner[y0:y1, c].all() for c in cols) and all(inner[y0:y1, c - 1:c + 2].sum() == y1 - y0 for c in cols))
check("one pixel wide at scale 1", inner[y0:y1, x0:x1].sum() == 4 * (y1 - y0))
lines = line_map(sc, qv, lo, hi)
frame2 = sc.frame.reshape(sc.height, sc.width)
qw = np.asarray(fc.xy[:, 1], dtype=np.float32) * 2.0e9
lines_w = line_map(sc, qw, float(qw.min()), float(qw.max()))
rows = np.flatnonzero(lines_w[y0 - 3:y1 + 3, x0:x1].any(axis=1))
check("linear field along W: horizontal lines", len(rows) == 4, list(rows))
qd = np.asarray(fc.xy[:, 0] + fc.xy[:, 1], dtype=np.float32) * 2.0e9
diag = line_map(sc, qd, float(qd.min()), float(qd.max()))
m = diag[y0:y1, x0:x1]
ys, xs = np.nonzero(m)
pv, pw = sc.x0 + (xs + x0 + 0.5) / sc.s, sc.y1 - (ys + y0 + 0.5) / sc.s
got = np.round((pv + pw - float(qd.min()) / 2.0e9) / ((float(qd.max()) - float(qd.min())) / 2.0e9 / 5.0)).astype(int)
worst = np.abs((pv + pw) - (float(qd.min()) / 2.0e9 + got * (float(qd.max()) - float(qd.min())) / 2.0e9 / 5.0)).max()
check("skew field: every line pixel on a level, within two pixels", worst * sc.s <= 2.0, worst * sc.s)
per_row = m[10:-10].sum(axis=1)
check("skew field: one pixel wide, per line and row", per_row.max() <= 5 and per_row.mean() <= 4.2,
      (int(per_row.max()), float(per_row.mean())))
qa = np.asarray(fc.xy[:, 0] - fc.xy[:, 1], dtype=np.float32) * 2.0e9
other = line_map(sc, qa, float(qa.min()), float(qa.max()))[y0:y1, x0:x1][10:-10].sum(axis=1)
check("the other diagonal the same", other.max() <= 5 and other.mean() <= 4.2,
      (int(other.max()), float(other.mean())))
# no gaps: an 8-connected line reaches from side to side
check("skew field: each column has its line pixels, no gaps", all(m[:, c].any() for c in range(10, m.shape[1] - 10)))

# the levels of the legend, also on a fixed scale
fixed = line_map(sc, qv, -5.0e7, 5.0e7)
cols2 = np.flatnonzero(fixed[y0:y1, x0:x1].any(axis=0)) + x0
want2 = [sc.pixel_of((-5.0e7 + 1.0e8 * i / 5.0) / 2.0e9, 0.0)[0] for i in range(6)]
check("fixed scale: all six levels stand inside the range of the field", len(cols2) == 6 and
      all(abs(c + 1.0 - x) <= 1.0 for c, x in zip(cols2, want2)), (list(cols2), want2))
out = line_map(sc, qv, 5.0e9, 6.0e9)
check("a fixed scale that misses the field: no lines", not out.any())

# empty pixels stay empty, in a hole and outside
box = [[(-0.05, -0.03), (0.05, -0.03), (0.05, 0.03), (-0.05, 0.03)],
       [(-0.04, -0.02), (0.04, -0.02), (0.04, 0.02), (-0.04, 0.02)]]
fh = ss.SectionField(box, nu=0.3, n_target=1500)
sh = br.Scene(fh.xy, fh.elements, [l - fh.centroid for l in fh.loops_in], 500, 320)
qh = np.asarray(fh.xy[:, 0], dtype=np.float32) * 2.0e9
hole = line_map(sh, qh, float(qh.min()), float(qh.max()))
blank = sh.index.reshape(sh.height, sh.width) < 0
check("no line over an empty pixel, not in a hole", not (hole & blank).any() and hole.any())
hx, hy = sh.pixel_of(0.0, 0.0)
check("the hole is white", sh.frame.reshape(sh.height, sh.width)[int(hy), int(hx)] == br.BACKGROUND)

# a peak: the top class does not fill with lines
qp = qv.copy()
near_corner = np.hypot(fc.xy[:, 0] - 0.05, fc.xy[:, 1] - 0.03) < 0.008
qp[near_corner] = 40.0 * hi
plain_lines = line_map(sc, qv, lo, hi)
peak_lines = line_map(sc, qp, lo, hi)
zone = np.zeros_like(peak_lines)
cx, cy = sc.pixel_of(0.05, 0.03)
r = int(0.012 * sc.s)
zone[max(int(cy) - r, 0):int(cy) + r, int(cx) - r:int(cx) + r] = True
check("a peak of 40 times hi: lines only as for one more class",
      (peak_lines & zone).sum() <= (plain_lines & zone).sum() + 8 * r, ((peak_lines & zone).sum(), (plain_lines & zone).sum(), r))

# readable on the colours
sc.paint(qv, lo, hi, contours=True)
frame = sc.frame.reshape(sc.height, sc.width).copy()
left, right = cols[0], cols[-1]
mid = (y0 + y1) // 2
sc.paint(qv, -5.0e7, 5.0e7, contours=True)
dark = sc.frame.reshape(sc.height, sc.width)[mid, cols2[0]]
check("readable: white on the dark blue, black on the rest",
      br.make_line_lut(sc.lut)[0] == br.WHITE and br.make_line_lut(sc.lut)[-1] == br.BLACK and
      frame[mid, right] == br.BLACK and dark == br.WHITE,
      (hex(int(dark)), hex(int(frame[mid, right]))))

# no speckle: a flat field, and a field exactly on a level
flat = np.full(len(fc.xy), 1.0e8, dtype=np.float32) * (1.0 + np.random.RandomState(1).rand(len(fc.xy)).astype(np.float32) * 1e-7)
check("a flat field gives no lines", not line_map(sc, flat, float(flat.min()), float(flat.max())).any())
on_level = np.full(len(fc.xy), 2.0e7, dtype=np.float32)
check("a field exactly on a level gives no speckle", not line_map(sc, on_level, 0.0, 1.0e8).any())

# thick lines at a higher scale, and nothing else moves
st = br.Scene(fc.xy, fc.elements, loops_c, 500, 320, thick=2)
tl = line_map(st, qv, lo, hi)
check("two pixels at thick 2", tl[y0:y1, x0:x1].sum() == 8 * (y1 - y0))
check("lines off: the same frame as before", (lambda a, b: (a == b).all())(
    (sc.paint(qv, lo, hi), sc.frame.copy())[1], (sc.paint(qv, lo, hi, contours=False), sc.frame.copy())[1]))

# speed: a full picture, 29 000 node mesh not needed, the pixels count
sb.paint(vm, lo=float(vm.min()), hi=float(vm.max()), contours=True)
spent = {}
for name, on in (("off", False), ("on", True)):
    rows_t = []
    for i in range(41):
        t0 = time.perf_counter()
        sb.paint(vm, float(vm.min()), float(vm.max()), contours=on)
        rows_t.append(time.perf_counter() - t0)
    spent[name] = sorted(rows_t)[20] * 1e3
print("contour lines: {0} pixels on the material, frame {1:.1f} ms off, {2:.1f} ms on".format(
    sb.n_pixels, spent["off"], spent["on"]))
check("contour lines cost little in a frame", spent["on"] < spent["off"] + 6.0, spent)

print("{0} failed".format(len(FAILED)) if FAILED else "All checks OK")
sys.exit(1 if FAILED else 0)
