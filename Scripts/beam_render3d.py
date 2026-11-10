# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# beam_render3d.py
# The sections on their place along the chain, in an orthographic 3D view.
# CPython 3 with numpy only; NOT in IronPython.
#
# Plan: docs/research/02_3d_en_detail.md, par. 5 (z-buffer per pixel, texture
# warp of the tile scenes of beam_render).
#
#   Camera(...)                  orthographic camera: turn, zoom, pan, protocol
#   default_camera(...)          a camera that fits the chain and its sections
#   Placement(...)               where a section stands: origin, V, W, magnify
#   Composer3D(w, h).render()    painted Scenes of beam_render -> one picture
#   Composer3D.probe(x, y)       which section, and (v, w) in true metres
#
# A section is flat, so its picture under an orthographic camera is an affine
# map of its tile scene, and its depth is affine in the screen pixels. Per
# section only the pixels inside the projected box of the covered texels are
# visited; each takes the texel under its centre through the inverse map
# (nearest), so colours, contour lines, outline and markers come with the
# texture and never cross into another section. A z-buffer per pixel decides
# which section is in front, also at a kink with magnified sections that cut
# each other. The overlay (texels off the material, the outline, the points G
# and S) has its depth moved BIAS_PX towards the viewer, so it wins against a
# section in the same plane but stays hidden behind any section clearly in
# front. Overlay texels are also splatted forward, one screen pixel per texel,
# and so are the line texels on the material (contour lines, arrows, marker
# rings) where the tile is drawn smaller than it is, so that thin lines do not
# break up. A section nearly on its edge also gets a line along its extent.
# Sections hidden behind others are skipped per 8 x 8 pixel tile (exact).
#
# Coarse and fine share all geometry, so the owner map is the same; fine
# averages 2 x 2 samples per pixel where the tile is drawn smaller than it is,
# coarse takes the one sample at the centre. So coarse is only cheaper where
# the tiles are drawn smaller; zoomed in, both cost the same (the warp, about
# 100 ns per covered pixel; measured in tests/test_beam_render3d.py).
#
# Screen: pixel centres at (col + 0.5, row + 0.5), row 0 on top. Depth in
# pixels (zoom x metres), growing away from the viewer. Angles in degrees.
#
# DEFAULT VIEW (default_camera, also Fit). As Mecway's default view: Y up, X to
# the right, the viewer in front at +Z (the user's report; NOT measured here). Up
# is Z instead when the chain runs within ALONG_Y_DEG of Y; then the viewer is in
# front at -Y, X still to the right. From that front view the viewer stands
# DEFAULT_AZIMUTH to the side of +X (or of -X, whichever sees the sections
# better) and DEFAULT_ELEVATION above. The view is fixed to the world, not to the
# chain, so the side of U the viewer stands on varies: a section can look
# mirrored compared with its 2D tile (the hover and the numbers do not depend on
# it). A chain that would still be seen nearly on edge (|eye . U| below
# EDGE_GUARD_DEG) is looked at from higher or lower instead.

import math
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import beam_render as br  # noqa: E402

DEG_PER_PX = 0.4
# the default view (see the header): from the front, turned and lifted this much
DEFAULT_AZIMUTH = 30.0
DEFAULT_ELEVATION = 20.0
# the chain counts as running along Y within this angle; up is then Z
ALONG_Y_DEG = 20.0
# a default view sees the sections at least this far from their edge if it can
EDGE_GUARD_DEG = 15.0
FIT_MARGIN = 24
ZOOM_MIN, ZOOM_MAX = 1.0e-9, 1.0e12
# depth lead of the overlay on its own plane, in pixels; and of the splatted
# lines on the material, only enough to beat the rounding of their own plane
BIAS_PX = 1.0
LINE_LEAD_PX = 0.05
# |cos| between view and section normal below which there is no plane to warp
EDGE_ON = 1.0e-9
# a projected section thinner than this gets a line along its extent
THIN_PX = 2.0
# below this |cos| (2 degrees) probe gives no (v, w): the map is badly conditioned
PROBE_MIN = math.sin(math.radians(2.0))
# fine: 2 x 2 samples per pixel where one pixel spans more texels than this
SUPERSAMPLE_FROM = 1.25
AXIS_PX = 30.0
AXIS_CORNER = 14.0
LINE_SAMPLES_MAX = 16384
# texels per side of the blocks whose runs are warped
BLOCK = 8
# pixels per side of the occlusion tiles, and the margin of the cull in pixels
TILE = 8
CULL_MARGIN = 1.0e-3
# float32 for the texel coordinates while texels per pixel x (W + H) stay below
FLOAT32_RANGE = 2.0e4
BACKGROUND = br.BACKGROUND
BLACK = br.BLACK
GREY = br.GREY
CHAIN = 0xFF808890
OPAQUE = np.uint32(0xFF000000)
# a texel of the warp texture: 0 is empty, alpha 0xFF material, 0xFE overlay
OVERLAY_ALPHA_MASK = np.uint32(0xFEFFFFFF)
COVERED_FROM = np.uint32(0x00FFFFFF)

_GLYPHS = {"X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
           "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
           "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111")}


# ---------------------------------------------------------------------- camera
def _unit(v, fallback=(0.0, 0.0, 1.0)):
    a = np.asarray(v, dtype=float).reshape(3)
    n = float(np.linalg.norm(a))
    if not n > 1e-300 or not np.isfinite(n):
        return np.array(fallback, dtype=float)
    return a / n


def _horizontal(up):
    """(h1, h2): unit vectors normal to up with h1 x h2 = up. For up along a world
    axis k, h1 is the next axis: Z gives (X, Y), Y gives (Z, X)."""
    k = int(np.argmax(np.abs(up)))
    seed = np.zeros(3)
    seed[(k + 1) % 3] = 1.0
    h1 = seed - seed.dot(up) * up
    h1 /= np.linalg.norm(h1)
    return h1, np.cross(up, h1)


def _angles(direction, up):
    """(azimuth, elevation) in degrees of a unit direction about up."""
    h1, h2 = _horizontal(up)
    a = math.degrees(math.atan2(float(direction.dot(h2)), float(direction.dot(h1))))
    e = math.degrees(math.asin(min(max(float(direction.dot(up)), -1.0), 1.0)))
    return a, e


def _wrap(angle):
    """Angle in (-180, 180]."""
    a = math.fmod(angle, 360.0)
    if a > 180.0:
        a -= 360.0
    elif a <= -180.0:
        a += 360.0
    return a


class Camera(object):
    """Orthographic camera that turns about the axis `up` (a world unit vector).

    azimuth, elevation (degrees): the viewer stands in the direction
        eye = cos(el) (cos(az) h1 + sin(az) h2) + sin(el) up
    from the target, with (h1, h2) of _horizontal(up). Screen right is
    -sin(az) h1 + cos(az) h2, screen up is eye x right.
    target: the world point drawn at the centre of the screen, shifted by pan.
    zoom: pixels per metre. pan: (dx, dy) in pixels.
    """

    def __init__(self, azimuth, elevation, up, target, zoom, pan, width, height):
        self.azimuth = float(azimuth)
        self.elevation = min(max(float(elevation), -90.0), 90.0)
        self.up = _unit(up)
        # values from the protocol: anything not finite becomes 0 (zoom 1)
        target = np.asarray(target, dtype=float).reshape(3)
        self.target = np.where(np.isfinite(target), target, 0.0)
        z = float(zoom)
        self.zoom = min(max(z, ZOOM_MIN), ZOOM_MAX) if np.isfinite(z) else 1.0
        pan = (0.0, 0.0) if pan is None else (float(pan[0]), float(pan[1]))
        self.pan = tuple(p if math.isfinite(p) else 0.0 for p in pan)
        self.width, self.height = max(int(width), 1), max(int(height), 1)
        h1, h2 = _horizontal(self.up)
        a, e = math.radians(self.azimuth), math.radians(self.elevation)
        ca, sa, ce, se = math.cos(a), math.sin(a), math.cos(e), math.sin(e)
        self.eye = ce * (ca * h1 + sa * h2) + se * self.up
        self.right = -sa * h1 + ca * h2
        self.upv = np.cross(self.eye, self.right)

    @property
    def pixels_per_unit(self):
        """Pixels per metre on the screen (orthographic: the zoom)."""
        return self.zoom

    def basis(self):
        """(right, screen up, eye) as world unit vectors; eye points to the viewer."""
        return self.right, self.upv, self.eye

    def _centre(self):
        return 0.5 * self.width + self.pan[0], 0.5 * self.height + self.pan[1]

    def to_screen(self, points):
        """(x, y, depth) arrays of world points (..., 3): pixels, depth in pixels
        growing away from the viewer."""
        p = np.asarray(points, dtype=float)
        d = p - self.target
        cx, cy = self._centre()
        x = cx + self.zoom * d.dot(self.right)
        y = cy - self.zoom * d.dot(self.upv)
        depth = -self.zoom * d.dot(self.eye)
        return x, y, depth

    def unproject(self, x, y, depth=0.0):
        """The world point at screen (x, y) and depth (pixels, 0 at the target)."""
        cx, cy = self._centre()
        return (self.target + (float(x) - cx) / self.zoom * self.right -
                (float(y) - cy) / self.zoom * self.upv - float(depth) / self.zoom * self.eye)

    def _copy(self, **kw):
        args = {"azimuth": self.azimuth, "elevation": self.elevation, "up": self.up,
                "target": self.target, "zoom": self.zoom, "pan": self.pan,
                "width": self.width, "height": self.height}
        args.update(kw)
        return Camera(**args)

    def resized(self, width, height):
        return self._copy(width=width, height=height)

    def rotate(self, dx_px, dy_px):
        """Turn about the point at the centre of the screen: dragging right turns
        the model to the right, dragging down shows more of its top."""
        cx, cy = 0.5 * self.width, 0.5 * self.height
        pivot = self.unproject(cx, cy)
        return self._copy(azimuth=_wrap(self.azimuth - float(dx_px) * DEG_PER_PX),
                          elevation=self.elevation + float(dy_px) * DEG_PER_PX,
                          target=pivot, pan=(0.0, 0.0))

    def zoom_at(self, factor, x, y):
        """Zoom by factor, the world point under (x, y) stays there."""
        f = float(factor)
        if not (f > 0.0 and np.isfinite(f)):
            return self._copy()
        zoom = min(max(self.zoom * f, ZOOM_MIN), ZOOM_MAX)
        f = zoom / self.zoom
        cx, cy = 0.5 * self.width, 0.5 * self.height
        pan = ((float(x) - cx) - f * (float(x) - cx - self.pan[0]),
               (float(y) - cy) - f * (float(y) - cy - self.pan[1]))
        return self._copy(zoom=zoom, pan=pan)

    def pan_by(self, dx, dy):
        return self._copy(pan=(self.pan[0] + float(dx), self.pan[1] + float(dy)))

    def as_dict(self):
        return {"azimuth": self.azimuth, "elevation": self.elevation,
                "up": [float(c) for c in self.up], "target": [float(c) for c in self.target],
                "zoom": self.zoom, "pan": [self.pan[0], self.pan[1]]}

    @classmethod
    def from_dict(cls, d, width, height):
        return cls(d["azimuth"], d["elevation"], d["up"], d["target"], d["zoom"],
                   d.get("pan", (0.0, 0.0)), width, height)


def default_camera(chain_points, half_size, width, height, margin=FIT_MARGIN):
    """A camera that shows the whole chain with its sections, in the default view
    of the header. chain_points: world points along the chain in order;
    half_size: the largest half size of a section as drawn (magnified), metres."""
    p = np.asarray(chain_points, dtype=float).reshape(-1, 3)
    if len(p) == 0:
        p = np.zeros((1, 3))
    u = p[-1] - p[0] if len(p) > 1 else np.zeros(3)
    scale = float(np.abs(p).max()) if len(p) else 1.0
    if not np.linalg.norm(u) > 1e-12 * max(scale, 1e-300):
        steps = np.diff(p, axis=0)
        long_ = [d for d in steps if np.linalg.norm(d) > 1e-12 * max(scale, 1e-300)]
        u = long_[0] if long_ else np.array([1.0, 0.0, 0.0])
    u = _unit(u, (1.0, 0.0, 0.0))
    x_axis = np.array([1.0, 0.0, 0.0])
    if abs(u[1]) >= math.cos(math.radians(ALONG_Y_DEG)):
        up = np.array([0.0, 0.0, 1.0])
    else:
        up = np.array([0.0, 1.0, 0.0])
    front = np.cross(x_axis, up)            # +Z for Y up, -Y for Z up
    # the first view in this order that sees the sections well enough, else the
    # one that sees them best: from +X, from -X, then higher, then lower
    a = math.radians(DEFAULT_AZIMUTH)
    enough = math.sin(math.radians(EDGE_GUARD_DEG))
    best = None
    for el_deg in (DEFAULT_ELEVATION, DEFAULT_ELEVATION + 30.0, DEFAULT_ELEVATION - 30.0):
        e = math.radians(el_deg)
        for side in (1.0, -1.0):
            eye = (math.cos(e) * (math.cos(a) * front + side * math.sin(a) * x_axis) +
                   math.sin(e) * up)
            seen = abs(float(eye.dot(u)))
            if best is None or seen > best[0]:
                best = (seen, eye)
            if seen >= enough:
                best = (seen, eye)
                break
        if best[0] >= enough:
            break
    az, el = _angles(best[1], up)
    centre = p.mean(0) if len(p) else np.zeros(3)
    cam = Camera(az, el, up, centre, 1.0, (0.0, 0.0), width, height)
    d = p - centre
    rx, ry = d.dot(cam.right), d.dot(cam.upv)
    h = max(float(half_size), 0.0)
    x0, x1 = float(rx.min()) - h, float(rx.max()) + h
    y0, y1 = float(ry.min()) - h, float(ry.max()) + h
    usable_w = max(cam.width - 2 * margin, 8)
    usable_h = max(cam.height - 2 * margin, 8)
    span_x, span_y = x1 - x0, y1 - y0
    if not max(span_x, span_y) > 0.0:
        zoom = 1.0
    else:
        zoom = min(usable_w / max(span_x, 1e-300), usable_h / max(span_y, 1e-300))
    target = centre + 0.5 * (x0 + x1) * cam.right + 0.5 * (y0 + y1) * cam.upv
    return Camera(az, el, up, target, zoom, (0.0, 0.0), width, height)


# ------------------------------------------------------------------- placement
class Placement(object):
    """Where a section stands: section point (v, w) is at the world point
    origin + magnify (v V + w W). origin: the centroid on the chain; V, W: the
    drawn axes of the element (beam_model ChainElement.frame[1:]), the same axes
    as the tile scene."""

    def __init__(self, number, s, origin, v_axis, w_axis, magnify):
        self.number = int(number)
        self.s = float(s)
        self.origin = np.asarray(origin, dtype=float).reshape(3).copy()
        self.v_axis = _unit(v_axis, (0.0, 1.0, 0.0))
        self.w_axis = _unit(w_axis, (0.0, 0.0, 1.0))
        self.magnify = float(magnify)

    def world(self, v, w):
        v = np.asarray(v, dtype=float)[..., None]
        w = np.asarray(w, dtype=float)[..., None]
        return self.origin + self.magnify * (v * self.v_axis + w * self.w_axis)


# ------------------------------------------------------------------ rasterising
def _stamp(letter, x, y, width, height, size=1):
    """Pixels of a letter with its top left at (x, y)."""
    out = []
    for r, line in enumerate(_GLYPHS[letter]):
        for c, on in enumerate(line):
            if on == "1":
                for i in range(size):
                    for j in range(size):
                        px, py = x + c * size + i, y + r * size + j
                        if 0 <= px < width and 0 <= py < height:
                            out.append(py * width + px)
    return np.array(out, dtype=np.int64)


def _clip_segments(x0, y0, x1, y1, xmin, ymin, xmax, ymax):
    """Liang-Barsky: parameters (t0, t1) of the part of each segment inside the
    box, and a mask of the segments that have one."""
    dx, dy = x1 - x0, y1 - y0
    t0 = np.zeros(len(x0))
    t1 = np.ones(len(x0))
    ok = np.ones(len(x0), dtype=bool)
    for p, q in ((-dx, x0 - xmin), (dx, xmax - x0), (-dy, y0 - ymin), (dy, ymax - y0)):
        parallel = p == 0.0
        ok &= ~(parallel & (q < 0.0))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(parallel, 0.0, q / np.where(parallel, 1.0, p))
        t0 = np.where(~parallel & (p < 0.0), np.maximum(t0, r), t0)
        t1 = np.where(~parallel & (p > 0.0), np.minimum(t1, r), t1)
    ok &= t0 <= t1
    return t0, t1, ok


def depth_lines(x0, y0, z0, x1, y1, z1, width, height, thick=1):
    """Lines with a depth (arrays of end points): (linear pixel index, depth),
    the depth interpolated along each line. Segments are clipped to the screen
    first, so a long chain off the screen stays one unbroken line."""
    x0, y0, z0, x1, y1, z1 = [np.atleast_1d(np.asarray(a, dtype=float))
                              for a in (x0, y0, z0, x1, y1, z1)]
    good = np.isfinite(x0) & np.isfinite(y0) & np.isfinite(x1) & np.isfinite(y1)
    t0, t1, ok = _clip_segments(x0, y0, x1, y1, -thick - 1.0, -thick - 1.0,
                                width + 1.0, height + 1.0)
    ok &= good
    if not ok.any():
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    x0, y0, z0, x1, y1, z1, t0, t1 = [a[ok] for a in (x0, y0, z0, x1, y1, z1, t0, t1)]
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    ax, ay, az = x0 + t0 * dx, y0 + t0 * dy, z0 + t0 * dz
    bx, by, bz = x0 + t1 * dx, y0 + t1 * dy, z0 + t1 * dz
    n = np.minimum(np.ceil(np.maximum(np.abs(bx - ax), np.abs(by - ay))).astype(np.int64) + 1,
                   LINE_SAMPLES_MAX)
    seg = np.repeat(np.arange(len(ax)), n)
    off = np.arange(len(seg)) - np.repeat(np.cumsum(n) - n, n)
    t = off / np.maximum(n[seg] - 1, 1)
    px = np.floor(ax[seg] + t * (bx - ax)[seg]).astype(np.int64)
    py = np.floor(ay[seg] + t * (by - ay)[seg]).astype(np.int64)
    pz = az[seg] + t * (bz - az)[seg]
    if thick > 1:
        d = np.arange(thick)
        px = np.repeat(px, thick * thick) + np.tile(np.repeat(d, thick), len(px))
        py = np.repeat(py, thick * thick) + np.tile(d, thick * len(py))
        pz = np.repeat(pz, thick * thick)
    inside = (px >= 0) & (px < width) & (py >= 0) & (py < height)
    return py[inside] * width + px[inside], pz[inside]


class _Texture(object):
    """The painted frame of a tile scene as a warp texture: 0 where nothing is
    drawn, the colour with alpha 0xFF on the material, alpha 0xFE on the overlay
    (outline, markers and everything else off the material). Made per render,
    since the scene is painted again between renders."""

    def __init__(self, scene):
        self.scene = scene
        self.sw, self.sh = int(scene.width), int(scene.height)
        frame = scene.frame
        material = scene.index >= 0
        covered = material | (frame != BACKGROUND)
        self.tex = frame * covered
        off = covered & ~material
        self.tex[off] &= OVERLAY_ALPHA_MASK
        fixed = [np.asarray(getattr(scene, name, ()), dtype=np.int64)
                 for name in ("outline", "marks")]
        fixed = np.concatenate(fixed) if fixed else np.zeros(0, dtype=np.int64)
        fixed = fixed[(fixed >= 0) & (fixed < len(self.tex))]
        fixed = fixed[self.tex[fixed] != 0]
        self.tex[fixed] &= OVERLAY_ALPHA_MASK
        self.overlay = np.union1d(np.flatnonzero(off), fixed)
        # thin lines on the material (contour lines, arrows, marker rings), splatted
        # where the tile is drawn smaller, so that they do not break up
        lines = material & ((frame == BLACK) | (frame == br.WHITE) | (frame == br.PEAK))
        self.lines = np.setdiff1d(np.flatnonzero(lines), fixed, assume_unique=False)
        grid = covered.reshape(self.sh, self.sw)
        rows = np.flatnonzero(grid.any(1))
        cols = np.flatnonzero(grid.any(0))
        self.empty = len(rows) == 0
        self.box = None if self.empty else (int(cols[0]), int(rows[0]),
                                            int(cols[-1]), int(rows[-1]))
        self.boxes = None if self.empty else self._runs(grid)

    def _runs(self, grid):
        """Boxes (c0, r0, c1, r1) of texels, inclusive, that hold every covered
        texel: runs of occupied BLOCK x BLOCK blocks along each block row. An open
        section (an I, an L) has most of its box empty; only the runs are warped."""
        B = BLOCK
        nby, nbx = -(-self.sh // B), -(-self.sw // B)
        pad = np.zeros((nby * B, nbx * B), dtype=bool)
        pad[:self.sh, :self.sw] = grid
        occ = pad.reshape(nby, B, nbx, B).any(axis=(1, 3))
        edge = np.diff(np.pad(occ, ((0, 0), (1, 1))).astype(np.int8), axis=1)
        by0, bx0 = np.nonzero(edge == 1)
        by1, bx1 = np.nonzero(edge == -1)
        # nonzero walks row by row, so starts and ends pair up in order; the runs
        # are cut to the tight box, since a pixel is clipped into that box
        c0, r0, c1, r1 = self.box
        return (np.maximum(bx0 * B, c0), np.maximum(by0 * B, r0),
                np.minimum(bx1 * B - 1, c1), np.minimum((by1 + 1) * B - 1, r1))


class _Mapped(object):
    """The affine maps of one section under one camera.
    true (v, w) -> screen:  (X, Y) = o + J (v, w),  depth = oz + g . (v, w)
    texel (sx, sy) -> screen: (X, Y) = t + M (sx, sy), depth = zt + gs . (sx, sy)
    with (v, w) = (x0 + sx / s, y1 - sy / s) of the tile scene."""

    def __init__(self, k, placement, scene, camera):
        self.k = k
        self.placement = placement
        self.number = placement.number
        self.scene = scene
        kz = camera.zoom * placement.magnify
        R, U, E = camera.basis()
        V, W = placement.v_axis, placement.w_axis
        ox, oy, oz = camera.to_screen(placement.origin)
        self.o = np.array([float(ox), float(oy)])
        self.oz = float(oz)
        self.J = np.array([[kz * V.dot(R), kz * W.dot(R)],
                           [-kz * V.dot(U), -kz * W.dot(U)]])
        self.g = np.array([-kz * V.dot(E), -kz * W.dot(E)])
        # |cos| of the angle between the view and the section normal
        self.cos = abs(float(np.linalg.det(self.J))) / (kz * kz) if kz != 0.0 else 0.0
        self.kz = kz
        if scene is None:
            return
        s = float(scene.s)
        D = np.array([[1.0 / s, 0.0], [0.0, -1.0 / s]])
        corner = np.array([float(scene.x0), float(scene.y1)])
        self.M = self.J.dot(D)
        self.t = self.o + self.J.dot(corner)
        self.gs = self.g.dot(D)
        self.zt = self.oz + float(self.g.dot(corner))

    def screen_of_texels(self, sx, sy):
        X = self.t[0] + self.M[0, 0] * sx + self.M[0, 1] * sy
        Y = self.t[1] + self.M[1, 0] * sx + self.M[1, 1] * sy
        Z = self.zt + self.gs[0] * sx + self.gs[1] * sy
        return X, Y, Z


def _spans(m, A, boxes, width, height):
    """Pixels whose centre maps (texel = A (X - t)) into one of the texel boxes
    [c0, c1 + 1] x [r0, r1 + 1]: (row, first column, count) per box and screen
    row. Closed intervals: a pixel on the edge of two boxes may come twice, but
    none falls between them."""
    c0, r0, c1, r1 = [np.asarray(b, dtype=float) for b in boxes]
    t = m.t
    _, ys, _ = m.screen_of_texels(np.stack([c0, c1 + 1.0, c0, c1 + 1.0]),
                                  np.stack([r0, r0, r1 + 1.0, r1 + 1.0]))
    row0 = np.maximum(np.ceil(ys.min(0) - 0.5 - 1e-9), 0).astype(np.int64)
    row1 = np.minimum(np.floor(ys.max(0) - 0.5 + 1e-9), height - 1).astype(np.int64)
    nrows = np.maximum(row1 - row0 + 1, 0)
    if not nrows.any():
        return None
    box = np.repeat(np.arange(len(nrows)), nrows)
    rows = np.arange(len(box)) - np.repeat(np.cumsum(nrows) - nrows, nrows) + row0[box]
    dY = rows + 0.5 - t[1]
    xl = np.full(len(rows), -np.inf)
    xr = np.full(len(rows), np.inf)
    for a, b, lo, hi in ((A[0, 0], A[0, 1], c0[box], c1[box] + 1.0),
                         (A[1, 0], A[1, 1], r0[box], r1[box] + 1.0)):
        rest = b * dY                       # texel coordinate at X = t[0]
        if a == 0.0:
            out = (rest < lo - 1e-9) | (rest > hi + 1e-9)
            xl[out] = np.inf
            xr[out] = -np.inf
            continue
        u0, u1 = (lo - rest) / a, (hi - rest) / a
        xl = np.maximum(xl, np.minimum(u0, u1) + t[0])
        xr = np.minimum(xr, np.maximum(u0, u1) + t[0])
    first = np.ceil(np.clip(xl, -1.0, width + 1.0) - 0.5 - 1e-9)
    last = np.floor(np.clip(xr, -2.0, width + 1.0) - 0.5 + 1e-9)
    first = np.maximum(first, 0).astype(np.int64)
    last = np.minimum(last, width - 1).astype(np.int64)
    count = np.maximum(last - first + 1, 0)
    keep = count > 0
    if not keep.any():
        return None
    return rows[keep], first[keep], count[keep]


# -------------------------------------------------------------------- composer
class Composer3D(object):
    """Composes painted tile scenes into one 3D picture of width x height."""

    def __init__(self, width, height):
        self.width, self.height = max(int(width), 1), max(int(height), 1)
        self._z = np.empty(self.width * self.height, dtype=np.float32)
        # occlusion tiles: the farthest depth in each whole TILE x TILE tile
        self._TH, self._TW = self.height // TILE, self.width // TILE
        self._tz = np.empty(self._TH * self._TW, dtype=np.float32)
        self._toff = (np.arange(TILE)[:, None] * self.width + np.arange(TILE)[None, :]).ravel()
        self._tz_any = False
        self._textures = {}
        self._last = None

    def render(self, items, camera, coarse=False, chain=None, axes=True, thick=1):
        """items: [(Placement, painted beam_render.Scene)], the scene in the drawn
        axes of its placement (scene None: only the anchor). chain: world points
        of the chain, drawn as a thin line (optional). The scene may be any object
        with frame, index, width, height, x0, y1, s (outline and marks optional).
        Returns {"frame": uint32 0xAARRGGBB HxW (bytes B, G, R, A), "bgra": the
        same as uint8 HxWx4, "owner": int32 HxW with the placement number (-1 none),
        "anchors": {number: (x, y)} of the origins, "boxes": {number: (x0, y0, x1,
        y1)} on the screen, "axes": {"O", "X", "Y", "Z": (x, y)}, "times": ms}."""
        t_start = time.perf_counter()
        W, H = self.width, self.height
        thick = max(int(thick), 1)
        if camera.width != W or camera.height != H:
            camera = camera.resized(W, H)
        frame = np.full(W * H, BACKGROUND, dtype=np.uint32)
        owner = np.full(W * H, -1, dtype=np.int32)
        z = self._z
        z.fill(np.inf)
        self._tz.fill(np.inf)
        self._tz_any = False
        mapped, anchors, boxes = [], {}, {}
        times = {"texture": 0.0, "warp": 0.0, "lines": 0.0}
        for k, (placement, scene) in enumerate(items):
            m = _Mapped(k, placement, scene, camera)
            anchors[m.number] = (float(m.o[0]), float(m.o[1]))
            mapped.append(m)
        # front to back: fewer writes; the number makes exact ties independent of
        # the order of the items
        mapped.sort(key=lambda m: (m.oz, m.number))
        textures = {}
        for m in mapped:
            if m.scene is None:
                continue
            t0 = time.perf_counter()
            tex = textures.get(id(m.scene)) or self._texture(m.scene)
            textures[id(m.scene)] = tex
            t1 = time.perf_counter()
            times["texture"] += (t1 - t0) * 1e3
            if tex.empty:
                continue
            boxes[m.number] = self._box(m, tex)
            if m.cos >= EDGE_ON:
                self._warp(m, tex, frame, owner, z, coarse)
            t2 = time.perf_counter()
            times["warp"] += (t2 - t1) * 1e3
            self._overlay(m, tex, frame, owner, z, thick)
            times["lines"] += (time.perf_counter() - t2) * 1e3
        t3 = time.perf_counter()
        if chain is not None and len(chain) > 1:
            self._chain(chain, camera, frame, z, thick)
        axes_at = self._axes(camera, frame, thick, axes)
        times["lines"] += (time.perf_counter() - t3) * 1e3
        times["total"] = (time.perf_counter() - t_start) * 1e3
        # only the textures of this render stay: no growth over many scenes
        self._textures = dict((k, self._textures[k]) for k in textures if k in self._textures)
        owner2 = owner.reshape(H, W)
        self._last = {"owner": owner2, "mapped": dict((m.number, m) for m in mapped)}
        frame2 = frame.reshape(H, W)
        return {"frame": frame2, "bgra": frame2.view(np.uint8).reshape(H, W, 4),
                "owner": owner2, "anchors": anchors, "boxes": boxes, "axes": axes_at,
                "times": times}

    # -- one section
    def _texture(self, scene):
        """The warp texture of a scene, again from the last render while the
        painted frame is the same (turning the view does not paint)."""
        old = self._textures.get(id(scene))
        if (old is not None and old[0] is scene and old[1].shape == scene.frame.shape
                and np.array_equal(old[1], scene.frame)):
            return old[2]
        tex = _Texture(scene)
        self._textures[id(scene)] = (scene, scene.frame.copy(), tex)
        return tex

    def _box(self, m, tex):
        c0, r0, c1, r1 = tex.box
        X, Y, _ = m.screen_of_texels(np.array([c0, c1 + 1.0, c0, c1 + 1.0]),
                                     np.array([r0, r0, r1 + 1.0, r1 + 1.0]))
        return (float(X.min()), float(Y.min()), float(X.max()), float(Y.max()))

    def _warp(self, m, tex, frame, owner, z, coarse):
        """The covered pixels of the projected texel box, the texel under each
        pixel centre, and the z-test."""
        W, H = self.width, self.height
        c0, r0, c1, r1 = tex.box
        A = np.linalg.inv(m.M)
        if not np.all(np.isfinite(A)):
            return
        spans = _spans(m, A, tex.boxes, W, H)
        if spans is None:
            return
        q = m.gs.dot(A)                      # depth per screen pixel
        rows, first, count, tiles = self._unhidden(
            spans, q, m.zt + q[0] * (0.5 - m.t[0]) + q[1] * (spans[0] + 0.5 - m.t[1]))
        if len(rows) == 0:
            return
        K = int(count.sum())
        start = np.cumsum(count) - count
        # texels per pixel: the largest singular value of A. float32 while the
        # texel coordinates keep 1e-3 texel; float64 for a section nearly on edge
        spread = float(np.linalg.norm(A, 2))
        ft = np.float32 if spread * (W + H) < FLOAT32_RANGE else np.float64
        # per pixel: column, screen index and the row terms of the affine maps
        cols = np.arange(K, dtype=np.int32)
        cols += np.repeat((first - start).astype(np.int32), count)
        lin = cols + np.repeat((rows * W).astype(np.int32), count)
        cf = cols.astype(ft)
        Yr = rows + 0.5 - m.t[1]
        bx = A[0, 0] * (0.5 - m.t[0]) + A[0, 1] * Yr
        by = A[1, 0] * (0.5 - m.t[0]) + A[1, 1] * Yr
        bz = m.zt + q[0] * (0.5 - m.t[0]) + q[1] * Yr
        fx = cf * ft(A[0, 0])
        fx += np.repeat(bx.astype(ft), count)
        fy = cf * ft(A[1, 0])
        fy += np.repeat(by.astype(ft), count)
        ix = fx.astype(np.int32)
        np.clip(ix, c0, c1, out=ix)
        iy = fy.astype(np.int32)
        np.clip(iy, r0, r1, out=iy)
        iy *= tex.sw
        iy += ix
        colour = tex.tex.take(iy)
        depth = cf * ft(q[0])
        depth += np.repeat(bz.astype(ft), count)
        np.subtract(depth, ft(BIAS_PX), out=depth, where=colour < OPAQUE)
        if ft is not np.float32:
            depth = depth.astype(np.float32)
        win = np.flatnonzero((depth < z.take(lin)) & (colour > COVERED_FROM))
        if len(win) == 0:
            return
        p = lin[win]
        z[p] = depth[win]
        owner[p] = m.number
        self._tiles_after(tiles, z)
        if not coarse and spread > SUPERSAMPLE_FROM:
            frame[p] = self._supersample(A, tex, fx[win], fy[win], colour[win])
        else:
            frame[p] = colour[win] | OPAQUE

    def _unhidden(self, spans, q, bz):
        """The spans cut at the tile edges, without the pieces that lie wholly
        behind what is drawn: the nearest depth of a piece (at one of its ends,
        the overlay lead included) is not in front of the farthest depth of its
        tile. Exact, so the picture does not change; it saves the sampling of
        sections hidden behind others. bz: depth at column 0 of each span row.
        Returns (rows, first, count, tiles): tiles, the whole tiles the pieces
        that are left touch (-1 for a piece in a part tile at the edge)."""
        rows, first, count = spans
        T = TILE
        last = first + count - 1
        tx0 = first // T
        n = last // T - tx0 + 1
        sid = np.repeat(np.arange(len(rows), dtype=np.int32), n)
        tx = (np.arange(len(sid), dtype=np.int32) +
              np.repeat((tx0 - (np.cumsum(n) - n)).astype(np.int32), n))
        s_first = np.maximum(first.astype(np.int32)[sid], tx * T)
        s_last = np.minimum(last.astype(np.int32)[sid], tx * T + (T - 1))
        r = rows.astype(np.int32)[sid]
        ty = r // T
        tile = ty * self._TW + tx
        tile[(tx >= self._TW) | (ty >= self._TH)] = -1
        if self._tz_any:
            far = self._tz.take(np.maximum(tile, 0)).astype(float)
            far[tile < 0] = np.inf
            # a pixel's depth may be made in float32: the margin holds its rounding
            near = bz[sid] + np.minimum(q[0] * s_first, q[0] * s_last) - BIAS_PX
            margin = CULL_MARGIN + 1e-6 * (np.abs(near) + 2.0 * abs(q[0]) * self.width)
            keep = np.flatnonzero(near < far + margin)
            r, s_first, s_last, tile = r[keep], s_first[keep], s_last[keep], tile[keep]
        return r, s_first, s_last - s_first + 1, tile

    def _tiles_after(self, tiles, z):
        """The farthest depth again in the whole tiles that were drawn on."""
        T, W, TH, TW = TILE, self.width, self._TH, self._TW
        if TW == 0 or TH == 0:
            return
        mark = np.zeros(TH * TW, dtype=bool)
        mark[tiles[tiles >= 0]] = True
        touched = np.flatnonzero(mark)
        if len(touched) == 0:
            return
        if 2 * len(touched) > TH * TW:
            grid = z.reshape(self.height, W)[:TH * T, :TW * T].reshape(TH, T, TW, T)
            self._tz[:] = grid.max(axis=(1, 3)).ravel()
        else:
            base = (touched // TW) * (T * W) + (touched % TW) * T
            self._tz[touched] = z.take(base[:, None] + self._toff[None, :]).max(1)
        self._tz_any = True

    def _supersample(self, A, tex, fx, fy, centre):
        """The mean of 2 x 2 samples at +-1/4 pixel, of the covered ones only, so
        that nothing of the background or another section mixes in."""
        c0, r0, c1, r1 = tex.box
        texels = tex.tex.view(np.uint8).reshape(-1, 4)
        total = np.zeros((len(fx), 4), dtype=np.uint16)
        n = np.zeros(len(fx), dtype=np.uint16)
        for dx, dy in ((-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25), (0.25, 0.25)):
            ix = (fx + (A[0, 0] * dx + A[0, 1] * dy)).astype(np.int64)
            np.clip(ix, c0, c1, out=ix)
            iy = (fy + (A[1, 0] * dx + A[1, 1] * dy)).astype(np.int64)
            np.clip(iy, r0, r1, out=iy)
            sample = texels[iy * tex.sw + ix]
            on = sample[:, 3] != 0
            total += sample * on[:, None]
            n += on
        out = centre | OPAQUE
        some = n > 0
        mean = ((total[some] + (n[some] // 2)[:, None]) // n[some][:, None]).astype(np.uint8)
        mean[:, 3] = 0xFF
        out[some] = np.ascontiguousarray(mean).view(np.uint32).ravel()
        return out

    def _overlay(self, m, tex, frame, owner, z, thick):
        """Overlay texels splatted forward, and a line along a section that is
        nearly on its edge."""
        W, H = self.width, self.height
        if len(tex.lines) and np.linalg.svd(m.M, compute_uv=False)[-1] < 1.0:
            # lines on the material, on their own plane: no lead in depth
            sx = (tex.lines % tex.sw) + 0.5
            sy = (tex.lines // tex.sw) + 0.5
            X, Y, Z = m.screen_of_texels(sx, sy)
            px = np.floor(X).astype(np.int64)
            py = np.floor(Y).astype(np.int64)
            ok = np.flatnonzero((px >= 0) & (px < W) & (py >= 0) & (py < H))
            self._zwrite(py[ok] * W + px[ok], (Z[ok] - LINE_LEAD_PX).astype(np.float32),
                         tex.tex.take(tex.lines[ok]) | OPAQUE, m.number, frame, owner, z)
        idx = tex.overlay
        if len(idx) == 0:
            c0, r0, c1, r1 = tex.box
            sx = np.array([c0, c1 + 1.0, c0, c1 + 1.0])
            sy = np.array([r0, r0, r1 + 1.0, r1 + 1.0])
            colours = np.full(4, BLACK, dtype=np.uint32)
            splat = False
        else:
            sx = (idx % tex.sw) + 0.5
            sy = (idx // tex.sw) + 0.5
            colours = tex.tex.take(idx) | OPAQUE
            splat = True
        X, Y, Z = m.screen_of_texels(sx, sy)
        Z = Z - BIAS_PX
        if splat:
            px = np.floor(X).astype(np.int64)
            py = np.floor(Y).astype(np.int64)
            ok = np.flatnonzero((px >= 0) & (px < W) & (py >= 0) & (py < H))
            self._zwrite(py[ok] * W + px[ok], Z[ok].astype(np.float32), colours[ok],
                         m.number, frame, owner, z)
        c0, r0, c1, r1 = tex.box
        e1 = m.M.dot([c1 + 1.0 - c0, 0.0])
        e2 = m.M.dot([0.0, r1 + 1.0 - r0])
        area = abs(e1[0] * e2[1] - e1[1] * e2[0])
        longest = max(np.hypot(*e1), np.hypot(*e2), np.hypot(*(e1 + e2)),
                      np.hypot(*(e1 - e2)))
        if longest > 0.0 and area / longest >= THIN_PX:
            return
        # along the extent: the overlay points furthest apart on the long axis
        u, _, _ = np.linalg.svd(m.M)
        d = u[:, 0]
        along = X * d[0] + Y * d[1]
        i, j = int(np.argmin(along)), int(np.argmax(along))
        lin, depth = depth_lines([X[i]], [Y[i]], [Z[i]], [X[j]], [Y[j]], [Z[j]], W, H, thick)
        self._zwrite(lin, depth.astype(np.float32),
                     np.full(len(lin), BLACK, dtype=np.uint32), m.number, frame, owner, z)

    @staticmethod
    def _zwrite(lin, depth, colour, number, frame, owner, z):
        if len(lin) == 0:
            return
        win = np.flatnonzero(depth < z.take(lin))
        p = lin[win]
        z[p] = depth[win]
        owner[p] = number
        frame[p] = colour[win]

    # -- chain and axes
    def _chain(self, chain, camera, frame, z, thick):
        """The chain as a thin line, behind the sections where they hide it. It
        is no section: it leaves the owner and the z-buffer alone."""
        X, Y, Z = camera.to_screen(np.asarray(chain, dtype=float).reshape(-1, 3))
        lin, depth = depth_lines(X[:-1], Y[:-1], Z[:-1], X[1:], Y[1:], Z[1:],
                                 self.width, self.height, thick)
        if len(lin):
            frame[lin[depth.astype(np.float32) < z.take(lin)]] = CHAIN

    def _axes(self, camera, frame, thick, draw):
        """World axes in the lower left corner, on top of everything."""
        W, H = self.width, self.height
        n = AXIS_PX * thick
        o = (AXIS_CORNER * thick + n, H - AXIS_CORNER * thick - n)
        out = {"O": (float(o[0]), float(o[1]))}
        lines, letters = [], []
        for name, e in (("X", (1.0, 0.0, 0.0)), ("Y", (0.0, 1.0, 0.0)), ("Z", (0.0, 0.0, 1.0))):
            e = np.array(e)
            dx, dy = float(e.dot(camera.right)), -float(e.dot(camera.upv))
            end = (o[0] + n * dx, o[1] + n * dy)
            out[name] = (float(end[0]), float(end[1]))
            lines.append((o[0], o[1], end[0], end[1]))
            letters.append((name, end[0] + 7.0 * thick * dx - 2.5 * thick,
                            end[1] + 7.0 * thick * dy - 3.5 * thick))
        if draw:
            a = np.array(lines, dtype=float)
            frame[br.line_pixels(a[:, 0], a[:, 1], a[:, 2], a[:, 3], W, H, thick)] = GREY
            for name, x, y in letters:
                frame[_stamp(name, int(round(x)), int(round(y)), W, H, thick)] = GREY
        return out

    # -- hover
    def probe(self, x, y):
        """{"number", "v", "w", "s", "item"} under pixel (x, y), v and w in true
        metres in the drawn axes of the section; without v and w when the section
        is nearly on its edge; None off the sections. Of the last render."""
        if self._last is None:
            return None
        x, y = int(x), int(y)
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        number = int(self._last["owner"][y, x])
        if number < 0:
            return None
        m = self._last["mapped"].get(number)
        if m is None:
            return None
        out = {"number": number, "s": m.placement.s, "item": m.k}
        if m.cos < PROBE_MIN:
            return out
        v, w = np.linalg.solve(m.J, np.array([x + 0.5, y + 0.5]) - m.o)
        out["v"], out["w"] = float(v), float(w)
        return out
