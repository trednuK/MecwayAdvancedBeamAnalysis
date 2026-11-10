# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# beam_render.py
# From stress values on the mesh of a section to a picture on device pixels.
# CPython 3 with numpy and scipy only; NOT in IronPython.
#
# The measured route (research note par. 6.1): the pixel map (which pixel lies
# in which triangle, with which weights) is built once per size and view as a
# sparse matrix P (pixels x nodes), a frame is then P.dot(values), a look-up
# table and a write into a frame buffer. Values on the nodes, float32, arrays
# kept between frames. Weighted look-up and writing were the bottleneck of
# the measurement; a new pixel map is the slow step (50 to 440 ms), hence a
# coarse scene at a quarter of the size in between.
#
#   UnitFields(field)            nodal unit fields of a section_stress field
#   make_view / Scene            the pixel map, outline, arrow grid
#   Scene.paint(...)             one frame into Scene.frame
#                                contour lines from the classes of the pixels
#   Scene.value_at(x, y)         (triangle nodes, weights) under a pixel
#   upscale(...)                 coarse frame to full size
#   tile_grid(...)               several sections on one canvas
#
# Scene coordinates: pixel centres at (col + 0.5, row + 0.5), row 0 on top,
# V to the right and W up, so the section is drawn looking along -U.
# A pixel is 0xAARRGGBB in a uint32 (bytes B, G, R, A in memory).

import math
import os
import sys

import numpy as np
import scipy.sparse as sp

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from beam_utils import FORCES, LEGEND_STEPS, RAMP  # noqa: E402

# tri6 of section_stress: corners 0, 1, 2, midsides 3 (0-1), 4 (1-2), 5 (2-0)
SUB_TRIANGLES = np.array([[0, 3, 5], [3, 1, 4], [5, 4, 2], [3, 4, 5]])
LUT_SIZE = 1024
COARSE = 4
BACKGROUND = 0xFFFFFFFF
BLACK = 0xFF000000
GREY = 0xFF505A64
WHITE = 0xFFFFFFFF
PEAK = 0xFFE000D0
MARGIN = 28
TILE_MARGIN = 6
ARROW_SEEDS = 150
ARROW_MIN = 2.0
# Contour lines. A field that sits exactly on a level would speckle by the rounding of
# float32, so a level is moved this part of a class; and below this range, as a part
# of the values, a field is flat and has no lines.
CONTOUR_SLIP = 1.0e-4
CONTOUR_FLAT = 1.0e-5
# Below this luminance of the colour under it a line is white, else black: white only
# on the dark blue, for the red end of the ramp (80) black reads better
CONTOUR_DARK = 70.0

# 5 x 7 letters for the axes and the points G and S
_GLYPHS = {"V": ("10001", "10001", "10001", "10001", "01010", "01010", "00100"),
           "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
           "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
           "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110")}


def make_lut(size=LUT_SIZE):
    """Look-up table of RAMP as uint32 0xAARRGGBB."""
    x = np.linspace(0.0, 1.0, size)
    stops = np.array(RAMP, dtype=float)
    r = np.interp(x, stops[:, 0], stops[:, 1]).astype(np.uint32)
    g = np.interp(x, stops[:, 0], stops[:, 2]).astype(np.uint32)
    b = np.interp(x, stops[:, 0], stops[:, 3]).astype(np.uint32)
    return (0xFF000000 + (r << 16) + (g << 8) + b).astype(np.uint32)


def make_line_lut(lut):
    """The colour of a contour line on each colour of the look-up table, so that a
    line is readable on the whole ramp."""
    r = (lut >> 16) & 0xFF
    g = (lut >> 8) & 0xFF
    b = lut & 0xFF
    dark = 0.299 * r + 0.587 * g + 0.114 * b < CONTOUR_DARK
    return np.where(dark, WHITE, BLACK).astype(np.uint32)


class UnitFields(object):
    """The stress on the nodes per unit section force, so that a frame is a sum
    of six fields. Made with the stress and the averaging of the core, so no
    formula is repeated here: T counts as the torque about the shear centre
    (about_shear_centre=True); the caller adds the term to it."""

    def __init__(self, field):
        self.field = field
        n = len(field.nodes)
        self.has_tau = field.fields is not None
        self.sigma = np.zeros((6, n), dtype=np.float32)
        self.tau_v = np.zeros((6, n), dtype=np.float32)
        self.tau_w = np.zeros((6, n), dtype=np.float32)
        for i, name in enumerate(FORCES):
            s = field.stress({name: 1.0}, about_shear_centre=True)
            self.sigma[i] = field.nodal(s["sigma"])
            if self.has_tau:
                self.tau_v[i] = field.nodal(s["tau_v"])
                self.tau_w[i] = field.nodal(s["tau_w"])
        # A node counts as outside the corner zones when none of its element
        # nodes lies inside one.
        inside = np.zeros(n, dtype=bool)
        inside[field.elements[~field.outside]] = True
        self.outside = ~inside
        if not self.outside.any():
            self.outside = np.ones(n, dtype=bool)
        self._s = np.zeros(n, dtype=np.float32)
        self._tv = np.zeros(n, dtype=np.float32)
        self._tw = np.zeros(n, dtype=np.float32)

    def nodal(self, values):
        """(sigma, tau_v, tau_w) on the nodes for the six values in the order
        FORCES, with T the torque about the shear centre; tau None when the
        section has no shear fields. The arrays are reused by the next call."""
        a = np.asarray(values, dtype=np.float32)
        np.dot(a, self.sigma, out=self._s)
        if not self.has_tau:
            return self._s, None, None
        np.dot(a, self.tau_v, out=self._tv)
        np.dot(a, self.tau_w, out=self._tw)
        return self._s, self._tv, self._tw


def scale_range(q, outside):
    """Automatic colour scale: lowest and highest value outside the corner
    zones, since the peak in a sharp corner is an artefact (research note par.
    5.3). Returns (lo, hi); hi > lo always."""
    vals = q[outside]
    lo, hi = float(vals.min()), float(vals.max())
    if not hi > lo:
        d = max(abs(hi) * 1e-6, 1e-6)
        lo, hi = lo - d, hi + d
    return lo, hi


def loop_bounds(loops):
    """(vmin, wmin, vmax, wmax) of closed loops."""
    p = np.vstack(loops)
    return (float(p[:, 0].min()), float(p[:, 1].min()),
            float(p[:, 0].max()), float(p[:, 1].max()))


def tile_grid(n, width, height, bounds, band):
    """(columns, rows, tile width, tile height) of n pictures on a canvas: the
    grid that gives the sections the largest scale. band: height of the label
    above each picture."""
    du = max(bounds[2] - bounds[0], 1e-12)
    dv = max(bounds[3] - bounds[1], 1e-12)
    best = None
    for cols in range(1, n + 1):
        rows = (n + cols - 1) // cols
        tw, th = width // cols, height // rows
        scale = min((tw - 2 * TILE_MARGIN) / du, (th - band - 2 * TILE_MARGIN) / dv)
        if best is None or scale > best[0]:
            best = (scale, cols, rows, tw, th)
    return best[1:]


def make_view(bounds, width, height, view=None, margin=MARGIN):
    """(x0, y1, scale): pixel x = (v - x0) * scale, y = (y1 - w) * scale, for a
    bounding box (vmin, wmin, vmax, wmax) of the section. view: None for a fit,
    or {"zoom": factor on the fit, "cx", "cy": V, W at the centre}."""
    vmin, wmin, vmax, wmax = bounds
    du = max(vmax - vmin, 1e-12)
    dv = max(wmax - wmin, 1e-12)
    usable_w = max(width - 2 * margin, 8)
    usable_h = max(height - 2 * margin, 8)
    s = min(usable_w / du, usable_h / dv)
    cx, cy = 0.5 * (vmin + vmax), 0.5 * (wmin + wmax)
    if view:
        s *= float(view.get("zoom", 1.0))
        cx = float(view.get("cx", cx))
        cy = float(view.get("cy", cy))
    return cx - 0.5 * width / s, cy + 0.5 * height / s, s


def raster_triangles(T, width, height, eps=1e-7):
    """Scanline rasteriser, fully vectorised. T (M,3,2) in pixels. Returns
    (pix, tri): linear pixel index (sorted) and triangle per pixel, for the pixels
    whose centre lies in a triangle. On a shared edge the last triangle wins."""
    xs, ys = T[:, :, 0], T[:, :, 1]
    row0 = np.maximum(np.ceil(ys.min(1) - 0.5 - eps).astype(np.int64), 0)
    row1 = np.minimum(np.floor(ys.max(1) - 0.5 + eps).astype(np.int64),
                      height - 1)
    keep = (row1 >= row0) & (xs.max(1) >= -1.0) & (xs.min(1) <= width + 1.0)
    ids = np.flatnonzero(keep)
    if len(ids) == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int32)
    Tk = T[ids]
    row0 = row0[ids]
    nrows = row1[ids] - row0 + 1
    tr = np.repeat(np.arange(len(ids)), nrows)
    rows = np.arange(len(tr)) - np.repeat(np.cumsum(nrows) - nrows, nrows) + row0[tr]
    yc = rows + 0.5
    x = Tk[tr, :, 0]
    y = Tk[tr, :, 1]
    x2 = np.roll(x, -1, axis=1)
    y2 = np.roll(y, -1, axis=1)
    dy = y2 - y
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (yc[:, None] - y) / dy
    valid = (dy != 0) & (t >= -1e-9) & (t <= 1 + 1e-9)
    xi = x + t * (x2 - x)
    xl = np.where(valid, xi, np.inf).min(1)
    xr = np.where(valid, xi, -np.inf).max(1)
    good = np.isfinite(xl)
    tr, rows, xl, xr = tr[good], rows[good], xl[good], xr[good]
    c0 = np.maximum(np.ceil(xl - 0.5 - eps), 0).astype(np.int64)
    c1 = np.minimum(np.floor(xr - 0.5 + eps), width - 1).astype(np.int64)
    nc = np.maximum(c1 - c0 + 1, 0)
    tp = np.repeat(tr, nc)
    rp = np.repeat(rows, nc)
    col = np.arange(len(tp)) - np.repeat(np.cumsum(nc) - nc, nc) + np.repeat(c0, nc)
    dense = np.full(width * height, -1, dtype=np.int32)
    dense[rp * width + col] = ids[tp]
    pix = np.flatnonzero(dense >= 0)
    return pix, dense[pix]


def line_pixels(x0, y0, x1, y1, width, height, thick=1):
    """Vectorised lines (arrays of end points): linear pixel indices."""
    dx = x1 - x0
    dy = y1 - y0
    # segments far outside are clipped to a bounded length
    n = np.minimum(np.ceil(np.maximum(np.abs(dx), np.abs(dy))).astype(np.int64) + 1,
                   8192)
    seg = np.repeat(np.arange(len(x0)), n)
    off = np.arange(len(seg)) - np.repeat(np.cumsum(n) - n, n)
    t = off / np.maximum(n[seg] - 1, 1)
    px = np.floor(x0[seg] + t * dx[seg]).astype(np.int64)
    py = np.floor(y0[seg] + t * dy[seg]).astype(np.int64)
    if thick > 1:
        d = np.arange(thick)
        px = np.repeat(px, thick * thick) + np.tile(np.repeat(d, thick), len(px))
        py = np.repeat(py, thick * thick) + np.tile(d, thick * len(py))
    ok = (px >= 0) & (px < width) & (py >= 0) & (py < height)
    return py[ok] * width + px[ok]


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


def _segments(points):
    p = np.asarray(points, dtype=float)
    return p[:-1, 0], p[:-1, 1], p[1:, 0], p[1:, 1]


class Scene(object):
    """Everything that depends on size and view. xy: nodes (N,2) relative to
    the centroid; elements (E,6); loops in the same coordinates; points:
    {"G": (v, w), "S": (v, w) or None}; thick: line width in device pixels."""

    def __init__(self, xy, elements, loops, width, height, view=None, thick=1,
                 points=None, axes_at_corner=True, margin=MARGIN, bounds=None):
        self.width, self.height = width, height
        self.thick = thick
        self.bounds = bounds or loop_bounds(loops)
        self.x0, self.y1, self.s = make_view(self.bounds, width, height, view,
                                             margin)
        pn = np.empty(xy.shape, dtype=float)
        pn[:, 0] = (xy[:, 0] - self.x0) * self.s
        pn[:, 1] = (self.y1 - xy[:, 1]) * self.s
        v_all = elements[:, SUB_TRIANGLES].reshape(-1, 3)
        T = pn[v_all]
        pix, tri = raster_triangles(T, width, height)
        px, py = (pix % width) + 0.5, (pix // width) + 0.5
        # plane of each weight per triangle, taken per pixel: no T[tri] copy
        xa, ya = T[:, 0, 0], T[:, 0, 1]
        xb, yb = T[:, 1, 0], T[:, 1, 1]
        xc, yc = T[:, 2, 0], T[:, 2, 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            det = 1.0 / ((xb - xa) * (yc - ya) - (xc - xa) * (yb - ya))
        l0 = ((yb - yc) * det).take(tri) * px + ((xc - xb) * det).take(tri) * py + \
            ((xb * yc - xc * yb) * det).take(tri)
        l1 = ((yc - ya) * det).take(tri) * px + ((xa - xc) * det).take(tri) * py + \
            ((xc * ya - xa * yc) * det).take(tri)
        L = np.stack([l0, l1, 1.0 - l0 - l1], axis=1)
        L = np.clip(L, 0.0, 1.0)
        total = L.sum(1, keepdims=True)
        L /= np.where(total > 0.0, total, 1.0)
        self.pix = pix
        self.nodes_of_pixel = v_all[tri]
        self.weights = L.astype(np.float32)
        K = len(pix)
        self.n_pixels = K
        self.P = sp.csr_matrix(
            (self.weights.ravel(), self.nodes_of_pixel.ravel().astype(np.int32),
             np.arange(0, 3 * K + 1, 3, dtype=np.int32)),
            shape=(K, len(xy)))
        self.index = np.full(width * height, -1, dtype=np.int32)
        self.index[pix] = np.arange(K, dtype=np.int32)
        self.frame = np.zeros(width * height, dtype=np.uint32)
        self.lut = make_lut()
        self._idx = np.zeros(K, dtype=np.int32)
        self._colour = np.zeros(K, dtype=np.uint32)
        self._pairs = None                  # made when contour lines are first asked
        self.template = np.full(width * height, BACKGROUND, dtype=np.uint32)
        self._outline(loops)
        self._arrow_grid(len(xy))
        self._overlay(points or {}, axes_at_corner)

    def pixel_of(self, v, w):
        return (v - self.x0) * self.s, (self.y1 - w) * self.s

    def _outline(self, loops):
        parts = []
        for loop in loops:
            lp = np.asarray(loop, dtype=float)
            lp = np.vstack([lp, lp[:1]])
            x, y = self.pixel_of(lp[:, 0], lp[:, 1])
            parts.append(line_pixels(x[:-1], y[:-1], x[1:], y[1:], self.width,
                                     self.height, self.thick))
        self.outline = np.unique(np.concatenate(parts))

    def _contour_pairs(self):
        """Neighbouring pixels of the material, as positions in self.pix: right and
        below. A pair off the material does not exist, so a line cannot run over an
        empty pixel. Part of the scene, made once: only those who use the lines pay."""
        grid = self.index.reshape(self.height, self.width)
        first, second = [], []
        for a, b in ((grid[:, :-1], grid[:, 1:]), (grid[:-1, :], grid[1:, :])):
            both = (a >= 0) & (b >= 0)
            first.append(a[both])
            second.append(b[both])
        self._pairs = (np.concatenate(first), np.concatenate(second))
        self._line_lut = make_line_lut(self.lut)
        self._shifted = np.zeros(self.n_pixels, dtype=np.float32)
        self._class = np.zeros(self.n_pixels, dtype=np.int8)

    def _classes(self, v, span):
        """The class of each pixel from v, the value minus lo: 0 below lo, 1 to
        LEGEND_STEPS between the levels, one more from hi up. Everything above hi is
        one class, so a peak in a corner adds no lines; the same below lo."""
        if self._pairs is None:
            self._contour_pairs()
        w = self._shifted
        np.multiply(v, LEGEND_STEPS / span, out=w)
        np.add(w, 1.0 + CONTOUR_SLIP, out=w)
        np.clip(w, 0.0, LEGEND_STEPS + 1.0, out=w)
        self._class[:] = w

    def _contours(self):
        """Lines on the pixel of the lower class of each pair that differs: always
        the same side of a level, so one pixel wide in every direction, and closed
        along a diagonal. At thick 2 and more both pixels."""
        a, b = self._pairs
        ca, cb = self._class[a], self._class[b]
        change = np.flatnonzero(ca != cb)
        first, second = a[change], b[change]
        sides = (first, second) if self.thick > 1 else (
            np.where(ca[change] < cb[change], first, second),)
        for hit in sides:
            self.frame[self.pix[hit]] = self._line_lut.take(self._idx[hit])

    def _arrow_grid(self, n_nodes):
        """Seeds on a regular pixel grid, on the material only."""
        K = max(self.n_pixels, 1)
        step = max(int(math.sqrt(K / float(ARROW_SEEDS))), 12 * self.thick)
        off = step // 2
        grid = self.index.reshape(self.height, self.width)[off::step, off::step]
        sel = grid[grid >= 0].astype(np.int64)
        pix = self.pix[sel]
        self.seed_x = (pix % self.width) + 0.5
        self.seed_y = (pix // self.width) + 0.5
        S = len(sel)
        self.P_seed = sp.csr_matrix(
            (self.weights[sel].ravel(),
             self.nodes_of_pixel[sel].ravel().astype(np.int32),
             np.arange(0, 3 * S + 1, 3, dtype=np.int32)), shape=(S, n_nodes))
        self.arrow_max_px = 0.9 * step      # neighbours do not touch at factor 1

    def _overlay(self, points, axes_at_corner):
        """Fixed marks in template-independent lists: drawn over the colours."""
        w, h, t = self.width, self.height, self.thick
        marks = []
        for name in ("G", "S"):
            p = points.get(name)
            if p is None:
                continue
            x, y = self.pixel_of(float(p[0]), float(p[1]))
            r = 4.0 * t
            xs = np.array([x - r, x])
            ys = np.array([y, y - r])
            xe = np.array([x + r, x])
            ye = np.array([y, y + r])
            marks.append(line_pixels(xs, ys, xe, ye, w, h, t))
            marks.append(_stamp(name, int(x + 2 * t), int(y + 2 * t), w, h, t))
        if axes_at_corner:
            n = 30.0 * t
            ox, oy = 10.0 * t, h - 10.0 * t
            lines = [(ox, oy, ox + n, oy), (ox + n, oy, ox + n - 5 * t, oy - 3 * t),
                     (ox + n, oy, ox + n - 5 * t, oy + 3 * t),
                     (ox, oy, ox, oy - n), (ox, oy - n, ox - 3 * t, oy - n + 5 * t),
                     (ox, oy - n, ox + 3 * t, oy - n + 5 * t)]
            a = np.array(lines, dtype=float)
            marks.append(line_pixels(a[:, 0], a[:, 1], a[:, 2], a[:, 3], w, h, t))
            marks.append(_stamp("V", int(ox + n + 3 * t), int(oy - 5 * t), w, h, t))
            marks.append(_stamp("W", int(ox - 2 * t), int(oy - n - 10 * t), w, h, t))
        self.marks = np.concatenate(marks) if marks else np.zeros(0, dtype=np.int64)

    def arrow_top(self, tv, tw):
        """The largest shear on the arrow grid."""
        mag = np.hypot(self.P_seed.dot(tv), self.P_seed.dot(tw))
        return float(mag.max()) if len(mag) else 0.0

    def _arrows(self, tv, tw, length, reference=None):
        """Shear vectors: the longest is `length` x the grid length; with a
        reference that value has that length, so that pictures compare. Returns
        the value of the longest arrow."""
        ax = self.P_seed.dot(tv)
        ay = self.P_seed.dot(tw)
        mag = np.hypot(ax, ay)
        if len(mag) == 0:
            return 0.0
        top = float(mag.max()) if reference is None else float(reference)
        if not top > 0.0:
            return top
        L = mag / top * self.arrow_max_px * float(length)
        on = L >= ARROW_MIN * self.thick
        if not on.any():
            return top
        ux = ax[on] / mag[on]
        uy = -ay[on] / mag[on]          # pixel y points down
        L = L[on]
        x, y = self.seed_x[on], self.seed_y[on]
        tx0, ty0 = x - 0.5 * L * ux, y - 0.5 * L * uy
        tx1, ty1 = x + 0.5 * L * ux, y + 0.5 * L * uy
        h = np.minimum(0.4 * L, 6.0 * self.thick)
        ca, sa = 0.906, 0.423
        hx1 = tx1 - h * (ux * ca - uy * sa)
        hy1 = ty1 - h * (uy * ca + ux * sa)
        hx2 = tx1 - h * (ux * ca + uy * sa)
        hy2 = ty1 - h * (uy * ca - ux * sa)
        idx = line_pixels(np.concatenate([tx0, tx1, tx1]),
                          np.concatenate([ty0, ty1, ty1]),
                          np.concatenate([tx1, hx1, hx2]),
                          np.concatenate([ty1, hy1, hy2]),
                          self.width, self.height, self.thick)
        self.frame[idx] = BLACK
        return top

    def _marker(self, point, kind):
        """Ring for the maximum outside the corners, X for the peak."""
        x, y = self.pixel_of(float(point[0]), float(point[1]))
        w, h, t = self.width, self.height, self.thick
        if kind == "peak":
            r = 7.0 * t
            xs = np.array([x - r, x - r])
            ys = np.array([y - r, y + r])
            xe = np.array([x + r, x + r])
            ye = np.array([y + r, y - r])
            self.frame[line_pixels(xs, ys, xe, ye, w, h, t + 1)] = PEAK
            return
        for r, colour in ((7.0 * t, BLACK), (8.0 * t, WHITE)):
            a = np.linspace(0.0, 2.0 * math.pi, 25)
            sx, sy, ex, ey = _segments(np.column_stack([x + r * np.cos(a),
                                                        y + r * np.sin(a)]))
            self.frame[line_pixels(sx, sy, ex, ey, w, h, t)] = colour

    def paint(self, q, lo, hi, tv=None, tw=None, length=1.0, markers=(),
              reference=None, contours=False):
        """One frame in self.frame from values q on the nodes, colour scale lo
        to hi. tv, tw: shear on the nodes for the arrows (None: none). markers:
        [(point, "peak" or "max")]. contours: lines on the levels of the legend.
        Returns the largest arrow value."""
        v = self.P.dot(q)
        span = max(hi - lo, 1e-30)
        k = (LUT_SIZE - 1.0) / span
        np.subtract(v, lo, out=v)
        contours = contours and span > CONTOUR_FLAT * max(abs(lo), abs(hi))
        if contours:
            self._classes(v, span)
        np.multiply(v, k, out=v)
        np.clip(v, 0.0, LUT_SIZE - 1.0, out=v)
        self._idx[:] = v
        self.lut.take(self._idx, out=self._colour, mode="clip")
        frame = self.frame
        frame[:] = self.template
        frame[self.pix] = self._colour
        if contours:
            self._contours()
        frame[self.outline] = BLACK
        top = 0.0
        if tv is not None:
            top = self._arrows(tv, tw, length, reference)
        if len(self.marks):
            frame[self.marks] = GREY
        for point, kind in markers:
            self._marker(point, kind)
        return top

    def value_at(self, x, y):
        """(node numbers, weights) under pixel (x, y), or None off the
        material."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        k = int(self.index[y * self.width + x])
        if k < 0:
            return None
        return self.nodes_of_pixel[k], self.weights[k]


def upscale(small, width, height, out):
    """Stretch a frame (h, w) of a coarse scene to the buffer `out` (width x
    height), by nearest pixel."""
    rows = np.minimum(np.arange(height) // COARSE, small.shape[0] - 1)
    cols = np.minimum(np.arange(width) // COARSE, small.shape[1] - 1)
    out[:] = small[rows[:, None], cols].ravel()
    return out
