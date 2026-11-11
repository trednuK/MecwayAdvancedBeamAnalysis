# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test of the 3D view in Scripts/beam_worker.py: the sheet request with mode
# "3d", its probe, the 3D slot beside the 2D scenes, coarse and fine through
# beam_utils.RequestSlot, and the detail of one section. The worker's Session
# runs in this process, without windows and without Mecway; the models are own
# small .liml files in the temp folder. Without Scripts/beam_render3d.py a small
# stand-in with the same interface draws the picture. Run with a Python with
# numpy and scipy:
#
#   python tests/test_beam_worker3d.py
#
# Reruns itself with the Python of a Code-Aster installation if needed.

from __future__ import print_function

import glob
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types

_HERE = os.path.dirname(os.path.abspath(globals().get("__file__", sys.argv[0])))
SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", "Scripts"))
sys.path.insert(0, SCRIPTS)
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
import beam_utils as bu  # noqa: E402
import beam_worker as bw  # noqa: E402
import beam_render as br  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (" ({0})".format(detail) if detail != "" else ""))
    if not ok:
        FAILED.append(name)


def near(a, b, tol):
    return abs(a - b) <= tol * max(abs(b), 1e-300)


# ------------------------------------------------------------------ stand-in
def make_stand_in():
    """Orthographic renderer with the interface of beam_render3d: textures by
    inverse affine map, a z-buffer, an owner per pixel. Only for this test."""
    mod = types.ModuleType("beam_render3d")
    EDGE = math.sin(math.radians(2.0))

    def unit(a):
        a = np.asarray(a, dtype=float)
        return a / np.linalg.norm(a)

    class Camera(object):
        def __init__(self, azimuth, elevation, up, target, zoom, pan, width, height, fit=1.0):
            self.azimuth, self.elevation = float(azimuth), max(min(float(elevation), 89.0), -89.0)
            self.up = unit(up)
            self.target = np.asarray(target, dtype=float)
            self.zoom, self.pan = float(zoom), [float(pan[0]), float(pan[1])]
            self.width, self.height, self.fit = width, height, float(fit)
            e1 = unit(np.cross(self.up, [0.0, 1.0, 0.0]) if abs(self.up[1]) < 0.9
                      else np.cross(self.up, [1.0, 0.0, 0.0]))
            e2 = np.cross(self.up, e1)
            a, e = math.radians(self.azimuth), math.radians(self.elevation)
            self.eye = math.cos(e) * (math.cos(a) * e1 + math.sin(a) * e2) + math.sin(e) * self.up
            self.right = unit(np.cross(-self.eye, self.up))
            self.screen_up = np.cross(self.right, -self.eye)
            self.scale = self.zoom * min(width, height) / (2.0 * self.fit)

        @classmethod
        def from_dict(cls, d, width, height):
            return cls(d["azimuth"], d["elevation"], d["up"], d["target"], d["zoom"], d["pan"],
                       width, height, d.get("fit", 1.0))

        def as_dict(self):
            return {"azimuth": self.azimuth, "elevation": self.elevation, "up": self.up,
                    "target": self.target, "zoom": self.zoom, "pan": self.pan, "fit": self.fit}

        def project(self, p):
            d = np.asarray(p, dtype=float) - self.target
            return (0.5 * self.width + self.pan[0] + self.scale * d.dot(self.right),
                    0.5 * self.height + self.pan[1] - self.scale * d.dot(self.screen_up),
                    d.dot(self.eye))

        def direction(self, a):
            a = np.asarray(a, dtype=float)
            return np.array([self.scale * a.dot(self.right), -self.scale * a.dot(self.screen_up),
                             a.dot(self.eye)])

    def default_camera(chain_points, half_size, width, height, z_top=False):
        p = np.asarray(chain_points, dtype=float)
        lo, hi = p.min(axis=0), p.max(axis=0)
        span = hi - lo
        # as beam_render3d: up as Mecway's (Y, or Z with z_top), from the front, a
        # little from +X and from above (the azimuths in the axes of this stand-in)
        up = [0.0, 0.0, 1.0] if z_top else [0.0, 1.0, 0.0]
        return Camera(120.0 if z_top else -150.0, 20.0, up, 0.5 * (lo + hi), 1.0, (0.0, 0.0),
                      width, height, 0.5 * np.linalg.norm(span) + half_size)

    class Placement(object):
        def __init__(self, number, s, origin, v_axis, w_axis, magnify):
            self.number, self.s = number, s
            self.origin = np.asarray(origin, dtype=float)
            self.v_axis = np.asarray(v_axis, dtype=float)
            self.w_axis = np.asarray(w_axis, dtype=float)
            self.magnify = float(magnify)

    class Composer3D(object):
        def __init__(self, width, height):
            self.width, self.height = width, height

        def render(self, items, camera, coarse, **kw):
            t0 = time.perf_counter()
            f = 2 if coarse else 1
            W, H = -(-self.width // f), -(-self.height // f)
            frame = np.full(W * H, br.BACKGROUND, dtype=np.uint32)
            depth = np.full(W * H, -np.inf)
            owner = np.full(W * H, -1, dtype=np.int64)
            self.maps, anchors = [], []
            for k, (pl, sc) in enumerate(items):
                ox, oy, od = camera.project(pl.origin)
                anchors.append((ox, oy))
                dv = camera.direction(pl.magnify * pl.v_axis)
                dw = camera.direction(pl.magnify * pl.w_axis)
                A = np.array([[dv[0], dw[0]], [dv[1], dw[1]]]) / f
                b = np.array([ox, oy]) / f
                det = np.linalg.det(A)
                edge = abs(det) < EDGE * (A ** 2).sum() / 2.0
                corners = [(sc.x0, sc.y1), (sc.x0 + sc.width / sc.s, sc.y1),
                           (sc.x0, sc.y1 - sc.height / sc.s),
                           (sc.x0 + sc.width / sc.s, sc.y1 - sc.height / sc.s)]
                xy = np.array([A.dot(c) + b for c in corners])
                x0, y0 = [max(int(math.floor(v)), 0) for v in xy.min(axis=0)]
                x1, y1 = int(math.ceil(xy[:, 0].max())), int(math.ceil(xy[:, 1].max()))
                x1, y1 = min(max(x1, x0 + 1), W), min(max(y1, y0 + 1), H)
                self.maps.append((A, b, edge, pl.number))
                if x1 <= x0 or y1 <= y0:
                    continue
                gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
                pix = (np.floor(gy).astype(int) * W + np.floor(gx).astype(int)).ravel()
                if edge:
                    # a line: owner only, no colour
                    d = np.full(pix.size, od)
                    take = d > depth[pix]
                    owner[pix[take]] = k
                    depth[pix[take]] = d[take]
                    continue
                inv = np.linalg.inv(A)
                rel = np.stack([gx.ravel() - b[0], gy.ravel() - b[1]])
                vw = inv.dot(rel)
                tx = np.floor((vw[0] - sc.x0) * sc.s).astype(int)
                ty = np.floor((sc.y1 - vw[1]) * sc.s).astype(int)
                inside = (tx >= 0) & (tx < sc.width) & (ty >= 0) & (ty < sc.height)
                colour = np.full(pix.size, br.BACKGROUND, dtype=np.uint32)
                colour[inside] = sc.frame[ty[inside] * sc.width + tx[inside]]
                d = od + vw[0] * dv[2] + vw[1] * dw[2]
                take = (colour != br.BACKGROUND) & (d > depth[pix])
                frame[pix[take]] = colour[take]
                depth[pix[take]] = d[take]
                owner[pix[take]] = k
            self.owner, self.f, self.W, self.H = owner, f, W, H
            if f > 1:
                rows = np.minimum(np.arange(self.height) // f, H - 1)
                cols = np.minimum(np.arange(self.width) // f, W - 1)
                frame = frame.reshape(H, W)[rows[:, None], cols].ravel()
            axes = dict((name, camera.direction(a)[:2].tolist()) for name, a in
                        (("X", [1, 0, 0]), ("Y", [0, 1, 0]), ("Z", [0, 0, 1])))
            return {"frame": frame, "owner": owner, "anchors": anchors, "axes": axes,
                    "times": {"compose": (time.perf_counter() - t0) * 1e3}}

        def probe(self, x, y):
            i, j = x // self.f, y // self.f
            if not (0 <= i < self.W and 0 <= j < self.H):
                return None
            k = int(self.owner[j * self.W + i])
            if k < 0:
                return None
            A, b, edge, number = self.maps[k]
            if edge:
                return {"number": number}
            v, w = np.linalg.inv(A).dot(np.array([(x + 0.5) / self.f, (y + 0.5) / self.f]) - b)
            return {"number": number, "v": float(v), "w": float(w)}

    mod.Camera, mod.default_camera = Camera, default_camera
    mod.Placement, mod.Composer3D = Placement, Composer3D
    mod.STAND_IN = True
    return mod


try:
    import beam_render3d as b3
    REAL = True
except ImportError:
    b3 = sys.modules["beam_render3d"] = make_stand_in()
    REAL = False
print("renderer: " + ("beam_render3d" if REAL else "stand-in"))

# what the worker hands the renderer, also with the real one
RENDERS = []
_render = b3.Composer3D.render


def _recording(self, items, camera, coarse, **kw):
    RENDERS.append((list(items), camera, coarse))
    return _render(self, items, camera, coarse, **kw)


b3.Composer3D.render = _recording

# ------------------------------------------------------------------- models
MODEL = u"""<liml version="33">
  <analysis type="S30" />
  %(nodes)s
  <elset name="B" material="I">
    %(elements)s
  </elset>
  <mat name="I">
    <geometric type="I" ia="200 mm" ib="100 mm" ic="10 mm" id="6 mm" pointy="0 m" pointz="0 m" />
    <mechanical type="Isotropic" youngsmodulus="210 GPa" poissonratio="0.3" />
  </mat>
  <solution><results /></solution>
</liml>
"""


def write_model(path, points, flip=()):
    nodes = u"".join(u'<node nid="{0}" x="{1!r}" y="{2!r}" z="{3!r}" />'.format(i + 1, *p)
                     for i, p in enumerate(points))
    elements = []
    for i in range(len(points) - 1):
        a, b = (i + 2, i + 1) if (i + 1) in flip else (i + 1, i + 2)
        elements.append(u'<elem eid="{0}" shape="line2" nodes="{1} {2}" />'.format(i + 1, a, b))
    with open(path, "w", encoding="utf-8") as f:
        f.write(MODEL % {"nodes": nodes, "elements": u"".join(elements)})


def raw_moment(n, length=2.0):
    raw = {}
    for i in range(n):
        x0, x1 = length * i / n, length * (i + 1) / n
        raw[str(i + 1)] = {"tensileforce": [100.0, 100.0], "sheary": [500.0, 500.0],
                           "shearz": [0.0, 0.0], "torsion": [10.0, 10.0],
                           "bendingmomv": [0.0, 0.0],
                           "bendingmomw": [500.0 * (length - x0), 500.0 * (length - x1)]}
    return raw


W, H = 640, 400
BASE = {"cmd": "frame", "gen": 1, "measure": "sigma", "width": W, "height": H, "view": None,
        "coarse": False, "scale": None, "shear_centre": True, "arrows": {"on": True, "length": 1.0},
        "ui_scale": 1.0, "band": 18}


def ask(session, **kw):
    rq = dict(BASE)
    rq.update(kw)
    reply, pixels, w, h = session.handle(json.loads(json.dumps(rq)))
    text = json.dumps(reply)                # what goes over the pipe
    if pixels is not None:
        data = memoryview(pixels).cast("B")
        assert len(data) == w * h * 4
    return json.loads(text), pixels, w, h


def probe(session, frame, x, y):
    reply, _, _, _ = session.handle({"cmd": "probe", "gen": 1, "frame": frame, "x": x, "y": y})
    return json.loads(json.dumps(reply))


def three_d(**kw):
    out = {"sheet": {"n": 5}, "mode": "3d"}
    out.update(kw)
    return out


tmp = tempfile.mkdtemp(prefix="beam_worker3d_")
try:
    n_el, length = 10, 2.0
    path = os.path.join(tmp, "beam.liml")
    write_model(path, [(length * i / n_el, 0.0, 0.0) for i in range(n_el + 1)])
    session = bw.Session()
    raw = raw_moment(n_el, length)
    reply, _, _, _ = session.handle({"cmd": "open", "gen": 1, "path": path,
                                     "element_ids": list(range(1, n_el + 1)), "raw": raw})
    check("open", reply["ok"], reply)

    # ------------------------------------------------------------ protocol
    flat, flat_px, _, _ = ask(session, frame=1, sheet={"n": 5})
    scenes_2d = dict(session.tile_scenes)
    singles_2d = dict(session.scenes)
    info, pixels, w, h = ask(session, frame=2, **three_d())
    check("3D: one picture of canvas size", (w, h) == (W, H) and pixels.size == W * H)
    check("3D: the reply says 3D, fine", info["mode"] == "3d" and info["sheet"] and not info["coarse"])
    tiles = info["tiles"]
    check("3D: the tiles of 2D, same numbers and places",
          [(t["number"], t["s"], t["eid"]) for t in tiles] ==
          [(t["number"], t["s"], t["eid"]) for t in flat["tiles"]], tiles)
    check("3D: same values per tile as 2D",
          all(a["outside"] == b["outside"] and a["peak"] == b["peak"]
              for a, b in zip(tiles, flat["tiles"])))
    check("3D: one colour scale, the one of 2D", (info["lo"], info["hi"]) == (flat["lo"], flat["hi"]),
          (info["lo"], info["hi"], flat["lo"], flat["hi"]))
    check("3D: headline as 2D", info["outside"] == flat["outside"] and info.get("peak") == flat.get("peak"))
    check("3D: every tile has an anchor in pixels",
          all(isinstance(t["anchor"], list) and len(t["anchor"]) == 2 for t in tiles), tiles[0])
    check("3D: no rectangles, no arrows", "x" not in tiles[0] and info["arrow_max"] is None)
    check("3D: camera, magnify and axes in the reply", isinstance(info["camera"], dict) and
          "axes" in info and info["magnify_auto"] is True)
    size = 0.2                            # the I section is 200 mm high
    want = min(max(length / 6 / size, 1.0), 100.0)
    check("3D: automatic magnify, largest section 1/(n+1) of the chain",
          near(info["magnify"], want, 1e-6), (info["magnify"], want))
    img = np.asarray(pixels, dtype=np.uint32)
    check("3D: something drawn", int((img != br.BACKGROUND).sum()) > 500, int((img != br.BACKGROUND).sum()))
    check("3D: 2D scenes not pushed out of their caches",
          session.tile_scenes == scenes_2d and all(session.tile_scenes[k] is v for k, v in scenes_2d.items())
          and session.scenes == singles_2d)
    check("3D: own slot", len(session.textures) >= 1 and
          not set(id(v) for v in session.textures.values()) & set(id(v) for v in scenes_2d.values()))

    items, camera, coarse = RENDERS[-1]
    pls = [p for p, _ in items]
    check("placement: numbers, s and magnify",
          [p.number for p in pls] == [1, 2, 3, 4, 5] and all(p.magnify == info["magnify"] for p in pls))
    check("placement: origin on the chain", all(near(p.origin[0], t["s"], 1e-9) or abs(t["s"]) < 1e-12
                                                 for p, t in zip(pls, tiles)) and
          max(abs(p.origin[1]) + abs(p.origin[2]) for p in pls) < 1e-6)
    check("placement: V x W along the chain",
          all(np.allclose(np.cross(p.v_axis, p.w_axis), [1.0, 0.0, 0.0]) for p in pls))
    tex = [t for _, t in items]
    check("textures: one frame per tile, not shared",
          len(set(id(t.frame) for t in tex)) == len(tex) and not np.array_equal(tex[0].frame, tex[-1].frame))
    check("textures: the frame of a texture fills the outline", all(
        int((t.frame != br.BACKGROUND).sum()) > 0.5 * t.n_pixels for t in tex))
    seven, _, _, _ = ask(session, frame=2, **three_d(magnify="7"))
    for asked in ("7", "abc", [1, 2]):
        m, _, _, _ = ask(session, frame=2, **three_d(magnify="7", camera=asked))
        check("camera {0!r} is no camera: the default; magnify as text".format(asked),
              m["camera"] == seven["camera"] and m["magnify"] == 7.0 and not m["magnify_auto"])

    # the camera of a reply comes back the same
    again, px2, _, _ = ask(session, frame=3, **three_d(camera=info["camera"]))
    check("camera round trip", again["camera"] == info["camera"])
    check("camera round trip: same picture", np.array_equal(np.asarray(px2), img))
    cam = dict(info["camera"])
    cam["zoom"] = float(cam["zoom"]) * 2.0
    zoomed, _, _, _ = ask(session, frame=4, **three_d(camera=cam))
    check("zoomed camera kept", near(zoomed["camera"]["zoom"], cam["zoom"], 1e-9))
    bad, _, _, _ = ask(session, frame=5, **three_d(camera={"nonsense": 1}))
    check("unreadable camera: the default", bad["ok"] and bad["camera"] == info["camera"])
    # Mecway's up axis counts for the default camera only
    check("default camera: Y up without z_top", near(info["camera"]["up"][1], 1.0, 1e-9))
    zt, _, _, _ = ask(session, frame=5, **three_d(z_top=True))
    check("default camera with z_top: Z up", near(zt["camera"]["up"][2], 1.0, 1e-9), zt["camera"])
    zb, _, _, _ = ask(session, frame=5, **three_d(camera={"nonsense": 1}, z_top=True))
    check("refused camera with z_top: the default with Z up", zb["camera"] == zt["camera"])
    zk, _, _, _ = ask(session, frame=5, **three_d(camera=info["camera"], z_top=True))
    check("a camera to keep ignores z_top", zk["camera"] == info["camera"])
    for asked, got in ((5.0, 5.0), (1000.0, 100.0), (0.1, 1.0), ("x", want)):
        m, _, _, _ = ask(session, frame=6, **three_d(magnify=asked))
        check("magnify {0!r} gives {1!r}".format(asked, got), near(m["magnify"], got, 1e-6) and
              m["magnify_auto"] == (asked == "x"), m["magnify"])

    # fixed scale and contours, as 2D; closer, the sections of a 2 m beam are small
    close = dict(info["camera"])
    close["zoom"] = float(close["zoom"]) * 8.0
    fixed, _, _, _ = ask(session, frame=7, scale=[-1e6, 1e6], **three_d())
    check("3D: fixed scale", fixed["lo"] == -1e6 and fixed["hi"] == 1e6 and fixed["clip_hi"])
    _, plain, _, _ = ask(session, frame=8, **three_d(camera=close))
    plain = np.asarray(plain).copy()
    root_plain = session.last["tiles3d"][1]["texture"].frame.copy()
    _, lines, _, _ = ask(session, frame=9, contours=True, **three_d(camera=close))
    root_lines = session.last["tiles3d"][1]["texture"].frame
    # a thin I section: few line pixels survive on the screen, more in the texture
    check("3D: contour lines in the texture and on the screen",
          int((root_lines != root_plain).sum()) > 20 and int((np.asarray(lines) != plain).sum()) > 0,
          (int((root_lines != root_plain).sum()), int((np.asarray(lines) != plain).sum())))

    # coarse: allowed, without contour lines
    c1, cp1, cw, ch = ask(session, frame=10, coarse=True, contours=True, **three_d(camera=close))
    c1p = np.asarray(cp1).copy()
    c2, cp2, _, _ = ask(session, frame=11, coarse=True, **three_d(camera=close))
    check("coarse 3D: full size, says coarse", (cw, ch) == (W, H) and c1["coarse"])
    check("coarse 3D: no contour lines", np.array_equal(c1p, np.asarray(cp2)))
    fine_mask = plain != br.BACKGROUND
    coarse_mask = c1p != br.BACKGROUND
    check("coarse 3D: covers the place of the fine one",
          (fine_mask & coarse_mask).sum() > 0.6 * fine_mask.sum(), ((fine_mask & coarse_mask).sum(), fine_mask.sum()))

    # ten quick camera changes: one coarse and one fine are sent
    slot = bu.RequestSlot()
    for k in range(10):
        cam = dict(info["camera"])
        cam["azimuth"] = float(cam["azimuth"]) + 2.0 * k
        rq = dict(BASE)
        rq.update(three_d(camera=cam, coarse=True, frame=100 + k))
        slot.put_frame(rq)
    sent = []
    while True:
        rq = slot.take()
        if rq is None:
            break
        sent.append(session.handle(json.loads(json.dumps(rq)))[0])
    check("ten camera changes: one coarse, one fine", [r["coarse"] for r in sent] == [True, False] and
          slot.replaced == 9, [r.get("coarse") for r in sent])
    check("both of the newest camera", all(r["frame"] == 109 and
                                           near(r["camera"]["azimuth"], float(info["camera"]["azimuth"]) + 18.0, 1e-9)
                                           for r in sent))

    # ----------------------------------------------------------------- probe
    info, _, _, _ = ask(session, frame=20, **three_d(camera=close))
    t3 = info["tiles"][2]
    hits3 = []
    for y in range(0, H, 3):
        for x in range(0, W, 3):
            p = probe(session, 20, x, y)
            if p.get("hit") and p.get("number") == 3:
                hits3.append((x, y, p))
    found = hits3[len(hits3) // 2] if hits3 else None
    check("3D probe: section 3 is found", found is not None)
    if found:
        x, y, p = found
        check("3D probe: number, s and values", p["number"] == 3 and near(p["s"], t3["s"], 1e-12) and
              all(p[k] is not None for k in ("value", "sigma", "v", "w")), p)
        # the same points of the same section in 2D, where 2D finds material too
        one, _, _, _ = ask(session, frame=21, s=t3["s"], scale=[info["lo"], info["hi"]])
        v2 = one["view"]
        span = info["hi"] - info["lo"]
        worst, compared = 0.0, 0
        for _, _, p3 in hits3[::max(len(hits3) // 40, 1)]:
            q2 = probe(session, 21, int((p3["v"] - v2["x0"]) * v2["s"]),
                       int((v2["y1"] - p3["w"]) * v2["s"]))
            if q2["hit"]:
                compared += 1
                worst = max(worst, abs(q2["sigma"] - p3["sigma"]) / span)
        check("3D probe: the values of 2D at those points", compared >= 10 and worst < 0.03,
              (compared, worst))
        px_, py_ = int((p["v"] - v2["x0"]) * v2["s"]), int((v2["y1"] - p["w"]) * v2["s"])
        # old and new requests mixed
        stale = probe(session, 20, x, y)
        check("3D then 2D: the old 3D frame number gives no hit", not stale["hit"] and "number" not in stale,
              stale)
        ask(session, frame=22, sheet={"n": 5})
        check("2D sheet after 3D: old single frame no hit", not probe(session, 21, px_, py_)["hit"])
        ask(session, frame=23, **three_d(camera=close))
        check("3D again: its own probe hits", probe(session, 23, x, y)["hit"])
        check("3D again: the 2D sheet number no longer", not probe(session, 22, x, y)["hit"])
    off = probe(session, 23, 0, 0)
    check("3D probe beside the sections: nothing", not off["hit"] and "number" not in off, off)
    outside = [probe(session, 23, x_, y_) for x_, y_ in ((-1, -1), (W, 10), (10, H), (-5, 100))]
    check("3D probe outside the canvas: nothing", all(not p["hit"] and "number" not in p for p in outside))

    # a resized window: another composer, the picture of the new size
    rs, rpx, rw, rh = ask(session, frame=24, width=500, height=300, **three_d(camera=close))
    check("resize: picture of the new size", (rw, rh) == (500, 300) and np.asarray(rpx).size == 500 * 300 and
          session.composer[0] == (500, 300))

    # edge-on: number and s, no value
    # the level view whose eye is square to the beam: its sections on edge
    import beam_render3d as b3e
    beam_u = np.array([1.0, 0.0, 0.0])
    edge = dict(info["camera"])
    edge["elevation"] = 0.0
    edge["azimuth"] = float(min(range(-180, 180), key=lambda a: abs(float(np.dot(
        b3e.Camera.from_dict(dict(edge, azimuth=a), W, H).basis()[2], beam_u)))))
    ed, epx, _, _ = ask(session, frame=30, **three_d(camera=edge))
    hits = [probe(session, 30, int(t["anchor"][0]), int(t["anchor"][1])) for t in ed["tiles"]
            if 0 <= t["anchor"][0] < W and 0 <= t["anchor"][1] < H]
    numbered = [p for p in hits if "number" in p]
    check("edge-on: the section is still found, with s", len(numbered) > 0 and
          all("s" in p for p in numbered), len(numbered))
    check("edge-on: no value", all(not p["hit"] and "value" not in p for p in numbered))
    check("edge-on: no NaN in the reply", "NaN" not in json.dumps(ed))

    # --------------------------------------------------------------- detail
    info, _, _, _ = ask(session, frame=40, **three_d(camera=info["camera"]))
    for t in info["tiles"]:
        d, _, _, _ = ask(session, frame=41, s=t["s"], scale=[info["lo"], info["hi"]])
        at = session._section_at(t["s"])[0]
        same = all(d["forces"][k] == float(at["forces"].get(k, 0.0)) for k in bu.FORCES)
        if not (same and d["eid"] == t["eid"] and d["outside"]["value"] == t["outside"] and
                (d["lo"], d["hi"]) == (info["lo"], info["hi"])):
            check("detail of tile {0}: forces, element, value and shared scale".format(t["number"]), False, d)
            break
    else:
        check("detail at the s of every tile: same forces, element, value, shared colours", True)
    own, _, _, _ = ask(session, frame=42, s=info["tiles"][1]["s"])
    check("detail without the shared scale: its own scale", (own["lo"], own["hi"]) != (info["lo"], info["hi"]))

    # turning does not evaluate again; new forces and other switches do
    first = session.sheet_items[1]
    ask(session, frame=43, **three_d(camera=edge))
    check("turning keeps the evaluated sections", session.sheet_items[1] is first)
    off_m, _, _, _ = ask(session, frame=44, on={"Mw": False}, **three_d())
    check("another switch evaluates again", session.sheet_items[1] is not first and
          abs(off_m["tiles"][0]["outside"]) < 0.5 * abs(info["tiles"][0]["outside"]))
    session.handle({"cmd": "forces", "gen": 1, "raw": raw_moment(n_el, length * 2.0), "route": "element"})
    more, _, _, _ = ask(session, frame=45, **three_d())
    check("new forces evaluate again", more["tiles"][0]["outside"] > 1.5 * info["tiles"][0]["outside"],
          (more["tiles"][0]["outside"], info["tiles"][0]["outside"]))

    # ------------------------------------------------ reversed element, kink
    pk = os.path.join(tmp, "kink.liml")
    pts = [(0.5 * i, 0.0, 0.0) for i in range(5)] + [(2.0, 0.5 * i, 0.0) for i in range(1, 5)]
    write_model(pk, pts, flip=(3, 6))
    ks = bw.Session()
    rk = raw_moment(8, 4.0)
    reply, _, _, _ = ks.handle({"cmd": "open", "gen": 1, "path": pk, "element_ids": list(range(1, 9)),
                                "raw": rk})
    check("kinked chain with reversed elements opens", reply["ok"], reply.get("code"))
    kinfo, kpx, _, _ = ask(ks, frame=1, **three_d(sheet={"n": 7}))
    check("kink: 3D drawn", kinfo["ok"] and int((np.asarray(kpx) != br.BACKGROUND).sum()) > 300)
    import beam_model as bm
    import section_profiles

    def physical_drawn(session_, pl):
        """The outline as the worker places it: drawn loops about the centroid."""
        e, _ = session_.chain.element_at(pl.s)
        sec = session_.sections[bm.section_key(e)]
        return e, np.vstack([pl.origin + np.outer(l[:, 0], pl.v_axis) + np.outer(l[:, 1], pl.w_axis)
                             for l in (np.asarray(x) for x in sec.loops)])

    def physical_true(session_, e, s):
        """The same outline from the profile in the element's own axes, unmirrored."""
        _, v_e, w_e = (np.asarray(a) for a in e.axes)
        p = np.asarray(session_._chain_point(e, s))
        loops = section_profiles.loops_for(e.geometric, e.section)["loops"]
        return np.vstack([p + np.outer(np.asarray(l)[:, 0], v_e) + np.outer(np.asarray(l)[:, 1], w_e)
                          for l in loops])

    def same_points(a, b, tol=1e-9):
        d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1))
        return float(max(d.min(axis=1).max(), d.min(axis=0).max())) < tol

    items = RENDERS[-1][0]
    good, mirrored = True, 0
    for pl, _ in items:
        e, drawn_pts = physical_drawn(ks, pl)
        good = good and np.allclose(np.cross(pl.v_axis, pl.w_axis), e.frame[0]) and \
            same_points(drawn_pts, physical_true(ks, e, pl.s))
        mirrored += e.reversed
    check("kink and reversed: the outline lands where the profile is, right-handed", good)
    check("kink: reversed elements are in the sheet", mirrored > 0, mirrored)

    # edge-on along the first leg: several sections, not stacked
    # look square to the first leg, level: its sections are seen edge-on
    import beam_render3d as b3
    u1 = np.asarray(ks.chain.elements[0].frame[0], dtype=float)
    cam = dict(kinfo["camera"])
    cam["elevation"] = 0.0
    cam["azimuth"] = float(min(range(-180, 180), key=lambda a: abs(float(np.dot(
        b3.Camera.from_dict(dict(cam, azimuth=a), W, H).basis()[2], u1)))))
    ke, _, _, _ = ask(ks, frame=2, **three_d(sheet={"n": 7}, camera=cam))
    edge_hits = [probe(ks, 2, int(t["anchor"][0]), int(t["anchor"][1])) for t in ke["tiles"]
                 if t["s"] < 2.0 and 0 <= t["anchor"][0] < W and 0 <= t["anchor"][1] < H]
    numbers = set(p.get("number") for p in edge_hits if "number" in p)
    check("edge-on: several sections found by number and s, no value",
          len(numbers) >= 2 and all(not p["hit"] and "value" not in p and "s" in p
                                    for p in edge_hits if "number" in p), edge_hits)

    # the centroid term of the origin: a section whose centroid is off the line
    cs = bw.Session()
    cs.handle({"cmd": "open", "gen": 1, "path": path, "element_ids": list(range(1, n_el + 1)),
               "raw": raw})
    sec = list(cs.sections.values())[0]
    shift = np.array([0.01, -0.02])
    loops_in = [np.asarray(l) + sec.field.centroid for l in sec.loops]
    sec.field.centroid = sec.field.centroid + shift
    sec.loops = [l - sec.field.centroid for l in loops_in]
    ask(cs, frame=1, **three_d(sheet={"n": 3}))
    good = True
    for pl, _ in RENDERS[-1][0]:
        e, drawn_pts = physical_drawn(cs, pl)
        p = np.asarray(cs._chain_point(e, pl.s))
        _, v, w = (np.asarray(a) for a in e.frame)
        truth = np.vstack([p + np.outer(l[:, 0], v) + np.outer(l[:, 1], w) for l in loops_in])
        good = good and same_points(drawn_pts, truth)
    check("origin: the centroid off the chain line counts", good)

    # composer answers with numpy values, NaN and another order
    class Odd(object):
        def __init__(self, inner):
            self.inner = inner

        def render(self, items, camera, coarse, **kw):
            out = self.inner.render(items, camera, coarse, **kw)
            given = out["anchors"]
            if isinstance(given, dict):
                given = [given[p.number] for p, _ in items]
            anchors = [np.array(a, dtype=float) for a in given]
            anchors[0] = np.array([np.nan, 1.0])
            out["anchors"] = dict((np.int64(p.number), a) for (p, _), a in zip(items, anchors))
            out["axes"] = dict((k, np.asarray(v)) for k, v in out["axes"].items())
            out["times"] = {"compose": np.float64(1.5)}
            return out

        def probe(self, x, y):
            hit = self.inner.probe(x, y)
            if hit:
                hit["number"] = np.int64(hit["number"])
                if "v" in hit:
                    hit["v"], hit["w"] = np.float64(hit["v"]), np.float64(hit["w"])
            return hit

    os_ = bw.Session()
    os_.handle({"cmd": "open", "gen": 1, "path": path, "element_ids": list(range(1, n_el + 1)),
                "raw": raw})
    os_._composer(b3, W, H)
    os_.composer = ((W, H), Odd(os_.composer[1]))
    oi, _, _, _ = ask(os_, frame=1, **three_d(camera=close))
    check("numpy and NaN from the composer: plain JSON, no NaN", "NaN" not in json.dumps(oi) and
          oi["tiles"][0]["anchor"] is None and isinstance(oi["tiles"][1]["anchor"], list), oi["tiles"][:2])
    got = None
    for y_ in range(0, H, 4):
        for x_ in range(0, W, 4):
            p = probe(os_, 1, x_, y_)
            if p.get("hit"):
                got = p
                break
        if got:
            break
    check("probe with numpy numbers from the composer", got is not None and isinstance(got["number"], int), got)

    class Wrong(Odd):
        def render(self, items, camera, coarse, **kw):
            out = self.inner.render(items, camera, coarse, **kw)
            out["frame"] = np.zeros((10, 10), dtype=np.uint32)
            return out
    os_.composer = ((W, H), Wrong(os_.composer[1].inner))
    try:
        ask(os_, frame=2, **three_d(camera=close))
        raised = False
    except ValueError:
        raised = True
    check("a picture of the wrong size is an error, and leaves no stale picture",
          raised and os_.last is None and not probe(os_, 1, 100, 100)["hit"])

    # the texture budget: oldest unused first, never one of this frame
    class Fake(object):
        def __init__(self, n):
            self.frame = np.zeros(n, dtype=np.uint8)
    ts = bw.Session()
    keys = [("k{0}".format(i), 1, 1, 1) for i in range(6)]
    for k in keys:
        ts.textures[k] = Fake(40)
        ts.texture_order.append(k)
    old_budget = bw.TEXTURE_BUDGET
    bw.TEXTURE_BUDGET = 100
    try:
        ts._trim_textures({keys[0], keys[5]})
        check("budget: the oldest unused go, the used stay",
              ts.texture_order == [keys[0], keys[5]], ts.texture_order)
        ts._trim_textures({keys[0], keys[5]})
        check("budget: over it with only used ones, nothing goes", ts.texture_order == [keys[0], keys[5]])
        bw.TEXTURE_BUDGET = 50
        ts._trim_textures(set())
        check("budget: within it afterwards", ts.texture_order == [keys[5]] and
              sum(bw._nbytes(ts.textures[k]) for k in ts.texture_order) <= 50, ts.texture_order)
    finally:
        bw.TEXTURE_BUDGET = old_budget
    real_scene = list(session.textures.values())[0]
    check("budget: bytes of a texture scene count its arrays",
          bw._nbytes(real_scene) > 12 * real_scene.width * real_scene.height)

    # a thin section uses the long side of its texture
    tw_, th_ = bw.Session()._texture_size((-0.1, -0.003, 0.1, 0.003), 5000.0, False)
    sc_ = br.make_view((-0.1, -0.003, 0.1, 0.003), tw_, th_, None, br.TILE_MARGIN)[2]
    check("thin section: the long side is used", sc_ * 0.2 > 0.95 * (max(tw_, th_) - 2 * br.TILE_MARGIN),
          (tw_, th_, sc_ * 0.2))

    # ---------------------------------------------------------------- speed
    for n in (6, 12):
        times = []
        for k in range(5):
            cam = dict(info["camera"])
            cam["azimuth"] = float(cam["azimuth"]) + 7.0 * k
            for coarse in (True, False):
                r, _, _, _ = ask(session, frame=50, coarse=coarse, width=1200, height=900,
                                 **three_d(sheet={"n": n}, camera=cam))
                times.append((coarse, sum(v for v in r["ms"].values() if isinstance(v, float))))
        med = lambda c: sorted(t for k_, t in times if k_ == c)[len(times) // 4]
        print("     {0} sections 1200 x 900: median coarse {1:.0f} ms, fine {2:.0f} ms (worker side)".format(
            n, med(True), med(False)))
        if REAL:
            # research note 02, par. 5: coarse <= 30 ms and fine <= 100 ms
            check("speed with {0} sections: coarse <= 30 ms, fine <= 100 ms".format(n),
                  med(True) <= 30.0 and med(False) <= 100.0, (med(True), med(False)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("{0} failed".format(len(FAILED)) if FAILED else "All checks OK")
sys.exit(1 if FAILED else 0)
