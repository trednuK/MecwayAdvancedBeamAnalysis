# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test of Scripts/beam_render3d.py: the camera, the texture warp and the z-buffer
# against exact geometry and a brute-force ray cast per pixel, and the speed.
# Run with a Python with numpy and scipy:
#
#   python tests/test_beam_render3d.py
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
import beam_render3d as b3  # noqa: E402
import section_stress as ss  # noqa: E402

FAILED = []
X_, Y_, Z_ = np.eye(3)


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (" ({0})".format(detail) if detail != "" else ""))
    if not ok:
        FAILED.append(name)


# --------------------------------------------------------------------- helpers
def square_mesh(a, n):
    """A tri6 mesh of the square [-a, a]^2: n x n cells, two triangles each."""
    g = np.linspace(-a, a, 2 * n + 1)
    vv, ww = np.meshgrid(g, g, indexing="xy")
    xy = np.column_stack([vv.ravel(), ww.ravel()])
    m = 2 * n + 1

    def at(i, j):
        return j * m + i

    el = []
    for cj in range(n):
        for ci in range(n):
            i, j = 2 * ci, 2 * cj
            el.append([at(i, j), at(i + 2, j), at(i + 2, j + 2),
                       at(i + 1, j), at(i + 2, j + 1), at(i + 1, j + 1)])
            el.append([at(i, j), at(i + 2, j + 2), at(i, j + 2),
                       at(i + 1, j + 1), at(i + 1, j + 2), at(i, j + 1)])
    loops = [np.array([[-a, -a], [a, -a], [a, a], [-a, a]])]
    return xy, np.array(el, dtype=np.int64), loops


def tile(xy, elements, loops, size, bounds=None, points=None, thick=1):
    """A tile scene as the worker makes one for a sheet."""
    return br.Scene(xy, elements, loops, size[0], size[1], None, thick, points,
                    axes_at_corner=False, margin=br.TILE_MARGIN, bounds=bounds)


def paint(scene, xy, cv, cw, c0=0.0, contours=True, markers=()):
    q = (cv * xy[:, 0] + cw * xy[:, 1] + c0).astype(np.float32)
    lo, hi = float(q.min()), float(q.max())
    if not hi > lo:
        lo, hi = lo - 1.0, hi + 1.0
    scene.paint(q, lo, hi, markers=markers, contours=contours)
    return scene


def section_frame(u):
    """Element axes as Mecway makes them without a vector (beam_model header)."""
    u = np.asarray(u, dtype=float)
    u = u / np.linalg.norm(u)
    v = np.cross(Z_, u)
    if np.linalg.norm(v) < 1e-4:
        v = Y_.copy()
    v /= np.linalg.norm(v)
    return u, v, np.cross(u, v)


def rays(cam, W, H):
    """Pixel centres as rays: a world point on the target plane, and the
    direction of view. Independent of to_screen: built from the basis only."""
    R, U, E = cam.basis()
    xs = np.arange(W * H) % W + 0.5
    ys = np.arange(W * H) // W + 0.5
    cx, cy = 0.5 * W + cam.pan[0], 0.5 * H + cam.pan[1]
    p0 = cam.target + ((xs - cx) / cam.zoom)[:, None] * R - ((ys - cy) / cam.zoom)[:, None] * U
    return p0, -E


def ray_hits(cam, W, H, placement):
    """(v, w) and depth in pixels of every pixel ray on the plane of a placement,
    by intersection of the ray with the plane: None when the ray runs in it."""
    p0, look = rays(cam, W, H)
    n = np.cross(placement.v_axis, placement.w_axis)
    den = float(look.dot(n))
    if abs(den) < 1e-12:
        return None
    t = (placement.origin - p0).dot(n) / den
    p = p0 + t[:, None] * look
    d = p - placement.origin
    m = placement.magnify
    v, w = d.dot(placement.v_axis) / m, d.dot(placement.w_axis) / m
    depth = -cam.zoom * (p - cam.target).dot(cam.eye)
    return v, w, depth


def band_of(cam, placement, scene, texels=2.5, pixels=1.5):
    """Width in section metres of the strip along an edge where the texture and
    the splats may differ from the exact outline: texels of the tile plus pixels
    on the screen, the latter through the shortest side of the projection."""
    R, U, _ = cam.basis()
    P = np.array([[placement.v_axis.dot(R), placement.w_axis.dot(R)],
                  [placement.v_axis.dot(U), placement.w_axis.dot(U)]])
    smin = float(np.linalg.svd(P, compute_uv=False)[-1])
    return texels / scene.s + pixels / (cam.zoom * placement.magnify * max(smin, 1e-12))


def brute_owner(cam, W, H, squares, tie=3.0):
    """The front square at every pixel by a ray cast, and the pixels where
    either answer is right: near an edge (band_of) or two depths within tie."""
    best = np.full(W * H, np.inf)
    owner = np.full(W * H, -1, dtype=np.int64)
    near_depth = np.full(W * H, np.inf)
    hits = []
    for placement, a, scene in squares:
        hit = ray_hits(cam, W, H, placement)
        if hit is None:
            continue
        v, w, depth = hit
        band = band_of(cam, placement, scene)
        r = np.maximum(np.abs(v), np.abs(w))
        inside = r < a - band
        near = np.abs(r - a) <= band
        hits.append((placement.number, inside, depth))
        near_depth = np.where(near, np.minimum(near_depth, depth), near_depth)
        front = inside & (depth < best)
        best = np.where(front, depth, best)
        owner = np.where(front, placement.number, owner)
    amb = near_depth < best + tie
    for number, inside, depth in hits:
        amb |= inside & (owner != number) & (np.abs(depth - best) < tie)
    return owner, amb


# ---------------------------------------------------------------------- camera
cam = b3.Camera(30.0, 20.0, Z_, (1.0, 2.0, 3.0), 250.0, (12.0, -7.0), 640, 480)
R, U, E = cam.basis()
check("camera basis is right-handed and orthonormal",
      np.allclose(np.cross(R, U), E) and np.allclose([R.dot(R), U.dot(U), E.dot(E)], 1.0)
      and abs(R.dot(U)) < 1e-12, (R, U, E))
pt = np.array([[1.3, 2.2, 2.5], [0.0, 0.0, 0.0]])
x, y, d = cam.to_screen(pt)
back = np.array([cam.unproject(x[i], y[i], d[i]) for i in range(2)])
check("to_screen and unproject are inverse", np.allclose(back, pt, atol=1e-12))
c2 = b3.Camera.from_dict(cam.as_dict(), 640, 480)
check("as_dict and from_dict give the same camera",
      np.allclose(np.column_stack(c2.to_screen(pt)), np.column_stack(cam.to_screen(pt))))
probe_pt = np.array([1.7, 1.1, 2.9])
xz, yz, _ = cam.to_screen(probe_pt)
cz = cam.zoom_at(1.7, 100.0, 300.0)
p_under = cam.unproject(100.0, 300.0)
xa, ya, _ = cz.to_screen(p_under)
check("zoom_at keeps the point under the mouse", abs(xa - 100.0) < 1e-9 and abs(ya - 300.0) < 1e-9
      and abs(cz.zoom - 1.7 * cam.zoom) < 1e-9)
cp = cam.pan_by(5.0, -3.0)
xp, yp, _ = cp.to_screen(probe_pt)
check("pan_by moves every point by the same pixels", abs(xp - xz - 5.0) < 1e-9 and abs(yp - yz + 3.0) < 1e-9)
centre_pt = cam.unproject(320.0, 240.0)
cr = cam.rotate(40.0, -25.0)
xr, yr, _ = cr.to_screen(centre_pt)
check("rotate turns about the point at the centre of the screen",
      abs(xr - 320.0) < 1e-9 and abs(yr - 240.0) < 1e-9
      and abs(cr.azimuth - (30.0 - 40.0 * b3.DEG_PER_PX)) < 1e-9
      and abs(cr.elevation - (20.0 - 25.0 * b3.DEG_PER_PX)) < 1e-9)
check("elevation stops at the poles", b3.Camera(0, 0, Z_, (0, 0, 0), 1, (0, 0), 9, 9)
      .rotate(0, 1e6).elevation == 90.0)
# dragging right turns the model right: a point in front (towards the eye) moves right
front_pt = centre_pt + 0.5 * E
x0_, _, _ = cam.to_screen(front_pt)
x1_, _, _ = cam.rotate(10.0, 0.0).to_screen(front_pt)
check("dragging right moves the front of the model to the right", x1_ > x0_, (x0_, x1_))

# default camera: up as Mecway's up-axis option, Y (front at +Z) or Z (front at
# -Y), whatever the chain; X to the right, from the front, a little from the side
# and from above
Y_ = np.array([0.0, 1.0, 0.0])
X_ = np.array([1.0, 0.0, 0.0])
CHAINS = (("along X", [(0, 0, 0), (6, 0, 0)]), ("along -X", [(6, 0, 0), (0, 0, 0)]),
          ("along Y", [(0, 0, 0), (0, 4, 0)]), ("along Y within 20 degrees", [(0, 0, 0), (0.3, 4, 0.2)]),
          ("sloping", [(0, 0, 0), (3, 1, 1)]), ("along Z", [(0, 0, 0), (0, 0, 5)]),
          ("along Z within 20 degrees", [(0, 0, 0), (0.2, -0.3, 4)]))
for z_top, want_up, front in ((False, Y_, Z_), (True, Z_, -Y_)):
    for name, chain in CHAINS:
        name = "{0} up, chain {1}".format("Z" if z_top else "Y", name)
        pts = np.array(chain, dtype=float)
        dc = b3.default_camera(pts, 0.4, 1200, 900, z_top=z_top)
        u, v, w = section_frame(pts[-1] - pts[0])
        R, U, E = dc.basis()
        # the sphere around each chain point with the half size fits the window
        ends = np.vstack([pts + 0.4 * s * e for e in (R, U) for s in (1, -1)])
        xs, ys, _ = dc.to_screen(ends)
        fits = xs.min() >= -1e-6 and xs.max() <= 1200 + 1e-6 and ys.min() >= -1e-6 and ys.max() <= 900 + 1e-6
        tight = max(xs.max() - xs.min() - 1200 + 2 * b3.FIT_MARGIN,
                    ys.max() - ys.min() - 900 + 2 * b3.FIT_MARGIN) > -1e-6
        ok = (np.allclose(dc.up, want_up) and fits and tight and X_.dot(R) > 0.8 and want_up.dot(U) > 0.9 and
              front.dot(E) > 0.5 and abs(E.dot(u)) > math.sin(math.radians(10.0)))
        check("default camera, " + name, ok, (dc.azimuth, dc.elevation, X_.dot(R), want_up.dot(U)))
check("default camera: Y up unless asked", np.allclose(b3.default_camera(
    [(0, 0, 0), (0, 0, 5)], 0.4, 300, 200).up, Y_))
pts = np.array([(0, 0, 0), (6, 0, 0)], dtype=float)
dc = b3.default_camera(pts, 0.4, 1200, 900)
check("default camera along X: from +X, above, in front",
      abs(dc.azimuth - b3.DEFAULT_AZIMUTH) < 1e-9 and abs(dc.elevation - b3.DEFAULT_ELEVATION) < 1e-9 and
      dc.basis()[2][0] > 0 and dc.basis()[2][1] > 0 and dc.basis()[2][2] > 0, (dc.azimuth, dc.elevation))
dc = b3.default_camera(pts, 0.4, 1200, 900, z_top=True)
check("default camera along X with Z up: from +X, above, in front at -Y (azimuth from X)",
      abs(dc.azimuth - (b3.DEFAULT_AZIMUTH - 90.0)) < 1e-9 and abs(dc.elevation - b3.DEFAULT_ELEVATION) < 1e-9 and
      dc.basis()[2][0] > 0 and dc.basis()[2][1] < 0 and dc.basis()[2][2] > 0, (dc.azimuth, dc.elevation))
# every direction: the sections are never seen near their edge
rng = np.random.RandomState(7)
dirs = rng.normal(size=(400, 3))
dirs = np.vstack([dirs / np.linalg.norm(dirs, axis=1)[:, None],
                  [(0.81, 0.0, -0.47), (-0.81, 0.0, 0.47), (0.0, -0.92, 0.39), (0.0, 0.92, -0.39)]])
for z_top, want in ((False, Y_), (True, Z_)):
    worst, bad_up = 1.0, []
    for d in dirs:
        cm = b3.default_camera([(0, 0, 0), tuple(d)], 0.1, 400, 300, z_top=z_top)
        worst = min(worst, abs(cm.basis()[2].dot(d)))
        if not np.allclose(cm.up, want):
            bad_up.append(tuple(np.round(d, 2)))
    check("default camera, any direction, {0} up: never near edge-on, up as Mecway's"
          .format("Z" if z_top else "Y"),
          worst >= math.sin(math.radians(10.0)) and not bad_up, (worst, bad_up[:3]))
dc = b3.default_camera([], 0.4, 300, 200)
check("default camera without points does not fail", dc.zoom > 0)
bad_cam = b3.Camera.from_dict({"azimuth": 10, "elevation": 5, "up": [0, 0, 1],
                               "target": [float("nan"), 0, 0], "zoom": 2.0,
                               "pan": [float("inf"), 0]}, 50, 40)
check("a camera from a bad dict has finite values",
      bool(np.isfinite(bad_cam.target).all()) and all(math.isfinite(p) for p in bad_cam.pan))

def z_up_view(points, half, width, height):
    """The default view before Y became up (Z up, turned -60 degrees from U and
    lifted 20, fitted the same way). The turned views of the kink and the L were
    chosen on it, so they stay on it."""
    p = np.asarray(points, dtype=float)
    u = (p[-1] - p[0]) / np.linalg.norm(p[-1] - p[0])
    az = math.degrees(math.atan2(u[1], u[0])) - 60.0
    el = min(max(math.degrees(math.asin(u[2])) + 20.0, -85.0), 85.0)
    centre = p.mean(0)
    cm = b3.Camera(az, el, Z_, centre, 1.0, (0.0, 0.0), width, height)
    d = p - centre
    rx, ry = d.dot(cm.right), d.dot(cm.upv)
    x0, x1 = rx.min() - half, rx.max() + half
    y0, y1 = ry.min() - half, ry.max() + half
    zoom = min((width - 2 * b3.FIT_MARGIN) / (x1 - x0), (height - 2 * b3.FIT_MARGIN) / (y1 - y0))
    target = centre + 0.5 * (x0 + x1) * cm.right + 0.5 * (y0 + y1) * cm.upv
    return b3.Camera(az, el, Z_, target, zoom, (0.0, 0.0), width, height)


# ---------------------------------------------------------- a square, exact pixels
a = 0.25
xy, el, loops = square_mesh(a, 6)
sq = paint(tile(xy, el, loops, (200, 200)), xy, 1.0, 0.5)
pl = b3.Placement(1, 0.0, (0.0, 0.0, 0.0), Y_, Z_, 1.0)
comp = b3.Composer3D(400, 400)
# head on, one texel per pixel on the pixel grid: the tile itself
ox, oy = 37, 61
cam0 = b3.Camera(0.0, 0.0, Z_, (0.0, 0.0, 0.0), sq.s, (ox - 200.0 - sq.s * sq.x0, oy - 200.0 + sq.s * sq.y1),
                 400, 400)
out = comp.render([(pl, sq)], cam0, coarse=False, axes=False)
fr = out["frame"].view(np.uint32).reshape(400, 400)
sub = fr[oy:oy + 200, ox:ox + 200].ravel()
covered = (sq.index >= 0) | (sq.frame != br.BACKGROUND)
want = np.where(covered, sq.frame, br.BACKGROUND)
rest = fr.copy()
rest[oy:oy + 200, ox:ox + 200] = br.BACKGROUND
check("head on at one texel per pixel the picture is the tile, pixel for pixel",
      np.array_equal(sub, want) and not (rest != br.BACKGROUND).any(),
      int(np.count_nonzero(sub != want)))
check("frame is uint32 H x W, bgra its bytes, every pixel opaque",
      out["frame"].dtype == np.uint32 and out["frame"].shape == (400, 400)
      and out["bgra"].shape == (400, 400, 4) and bool((out["bgra"][:, :, 3] == 255).all()))

for az, elv in ((0.0, 0.0), (30.0, 0.0), (-45.0, 20.0), (60.0, -35.0), (10.0, 80.0), (-120.0, 15.0)):
    cam1 = b3.Camera(az, elv, Z_, (0.0, 0.0, 0.0), 600.0, (3.3, -1.7), 400, 400)
    for coarse in (True, False):
        out = comp.render([(pl, sq)], cam1, coarse=coarse, axes=False)
        own = out["owner"].ravel()
        want, amb = brute_owner(cam1, 400, 400, [(pl, a, sq)])
        bad = int(np.count_nonzero((own != want) & ~amb))
        cosine = abs(cam1.eye.dot(X_))
        area = (2 * a * 600.0) ** 2 * cosine
        n_own = int(np.count_nonzero(own == 1))
        check("square at azimuth {0}, elevation {1}, {2}: the pixels of the exact outline".format(
            az, elv, "coarse" if coarse else "fine"),
            bad == 0 and abs(n_own - area) < 0.03 * area + 4 * 2400 * a * math.sqrt(cosine),
            (bad, n_own, round(area)))

# ---------------------------------------------------- kink with overlapping sections
W, H = 360, 300
kxy, kel, kloops = square_mesh(0.3, 8)
chain = np.array([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)])
ua, va, wa = section_frame(X_)
ub, vb, wb = section_frame(Y_)
sa = paint(tile(kxy, kel, kloops, (180, 180), points={"G": (0.0, 0.0)}), kxy, 1.0, 2.0,
           markers=(((0.2, 0.1), "max"),))
sb = paint(tile(kxy, kel, kloops, (180, 180)), kxy, -2.0, 1.0)
sc = paint(tile(kxy, kel, kloops, (180, 180)), kxy, 0.5, 0.5)
pa = b3.Placement(1, 0.85, (0.85, 0.0, 0.0), va, wa, 1.0)
pb = b3.Placement(2, 1.15, (1.0, 0.15, 0.0), vb, wb, 1.0)
pc = b3.Placement(3, 1.9, (1.0, 0.9, 0.0), vb, wb, 0.7)
items = [(pa, sa), (pb, sb), (pc, sc)]
squares = [(pa, 0.3, sa), (pb, 0.3, sb), (pc, 0.3, sc)]
comp = b3.Composer3D(W, H)
base = b3.default_camera(chain, 0.3 * math.sqrt(2), W, H)
cases = [("default", base), ("turned", z_up_view(chain, 0.3 * math.sqrt(2), W, H).rotate(60, 30)), ("from below", base.rotate(-150, -80)),
         ("into the kink", b3.Camera(-135.0, 10.0, Z_, (0.9, 0.1, 0.0), 260.0, (0, 0), W, H)),
         ("zoomed", base.zoom_at(2.5, 150, 160))]
meaningful = 0
for name, cm in cases:
    fine = comp.render(items, cm, coarse=False)
    fine_owner = fine["owner"].copy()
    fine_frame = fine["frame"].copy()
    coarse = comp.render(items, cm, coarse=True)
    want, amb = brute_owner(cm, W, H, squares)
    bad = int(np.count_nonzero((fine_owner.ravel() != want) & ~amb))
    check("kink, {0}: each pixel shows the front section, as the ray cast".format(name),
          bad == 0 and np.count_nonzero(want == 1) > 200 and np.count_nonzero(want == 2) > 200,
          (bad, int(np.count_nonzero(amb))))
    check("kink, {0}: coarse and fine give the same owner map".format(name),
          np.array_equal(fine_owner, coarse["owner"]))
    # the sections cut each other: the order by centre depth is wrong somewhere
    hits = {1: ray_hits(cm, W, H, pa), 2: ray_hits(cm, W, H, pb), 3: ray_hits(cm, W, H, pc)}
    ha, hb = hits[1], hits[2]
    za = cm.to_screen(pa.origin)[2]
    zb = cm.to_screen(pb.origin)[2]
    by_centre = 1 if za < zb else 2
    both = (want > 0) & ~amb
    wrong = int(np.count_nonzero(both & (want != by_centre) & (want <= 2) &
                                 (np.maximum(np.abs(ha[0]), np.abs(ha[1])) < 0.3) &
                                 (np.maximum(np.abs(hb[0]), np.abs(hb[1])) < 0.3)))
    meaningful += wrong > 0

    # probe round trip, and against the ray cast
    owned = np.flatnonzero(fine_owner.ravel() > 0)
    pick = owned[np.random.RandomState(3).randint(0, len(owned), 200)]
    worst, worst_ray, edge_ok = 0.0, 0.0, True
    places = {1: pa, 2: pb, 3: pc}
    comp.render(items, cm, coarse=False)
    for lin in pick:
        px, py = int(lin % W), int(lin // W)
        got = comp.probe(px, py)
        if got is None or "v" not in got:
            edge_ok = False
            continue
        p = places[got["number"]]
        sx, sy, _ = cm.to_screen(p.world(got["v"], got["w"]))
        worst = max(worst, abs(float(sx) - px - 0.5), abs(float(sy) - py - 0.5))
        hv, hw, _ = hits[got["number"]]
        worst_ray = max(worst_ray, abs(hv[lin] - got["v"]), abs(hw[lin] - got["w"]))
    check("kink, {0}: probe pixel -> (v, w) -> pixel".format(name),
          edge_ok and worst < 1e-6 and worst_ray < 1e-9, (worst, worst_ray))
check("the kink cases need the z-buffer: the order by centre depth fails in some", meaningful >= 2,
      meaningful)

# contour lines of one section never in another: B plain, A with lines
plain = br.Scene(kxy, kel, kloops, 180, 180, None, 1, None, axes_at_corner=False,
                 margin=br.TILE_MARGIN)
qb = np.full(len(kxy), 5.0, dtype=np.float32)
plain.paint(qb, 0.0, 10.0)
vals, counts = np.unique(plain.frame[plain.index >= 0], return_counts=True)
colour_b = int(vals[np.argmax(counts)])             # the outline lies on the edge
lined = paint(tile(kxy, kel, kloops, (180, 180)), kxy, 7.0, -3.0, contours=True)
n_lines = int(np.count_nonzero(np.isin(lined.frame[lined.index >= 0], [br.BLACK, br.WHITE])))
for name, cm in cases[:4]:
    for coarse in (True, False):
        out = comp.render([(pa, lined), (pb, plain)], cm, coarse=coarse)
        own = out["owner"].ravel()
        fr = out["frame"].view(np.uint32).ravel()
        hv, hw, _ = ray_hits(cm, W, H, pb)
        band = band_of(cm, pb, plain)
        inner = (own == 2) & (np.maximum(np.abs(hv), np.abs(hw)) < 0.3 - band)
        stray = int(np.count_nonzero(inner & (fr != colour_b)))
        check("kink, {0}, {1}: the contour lines of one section stay off the other".format(
            name, "coarse" if coarse else "fine"),
            stray == 0 and np.count_nonzero(inner) > 200 and n_lines > 100,
            (stray, int(np.count_nonzero(inner))))

# contour lines drawn three times smaller do not break up: the line pixels scale
# with the length, a third (warp alone kept about half of that)
for coarse in (True, False):
    head = b3.Camera(0.0, 0.0, Z_, pa.origin, lined.s / 3.0, (0.0, 0.0), W, H)
    out = comp.render([(pa, lined)], head, coarse=coarse, axes=False)
    fr = out["frame"].view(np.uint32).ravel()
    inner = out["owner"].ravel() == 1
    got = int(np.count_nonzero(inner & np.isin(fr, [br.BLACK, br.WHITE])))
    check("contour lines a third of the size stay whole, {0}".format("coarse" if coarse else "fine"),
          got >= 0.8 * n_lines / 3.0, (got, round(n_lines / 3.0)))

# coarse colours: each pixel the texel of its own section under its centre
cm = cases[1][1]
out = comp.render(items, cm, coarse=True, axes=False)
own = out["owner"].ravel()
fr = out["frame"].view(np.uint32).ravel()
want, amb = brute_owner(cm, W, H, squares)
mism, total = 0, 0
for p, scene in items:
    hv, hw, _ = ray_hits(cm, W, H, p)
    tx, ty = scene.pixel_of(hv, hw)
    sel = np.flatnonzero((own == p.number) & (want == p.number) & ~amb)
    # the texel under the centre, or one within a pixel of it, where a splat of a
    # line landed (a pixel spans up to 1 / smallest scale texels)
    Rc, Uc, _ = cm.basis()
    P = np.array([[p.v_axis.dot(Rc), p.w_axis.dot(Rc)], [p.v_axis.dot(Uc), p.w_axis.dot(Uc)]])
    px_per_texel = cm.zoom * p.magnify * np.linalg.svd(P, compute_uv=False)[-1] / scene.s
    r = int(math.ceil(1.0 / px_per_texel)) + 1
    ix = np.clip(np.floor(tx[sel]).astype(int), r, scene.width - 1 - r)
    iy = np.clip(np.floor(ty[sel]).astype(int), r, scene.height - 1 - r)
    near = np.stack([scene.frame[(iy + dy) * scene.width + ix + dx]
                     for dy in range(-r, r + 1) for dx in range(-r, r + 1)])
    mism += int(np.count_nonzero(~(near == fr[sel]).any(0)))
    total += len(sel)
check("coarse: the colour of every pixel is its own texel", mism == 0 and total > 1000, (mism, total))

# ------------------------------------------------- camera along the beam, hidden ones
W, H = 400, 400
comp = b3.Composer3D(W, H)
s_front = paint(tile(kxy, kel, kloops, (180, 180), points={"G": (0.0, 0.0)}), kxy, 1.0, 1.0)
sxy, sel_, sloops = square_mesh(0.2, 5)
s_back = paint(tile(sxy, sel_, sloops, (150, 150), points={"G": (0.05, 0.02)}), sxy, -3.0, 2.0,
               markers=(((0.1, 0.1), "max"), ((-0.15, 0.15), "peak")))
s_mid = paint(tile(kxy, kel, kloops, (180, 180)), kxy, 0.0, 4.0)
ua, va, wa = section_frame(X_)
p_back = b3.Placement(1, 0.0, (0.0, 0.0, 0.0), va, wa, 1.0)
p_mid = b3.Placement(2, 1.0, (1.0, 0.0, 0.0), va, wa, 1.0)
p_front = b3.Placement(3, 2.0, (2.0, 0.0, 0.0), va, wa, 1.0)
along = b3.Camera(0.0, 0.0, Z_, (1.0, 0.01, -0.02), 500.0, (0.0, 0.0), W, H)
check("camera along +X looks along -U", np.allclose(along.eye, X_))
out = comp.render([(p_back, s_back), (p_mid, s_mid), (p_front, s_front)], along, chain=[(0, 0, 0), (2, 0, 0)])
own = out["owner"].copy()
inside = np.zeros((H, W), dtype=bool)
xs, ys, _ = along.to_screen(p_front.world(np.array([-0.3, 0.3]), np.array([-0.3, 0.3])))
inside[int(min(ys)) + 3:int(max(ys)) - 3, int(min(xs)) + 3:int(max(xs)) - 3] = True
got = comp.probe(200, 200)
want_v = (200.5 - 200.0) / 500.0 + 0.01        # right is +Y = V
want_w = -(200.5 - 200.0) / 500.0 - 0.02       # down is -Z = -W
check("camera along the beam: the front section hides the rest",
      bool((own[inside] == 3).all()) and not (own == 1).any() and not (own == 2).any())
check("camera along the beam: probe gives the front section and its point",
      got is not None and got["number"] == 3 and abs(got["v"] - want_v) < 1e-12
      and abs(got["w"] - want_w) < 1e-12 and got["s"] == 2.0, got)
alone = comp.render([(p_front, s_front)], along, chain=[(0, 0, 0), (2, 0, 0)])["frame"].copy()
check("the outline and markers of a hidden section are not visible",
      np.array_equal(out["frame"], alone))
# turned a little: the back section shows, its outline stays hidden where the front is
tilt = along.rotate(30.0, 12.0)
out = comp.render([(p_back, s_back), (p_front, s_front)], tilt)
own = out["owner"].ravel()
want, amb = brute_owner(tilt, W, H, [(p_back, 0.2, s_back), (p_front, 0.3, s_front)])
bad = int(np.count_nonzero((own != want) & ~amb))
check("turned a little: the back shows only beside the front", bad == 0 and (own == 1).any(), bad)

# ---------------------------------------------------- reversed element, other node order
fl = ss.SectionField([[[0.0, 0.0], [0.12, 0.0], [0.12, 0.02], [0.02, 0.02], [0.02, 0.08], [0.0, 0.08]]],
                     nu=0.3, n_target=600)
lxy = fl.xy
lloops = [l - fl.centroid for l in fl.loops_in]
ql = (30.0 * lxy[:, 0] + 50.0 * lxy[:, 1]).astype(np.float32)
lo, hi = float(ql.min()), float(ql.max())
MARK = (0.1, 0.01)


def edge_zone(cm, W, H, placements, scene):
    """Pixels where a mirrored tile may differ: near the outline of any section
    whose plane the ray meets (lines are rounded down, not mirrored), and near
    the marker ring. Outside these the pictures must be the same."""
    zone = np.zeros(W * H, dtype=bool)
    loop = np.vstack([lloops[0], lloops[0][:1]])
    for p in placements:
        v, w, _ = ray_hits(cm, W, H, p)
        band = band_of(cm, p, scene)
        dist = np.full(W * H, np.inf)
        for (x0, y0), (x1, y1) in zip(loop[:-1], loop[1:]):
            ex, ey = x1 - x0, y1 - y0
            t = np.clip(((v - x0) * ex + (w - y0) * ey) / (ex * ex + ey * ey), 0.0, 1.0)
            dist = np.minimum(dist, np.hypot(v - x0 - t * ex, w - y0 - t * ey))
        zone |= dist < band
        zone |= np.hypot(v - MARK[0], w - MARK[1]) < 9.0 / scene.s + band
    return zone


def l_items(sv, sw, numbers, length=2.0, n=4, mag=3.0):
    """Sections of an L along a beam on X; sv, sw the signs of the drawn axes of
    a beam in the other node order (beam_model: V = sv V_e, W = sw W_e, loops
    mirrored the same way). (1, 1) is the beam as it is."""
    u, v, w = section_frame(X_)
    if sv * sw < 0:
        u = -u
    m_xy = lxy * np.array([sv, sw])
    m_loops = [l * np.array([sv, sw]) for l in lloops]
    # no letters G and S: a letter is drawn upright in the tile, so it reads
    # mirrored from the other side of the section, as it should in 3D
    scene = br.Scene(m_xy, fl.elements, m_loops, 160, 160, None, 1, None, axes_at_corner=False,
                     margin=br.TILE_MARGIN)
    scene.paint(ql, lo, hi, contours=True, markers=(((sv * MARK[0], sw * MARK[1]), "max"),))
    out = []
    for i in range(n):
        x = length * i / (n - 1)
        out.append((b3.Placement(numbers[i], x, (x, 0.0, 0.0), sv * v, sw * w, mag), scene))
    return out


W, H = 420, 320
comp = b3.Composer3D(W, H)
straight = l_items(1, 1, [1, 2, 3, 4])
lcam = b3.default_camera([(0, 0, 0), (2, 0, 0)], 3.0 * 0.1, W, H)
for cname, cm in (("default, zoomed", lcam.zoom_at(2.5, 0.5 * W, 0.5 * H)),
                  ("turned", z_up_view([(0, 0, 0), (2, 0, 0)], 3.0 * 0.1, W, H).rotate(-70, 25))):
    ref = comp.render(straight, cm)
    ref_frame, ref_owner = ref["frame"].view(np.uint32).copy(), ref["owner"].copy()
    owned = max(int(np.count_nonzero(ref_owner >= 0)), 1)
    zone = edge_zone(cm, W, H, [p for p, _ in straight], straight[0][1])
    for sv, sw in ((-1, 1), (1, -1)):
        other = comp.render(l_items(sv, sw, [1, 2, 3, 4]), cm)
        dframe = other["frame"].view(np.uint32).ravel() != ref_frame.ravel()
        downer = other["owner"].ravel() != ref_owner.ravel()
        check("reversed element ({0}, {1}), {2}: the same picture as the beam in its own order".format(
            sv, sw, cname), not (dframe & ~zone).any() and not (downer & ~zone).any()
            and np.count_nonzero((ref_owner.ravel() >= 0) & ~zone) > 0.3 * owned,
            (int(np.count_nonzero(dframe & ~zone)), int(np.count_nonzero(downer & ~zone)),
             int(np.count_nonzero(dframe)), owned))
    # control: the axes turned without mirroring the loops is another picture
    wrong = []
    for (p, scene), (_, s0) in zip(l_items(-1, 1, [1, 2, 3, 4]), straight):
        wrong.append((p, s0))
    bad = comp.render(wrong, cm)
    dpix = int(np.count_nonzero(bad["frame"].view(np.uint32) != ref_frame))
    check("control, {0}: turned axes without mirrored loops differ".format(cname), dpix > 0.2 * owned,
          (dpix, owned))

# ------------------------------------------------------------------------- on edge
W, H = 300, 240
comp = b3.Composer3D(W, H)
pe = [(b3.Placement(i + 1, float(i), (float(i), 0.0, 0.0), va, wa, 1.0), sq) for i in range(3)]
for name, az in (("exactly", 90.0), ("1e-7 rad off", 90.0 - math.degrees(1e-7)),
                 ("1e-3 rad off", 90.0 - math.degrees(1e-3)), ("1 degree off", 89.0)):
    edge = b3.Camera(az, 0.0, Z_, (1.0, 0.0, 0.0), 80.0, (0.0, 0.0), W, H)
    with np.errstate(all="raise"):
        try:
            for coarse in (True, False):
                out = comp.render(pe, edge, coarse=coarse, chain=[(0, 0, 0), (2, 0, 0)])
            ok = True
        except FloatingPointError as e:
            ok = False
            out = None
    own = out["owner"] if out else np.zeros(1)
    numbers = set(int(n) for n in np.unique(own) if n > 0)
    lin = np.flatnonzero(own.ravel() > 0) if out else []
    got = comp.probe(int(lin[0] % W), int(lin[0] // W)) if len(lin) else None
    check("on edge, {0}: no NaN, each section a visible line, probe gives the number".format(name),
          ok and numbers == {1, 2, 3} and got is not None and "v" not in got
          and bool(np.isfinite(own).all()), (ok, numbers, got))
xy1, el1, lp1 = square_mesh(0.25, 2)
tiny = paint(tile(xy1, el1, lp1, (40, 40)), xy1, 1.0, 1.0)
odd = b3.Placement(1, 0.0, (0.0, 0.0, 0.0), va, wa, 0.0)
out = comp.render([(odd, tiny)], b3.Camera(0, 0, Z_, (0, 0, 0), 50.0, (0, 0), W, H))
check("magnify 0 draws a dot at most, and no failure", int(np.count_nonzero(out["owner"] > 0)) <= 4)

# ------------------------------------------------------------------------- speed
h_, b_, tw_, tf_ = 0.3, 0.15, 0.0071, 0.0107
iloop = [[-b_ / 2, -h_ / 2], [b_ / 2, -h_ / 2], [b_ / 2, -h_ / 2 + tf_], [tw_ / 2, -h_ / 2 + tf_],
         [tw_ / 2, h_ / 2 - tf_], [b_ / 2, h_ / 2 - tf_], [b_ / 2, h_ / 2], [-b_ / 2, h_ / 2],
         [-b_ / 2, h_ / 2 - tf_], [-tw_ / 2, h_ / 2 - tf_], [-tw_ / 2, -h_ / 2 + tf_],
         [-b_ / 2, -h_ / 2 + tf_]]
rloop = [[-b_ / 2, -h_ / 2], [b_ / 2, -h_ / 2], [b_ / 2, h_ / 2], [-b_ / 2, h_ / 2]]
W, H = 1200, 900
comp = b3.Composer3D(W, H)
length = 6.0
_, uv, uw = section_frame(X_)
results = {}
for kind, n, sloop in (("I", 6, iloop), ("I", 12, iloop), ("solid", 6, rloop), ("solid", 12, rloop)):
    fi = ss.SectionField([sloop], nu=0.3, n_target=3000)
    units = br.UnitFields(fi)
    iloops = [l - fi.centroid for l in fi.loops_in]
    ipts = {"G": (0.0, 0.0), "S": tuple(fi.shear_centre)}
    bounds = br.loop_bounds(iloops)
    print("speed: {0} section with {1} tri6 elements, {2} sections, 1200 x 900".format(
        kind, len(fi.elements), n))
    cols, rows, tw, th = br.tile_grid(n, W, H, bounds, 20)
    mag = length / (n + 1) / h_
    sitems = []
    for i in range(n):
        s = length * i / (n - 1)
        scene = br.Scene(fi.xy, fi.elements, iloops, tw, th - 20, None, 1, ipts,
                         axes_at_corner=False, margin=br.TILE_MARGIN, bounds=bounds)
        sn, tv, tw2 = units.nodal([1e4, 2e3 * (1 + i), 1e3, 50.0, 3e3, 2e4 * (1 - s / length)])
        vm = ss.measures({"sigma": sn, "tau_v": tv, "tau_w": tw2})["vonmises"]
        lo, hi = br.scale_range(vm, units.outside)
        scene.paint(vm.copy(), lo, hi, contours=True, markers=(((0.05, 0.14), "max"),))
        sitems.append((b3.Placement(i + 1, s, (s, 0.0, 0.0), uv, uw, mag), scene))
    half = mag * 0.5 * math.hypot(h_, b_)
    chain_pts = [(0, 0, 0), (length, 0, 0)]
    start = b3.default_camera(chain_pts, half, W, H)
    views = {"default": start,
             "along the beam, zoomed": b3.Camera(0.0, 0.0, Z_, (length, 0.0, 0.0),
                                                 0.9 * H / (mag * h_), (0, 0), W, H)}
    for vname, c0 in views.items():
        for coarse in (True, False):
            times, parts = [], []
            for k in range(21):
                cm = c0.rotate(1.5 * k, 0.5 * k) if vname == "default" else c0
                t0 = time.perf_counter()
                out = comp.render(sitems, cm, coarse=coarse, chain=chain_pts)
                times.append((time.perf_counter() - t0) * 1e3)
                parts.append(out["times"])
            order = np.argsort(times)
            med = float(np.median(times))
            mid = parts[int(order[10])]
            results[(kind, n, vname, coarse)] = med
            print("  {0}, {1}: median {2:.1f} ms, worst {3:.1f} ms "
                  "(texture {4:.1f}, warp {5:.1f}, lines {6:.1f}); {7} pixels owned".format(
                      vname, "coarse" if coarse else "fine  ", med, max(times),
                      mid["texture"], mid["warp"], mid["lines"],
                      int(np.count_nonzero(out["owner"] >= 0))))
for kind in ("I", "solid"):
    for n in (6, 12):
        r = results
        check("speed, {0}, {1} sections, default view: median coarse <= 30 ms, fine <= 100 ms".format(
            kind, n), r[(kind, n, "default", True)] <= 30.0 and r[(kind, n, "default", False)] <= 100.0,
            (round(r[(kind, n, "default", True)], 1), round(r[(kind, n, "default", False)], 1)))

print()
print("FAILED: " + ", ".join(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
