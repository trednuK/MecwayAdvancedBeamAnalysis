# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# make_figures.py
# The drawings of the documentation as hand-made SVG, one per language, and the
# table of messages in both documents. Standard library only.
#
#   python tools/make_figures.py            writes docs/images/fig_*_{nl,en}.svg
#   python tools/make_figures.py --messages also rewrites the table of messages,
#                                           and stops on a code without a text
#
# Every figure has its own background and colours in one <style>, with a dark
# version under prefers-color-scheme. That follows the system of the reader; a
# viewer that ignores it shows the light version, on its own white background.
#
# VIEW OF THE SECTION: as the window, V to the right, W up, U out of the page, so
# looking along -U. Channel as Mecway's C: web at +W, flanges to -W.
#
# SIGNS, from Scripts/section_stress.py, checked on the channel of tools/gui_cap.py:
#   Mw > 0: tension at -V; Mv > 0: tension at +W; T > 0: counter-clockwise here.
#   Vv > 0: web along +V, flange at -V along +W, flange at +V along -W.
#   Vw > 0: both flanges along +W, the web from its ends towards its middle.
#
# NUMBERS: corner table from docs/research/01_vooronderzoek.md par. 5.3; distributed
# load from tests/mecway/results/dist.json (element 5: one element of 1 m); the error
# along the channel from `python scan.py ulong_c4:ulong ZS Z` in tests/validation.

import io
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "docs", "images")

CSS = u"""
text{font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;fill:#1f2933}
.b{font-weight:600}.h{font-weight:600;font-size:15px}.s{font-size:12px}.i{font-style:italic}
.m{fill:#5b6672}.bg{fill:#ffffff}.panel{fill:#f4f6f8;stroke:#d0d7de}
.sec{fill:#dfe5ec;stroke:#1f2933;stroke-width:1.5}.grid{stroke:#d0d7de;stroke-width:1;fill:none}
.zone{fill:#f3d9b1;opacity:.45}
.sfg{stroke:#1f2933}.ffg{fill:#1f2933}.sm{stroke:#5b6672}.fm{fill:#5b6672}
.sf{stroke:#0b5cad}.ff{fill:#0b5cad}.tf{fill:#0b5cad}
.so{stroke:#c2410c}.fo{fill:#c2410c}.to{fill:#c2410c}
.sr{stroke:#c81e1e}.fr{fill:#c81e1e}.tr{fill:#c81e1e}
.sg{stroke:#0f7b5f}.fg{fill:#0f7b5f}.tg{fill:#0f7b5f}
.sp{stroke:#7e3fb2}.fp{fill:#7e3fb2}.tp{fill:#7e3fb2}
@media (prefers-color-scheme:dark){
text{fill:#e6edf3}.m{fill:#9aa7b4}.bg{fill:#0d1117}.panel{fill:#161b22;stroke:#30363d}
.sec{fill:#3a4553;stroke:#e6edf3}.grid{stroke:#30363d}.zone{fill:#7a5a1e}
.sfg{stroke:#e6edf3}.ffg{fill:#e6edf3}.sm{stroke:#9aa7b4}.fm{fill:#9aa7b4}
.sf{stroke:#6cb6ff}.ff{fill:#6cb6ff}.tf{fill:#6cb6ff}
.so{stroke:#ffa657}.fo{fill:#ffa657}.to{fill:#ffa657}
.sr{stroke:#ff7b72}.fr{fill:#ff7b72}.tr{fill:#ff7b72}
.sg{stroke:#3fb950}.fg{fill:#3fb950}.tg{fill:#3fb950}
.sp{stroke:#d2a8ff}.fp{fill:#d2a8ff}.tp{fill:#d2a8ff}
}
"""
TENSION = "#d73027"
COMPRESSION = "#2166ac"
NEUTRAL = "#ffffff"


def esc(text):
    return text.replace(u"&", u"&amp;").replace(u"<", u"&lt;").replace(u">", u"&gt;")


class Svg(object):
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.defs = []
        self.body = [u'<rect class="bg" width="{0}" height="{1}"/>'.format(width, height)]

    def raw(self, text):
        self.body.append(text)

    def line(self, x1, y1, x2, y2, colour="fg", width=1.5, dash=None):
        extra = u' stroke-dasharray="{0}"'.format(dash) if dash else u""
        self.raw(u'<line class="s{0}" x1="{1:.1f}" y1="{2:.1f}" x2="{3:.1f}" y2="{4:.1f}" '
                 u'stroke-width="{5}"{6}/>'.format(colour, x1, y1, x2, y2, width, extra))

    def polyline(self, points, colour="fg", width=2, dash=None):
        extra = u' stroke-dasharray="{0}"'.format(dash) if dash else u""
        self.raw(u'<polyline class="s{0}" fill="none" stroke-width="{1}" stroke-linejoin="round" '
                 u'points="{2}"{3}/>'.format(colour, width, _points(points), extra))

    def polygon(self, points, css):
        self.raw(u'<polygon class="{0}" points="{1}"/>'.format(css, _points(points)))

    def head(self, x, y, angle, colour="fg", size=9):
        """An arrow head with its point at x, y."""
        a, b = angle + math.radians(155), angle - math.radians(155)
        self.polygon([(x, y), (x + size * math.cos(a), y + size * math.sin(a)),
                      (x + size * math.cos(b), y + size * math.sin(b))], u"f" + colour)

    def arrow(self, x1, y1, x2, y2, colour="fg", width=2, size=9, double=False, both=False):
        angle = math.atan2(y2 - y1, x2 - x1)
        cut = size * 0.8 * (2 if double else 1)
        self.line(x1 + (cut if both else 0) * math.cos(angle),
                  y1 + (cut if both else 0) * math.sin(angle),
                  x2 - cut * math.cos(angle), y2 - cut * math.sin(angle), colour, width)
        self.head(x2, y2, angle, colour, size)
        if double:
            d = size * 0.8
            self.head(x2 - d * math.cos(angle), y2 - d * math.sin(angle), angle, colour, size)
        if both:
            self.head(x1, y1, angle + math.pi, colour, size)

    def arc(self, cx, cy, r, start, end, colour="fg", width=2, size=9):
        """Arc in degrees, y up on the page, with a head at the end."""
        step = 1 if end > start else -1
        points = [(cx + r * math.cos(math.radians(a)), cy - r * math.sin(math.radians(a)))
                  for a in range(int(start), int(end) - 8 * step, step)]
        self.polyline(points, colour, width)
        x, y = cx + r * math.cos(math.radians(end)), cy - r * math.sin(math.radians(end))
        tangent = math.radians(end + 90 * step)
        self.head(x, y, math.atan2(-math.sin(tangent), math.cos(tangent)), colour, size)

    def text(self, x, y, text, anchor="start", css=u""):
        extra = u"" if anchor == "start" else u' text-anchor="{0}"'.format(anchor)
        cls = u' class="{0}"'.format(css) if css else u""
        self.raw(u'<text x="{0:.1f}" y="{1:.1f}"{2}{3}>{4}</text>'.format(x, y, extra, cls, esc(text)))

    def circle(self, x, y, r, css):
        self.raw(u'<circle class="{0}" cx="{1:.1f}" cy="{2:.1f}" r="{3}"/>'.format(css, x, y, r))

    def rect(self, x, y, w, h, css, r=0):
        self.raw(u'<rect class="{0}" x="{1:.1f}" y="{2:.1f}" width="{3:.1f}" height="{4:.1f}" '
                 u'rx="{5}"/>'.format(css, x, y, w, h, r))

    def cross(self, x, y, r, colour):
        self.line(x - r, y - r, x + r, y + r, colour, 2.5)
        self.line(x - r, y + r, x + r, y - r, colour, 2.5)

    def point(self, x, y, colour, name, dx=9, dy=-7):
        """A marked point: G or S."""
        self.raw(u'<circle class="f{0}" cx="{1:.1f}" cy="{2:.1f}" r="4"/>'.format(colour, x, y))
        self.text(x + dx, y + dy, name, css=u"b t{0}".format(colour))

    def save(self, name):
        defs = u"<defs>{0}</defs>".format(u"".join(self.defs)) if self.defs else u""
        text = (u'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {0} {1}" width="{0}" '
                u'height="{1}"><style>{2}</style>{3}\n{4}\n</svg>\n').format(
            self.width, self.height, CSS, defs, u"\n".join(self.body))
        with io.open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def _points(points):
    return u" ".join(u"{0:.1f},{1:.1f}".format(x, y) for x, y in points)


def number(value, lang, digits=1):
    text = u"{0:.{1}f}".format(abs(value), digits)
    if lang == "nl":
        text = text.replace(u".", u",")
    return (u"−" if value < 0 else u"") + text


class Plot(object):
    """A plot area on an Svg, linear or logarithmic in x and y."""

    def __init__(self, svg, x, y, w, h, xr, yr, logx=False, logy=False):
        self.svg, self.x, self.y, self.w, self.h = svg, x, y, w, h
        self.xr, self.yr, self.logx, self.logy = xr, yr, logx, logy

    def _t(self, v, r, log):
        if log:
            return (math.log10(v) - math.log10(r[0])) / (math.log10(r[1]) - math.log10(r[0]))
        return (v - r[0]) / float(r[1] - r[0])

    def px(self, v):
        return self.x + self.w * self._t(v, self.xr, self.logx)

    def py(self, v):
        return self.y + self.h * (1.0 - self._t(v, self.yr, self.logy))

    def line(self, points, colour, width=2.5, dash=None):
        self.svg.polyline([(self.px(a), self.py(b)) for a, b in points], colour, width, dash)

    def frame(self, xticks, yticks, xlabel=u"", ylabel=u""):
        s = self.svg
        for label, v in yticks:
            s.line(self.x, self.py(v), self.x + self.w, self.py(v), "m", 0.5)
            s.text(self.x - 6, self.py(v) + 4, label, "end", u"s m")
        for label, v in xticks:
            s.line(self.px(v), self.y + self.h, self.px(v), self.y + self.h + 4, "m", 1)
            s.text(self.px(v), self.y + self.h + 17, label, "middle", u"s m")
        s.line(self.x, self.y, self.x, self.y + self.h, "m", 1)
        s.line(self.x, self.y + self.h, self.x + self.w, self.y + self.h, "m", 1)
        if xlabel:
            s.text(self.x + self.w / 2.0, self.y + self.h + 34, xlabel, "middle", u"s")
        if ylabel:
            s.text(self.x - 6, self.y - 10, ylabel, "start", u"s")


def channel(height, width, web, flange):
    """Outline of the channel about its centroid in V, W: web at +W, flanges to -W.
    height along V, width along W incl. the web. Returns (points, top, bottom)."""
    a_web = height * web
    a_flange = (width - web) * flange
    top = (a_web * web / 2.0 + 2 * a_flange * (web + (width - web) / 2.0)) / (a_web + 2 * a_flange)
    bottom = top - width
    v = height / 2.0
    points = [(-v, top), (v, top), (v, bottom), (v - flange, bottom), (v - flange, top - web),
              (-v + flange, top - web), (-v + flange, bottom), (-v, bottom)]
    return points, top, bottom


def place(points, x, y, k):
    return [(x + k * p[0], y - k * p[1]) for p in points]


def axes_icon(s, x, y, length=26):
    """V to the right, W up, U out of the page."""
    s.arrow(x, y, x + length, y, "m", 1.5, 7)
    s.arrow(x, y, x, y - length, "m", 1.5, 7)
    s.circle(x, y, 3.5, "bg sfg")
    s.circle(x, y, 1.2, "fm")
    s.text(x + length + 3, y + 4, u"V", css=u"s m")
    s.text(x - 3, y - length - 3, u"W", "end", u"s m")
    s.text(x - 7, y + 15, u"U", "middle", u"s m")


# ------------------------------------------------------------------- (a) axes
def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(sum(c * c for c in a))
    return tuple(c / n for c in a)


class Iso(object):
    """Orthographic view from `eye`, W up, in right-handed (U, V, W)."""

    def __init__(self, x, y, eye=(0.62, -0.62, 0.48), k=1.0):
        d = _unit(eye)
        self.r = _unit(_cross((0, 0, 1), d))
        self.u = _cross(d, self.r)
        self.x, self.y, self.k = x, y, k

    def __call__(self, p):
        return (self.x + self.k * sum(a * b for a, b in zip(p, self.r)),
                self.y - self.k * sum(a * b for a, b in zip(p, self.u)))


def _beam(s, view, a=34.0, b=50.0, length=170.0):
    """A piece of beam on the -U side of the cut, with the cut face at U = 0."""
    top = [(0, -a, b), (0, a, b), (-length, a, b), (-length, -a, b)]
    side = [(0, -a, b), (0, -a, -b), (-length, -a, -b), (-length, -a, b)]
    cut = [(0, -a, b), (0, a, b), (0, a, -b), (0, -a, -b)]
    s.polygon([view(p) for p in top], "sec")
    s.polygon([view(p) for p in side], "sec")
    s.raw(u'<polygon class="sec" style="opacity:.55" points="{0}"/>'.format(
        _points([view(p) for p in cut])))
    for k in range(9):           # clamp at the far end
        w = -b + (2 * b) * k / 8.0
        p = view((-length, -a, w))
        s.line(p[0] - 11, p[1] + 7, p[0], p[1], "m", 1)


def _spin(s, view, axis, at, colour, r=15.0):
    """Right-handed turn about an axis (0, 1, 2 = U, V, W) at distance `at` from G."""
    i, j = (axis + 1) % 3, (axis + 2) % 3
    points = []
    for deg in range(20, 310, 6):
        p = [0.0, 0.0, 0.0]
        p[axis] = at
        p[i] = r * math.cos(math.radians(deg))
        p[j] = r * math.sin(math.radians(deg))
        points.append(view(p))
    s.polyline(points[:-1], colour, 2)
    x0, y0 = points[-2]
    x1, y1 = points[-1]
    s.head(x1, y1, math.atan2(y1 - y0, x1 - x0), colour, 8)


def fig_axes(lang):
    t = {"nl": (u"Krachten op het +U-vlak", u"Momenten op het +U-vlak",
                u"rechterhandregel: duim langs de pijl, vingers geven de draaizin",
                u"Getekend: het deel aan de −U-kant van de snede. De pijlen tonen wat het "
                u"weggesneden deel aan de +U-kant erop uitoefent.",
                u"inklemming"),
         "en": (u"Forces on the +U face", u"Moments on the +U face",
                u"right-hand rule: thumb along the arrow, fingers give the sense of rotation",
                u"Drawn: the part on the −U side of the cut. The arrows show what the part "
                u"removed on the +U side exerts on it.",
                u"clamped end")}[lang]
    s = Svg(960, 400)
    length = 118.0
    for k, x in enumerate((245, 725)):
        view = Iso(x, 205)
        moment = k == 1
        s.text(x - 215, 28, t[k], css=u"h")
        _beam(s, view)
        far = view((-170, -34, -50))
        s.text(far[0] - 10, far[1] + 24, t[4], "middle", u"s m")
        colour = "o" if moment else "f"
        ends = [view((length * 1.2, 0, 0)), view((0, length, 0)), view((0, 0, length * 0.95))]
        names = ((u"T", u"Mv", u"Mw") if moment else (u"N", u"Vv", u"Vw"))
        axes = (u"U", u"V", u"W")
        g = view((0, 0, 0))
        s.circle(g[0], g[1], 3.5, "ffg")
        for i, end in enumerate(ends):
            s.arrow(g[0], g[1], end[0], end[1], colour, 2.5, 10, double=moment)
            if moment:
                _spin(s, view, i, (0.72, 0.72, 0.68)[i] * length * (1.2 if i == 0 else 1.0), "o")
            dx, dy = ((14, 10), (12, -2), (-12, -8))[i]
            anchor = "end" if i == 2 else "start"
            s.text(end[0] + dx, end[1] + dy, u"+{0}:  {1}".format(axes[i], names[i]), anchor,
                   u"b t{0}".format(colour))
    s.text(480, 318, t[3], "middle", u"s")
    s.text(480, 338, t[2], "middle", u"s m")
    gap = u"  "
    s.text(480, 374, gap.join((u"N = ∫ σ dA", u"Vv = ∫ τ_uv dA",
                               u"Vw = ∫ τ_uw dA",
                               u"T = ∫ (v τ_uw − w τ_uv) dA",
                               u"Mv = ∫ w σ dA", u"Mw = −∫ v σ dA")),
           "middle")
    s.save("fig_axes_{0}.svg".format(lang))


# ---------------------------------------------------------- (b) stress by force
def _flows(s, items, colour="g", size=7):
    for x1, y1, x2, y2 in items:
        s.arrow(x1, y1, x2, y2, colour, 1.8, size)


def _gradient(s, name, x1, y1, x2, y2, opacity=1.0):
    s.defs.append(
        u'<linearGradient id="{0}" gradientUnits="userSpaceOnUse" x1="{1:.1f}" y1="{2:.1f}" '
        u'x2="{3:.1f}" y2="{4:.1f}"><stop offset="0" stop-color="{5}"/>'
        u'<stop offset=".5" stop-color="{6}"/><stop offset="1" stop-color="{7}"/>'
        u'</linearGradient>'.format(name, x1, y1, x2, y2, TENSION, NEUTRAL, COMPRESSION))


def fig_stress(lang):
    t = {"nl": dict(tension=u"trek", comp=u"druk", neutral=u"neutrale lijn",
                    tau=u"τ, voor V door S", tt=u"τ, om S",
                    note=u"Vlak bekeken van de +U-kant. Pijllengte alleen als aanduiding."),
         "en": dict(tension=u"tension", comp=u"compression", neutral=u"neutral line",
                    tau=u"τ, for V through S", tt=u"τ, about S",
                    note=u"Face seen from the +U side. Arrow length is indicative only.")}[lang]
    pw, ph = 310, 232
    s = Svg(3 * pw + 40, 2 * ph + 64)
    k = 1.15
    outline, top, bottom = channel(160.0, 74.0, 18.0, 18.0)
    h = 160.0
    titles = ((u"N > 0", u"σ = N/A"), (u"Mv > 0", u"σ = Mv·w/Ivv"),
              (u"Mw > 0", u"σ = −Mw·v/Iww"),
              (u"Vv > 0", t["tau"]), (u"Vw > 0", t["tau"]), (u"T_S > 0", t["tt"]))
    for i in range(6):
        px = 20 + (i % 3) * pw
        py = 14 + (i // 3) * ph
        s.rect(px, py, pw - 10, ph - 10, "panel", 6)
        s.text(px + 12, py + 22, titles[i][0], css=u"h")
        s.text(px + 80, py + 22, titles[i][1], css=u"s m")
        x0 = px + (pw - 10) / 2.0
        y0 = py + 112
        pts = place(outline, x0, y0, k)
        yt, yb = y0 - k * top, y0 - k * bottom
        xl, xr = x0 - k * h / 2, x0 + k * h / 2
        wall = k * 18.0
        axes_icon(s, px + 26, py + ph - 26, 22)
        if i == 0:
            s.raw(u'<polygon points="{0}" fill="{1}" fill-opacity=".8"/>'.format(
                _points(pts), TENSION))
            s.polygon(pts, "sfg grid")
            s.text(x0, yt + wall - 5, t["tension"], "middle", u"s b")
        elif i == 1:
            r = k * max(abs(top), abs(bottom))
            _gradient(s, "mv", x0, y0 - r, x0, y0 + r)
            s.raw(u'<polygon points="{0}" fill="url(#mv)"/>'.format(_points(pts)))
            s.polygon(pts, "sfg grid")
            s.line(xl - 16, y0, xr + 16, y0, "m", 1.2, "6 4")
            s.text(x0, y0 + 16, t["neutral"], "middle", u"s m")
            s.text(x0, yt - 7, t["tension"] + u" (+W)", "middle", u"s b")
            s.text(xl + wall / 2.0, yb + 16, t["comp"], "middle", u"s b")
        elif i == 2:
            _gradient(s, "mw", xl, y0, xr, y0)
            s.raw(u'<polygon points="{0}" fill="url(#mw)"/>'.format(_points(pts)))
            s.polygon(pts, "sfg grid")
            s.line(x0, yt - 22, x0, yb, "m", 1.2, "6 4")
            s.text(x0, yt - 26, t["neutral"], "middle", u"s m")
            s.text(xl, yb + 16, t["tension"] + u" (−V)", "start", u"s b")
            s.text(xr, yb + 16, t["comp"] + u" (+V)", "end", u"s b")
        else:
            s.polygon(pts, "sec")
        ym = yt + wall / 2.0
        xa, xb = xl + wall / 2.0, xr - wall / 2.0
        sc = (x0, yt - 33)              # the shear centre, schematic
        if i == 3:      # Vv: along the web, up the flange at -V, down the flange at +V
            _flows(s, [(xl + 26 + 42 * j, ym, xl + 58 + 42 * j, ym) for j in range(3)])
            _flows(s, [(xa, yb - 6 - 26 * j, xa, yb - 22 - 26 * j) for j in range(2)])
            _flows(s, [(xb, yt + wall + 14 + 26 * j, xb, yt + wall + 30 + 26 * j) for j in range(2)])
            s.arrow(sc[0] - 34, sc[1], sc[0] + 40, sc[1], "f", 2.5, 10)
            s.text(sc[0] + 46, sc[1] + 4, u"Vv", css=u"b tf")
            s.point(sc[0], sc[1], "p", u"S", -16, -6)
        if i == 4:      # Vw: up both flanges, then along the web to its middle
            for x in (xa, xb):
                _flows(s, [(x, yb - 6 - 22 * j, x, yb - 24 - 22 * j) for j in range(3)])
            _flows(s, [(xl + 28, ym, xl + 54, ym), (xl + 62, ym, xl + 84, ym)])
            _flows(s, [(xr - 28, ym, xr - 54, ym), (xr - 62, ym, xr - 84, ym)])
            s.arrow(sc[0], sc[1] + 16, sc[0], sc[1] - 26, "f", 2.5, 10)
            s.text(sc[0] + 8, sc[1] - 16, u"Vw", css=u"b tf")
            s.point(sc[0], sc[1], "p", u"S", -16, 4)
        if i == 5:      # T: counter-clockwise along every wall, both faces
            e = wall * 0.27
            xs = [xl + 34 + 38 * j for j in range(3)]
            _flows(s, [(x + 18, yt + e, x - 8, yt + e) for x in xs], "g", 6)
            _flows(s, [(x - 8, yt + wall - e, x + 18, yt + wall - e) for x in xs], "g", 6)
            for x in (xa, xb):      # around each flange: down on its -V edge, up on +V
                for j in range(2):
                    y = yb - 12 - 24 * j
                    _flows(s, [(x - e, y - 8, x - e, y + 10)], "g", 6)
                    _flows(s, [(x + e, y + 10, x + e, y - 8)], "g", 6)
            s.arc(sc[0], sc[1], 15, 200, 500, "o", 2.2, 8)
            s.text(sc[0] + 22, sc[1] + 2, u"T_S", css=u"b to")
            s.point(sc[0], sc[1], "p", u"S", -24, 12)
    s.text(s.width / 2.0, s.height - 14, t["note"], "middle", u"s m")
    s.save("fig_stress_{0}.svg".format(lang))


# --------------------------------------------------------------- (c) shear centre
def fig_shear_centre(lang):
    t = {"nl": dict(a=u"Wat de solver geeft", b=u"Wat de tool gebruikt", same=u"is hetzelfde",
                    line=u"lijn van de last", g=u"G: zwaartepunt, de balk-as.",
                    s=u"S: schuifcentrum.", v=u"Vector S → G: EY, EZ."),
         "en": dict(a=u"What the solver gives", b=u"What the tool uses", same=u"is the same as",
                    line=u"line of the load", g=u"G: centroid, the beam line.",
                    s=u"S: shear centre.", v=u"Vector S → G: EY, EZ.")}[lang]
    s = Svg(960, 400)
    k = 1.5
    outline, top, bottom = channel(160.0, 60.0, 6.0, 8.0)
    ez = -35.33
    for i, x0 in enumerate((245, 715)):
        y0 = 172
        s.text(x0, 28, t["a"] if i == 0 else t["b"], "middle", u"h")
        s.polygon(place(outline, x0, y0, k), "sec")
        gy, sy = y0, y0 + k * ez
        # the line of the load, along V
        y = gy if i == 0 else sy
        s.line(x0 - 215, y, x0 + 190, y, "f", 1, "7 5")
        s.text(x0 - 215, y - 6, t["line"], css=u"s tf")
        s.arrow(x0, y, x0 + 96, y, "f", 3, 12)
        s.text(x0 + 74, y + 20, u"Vv", css=u"b tf")
        s.arc(x0, y, 21, 150, 410, "o", 2.5, 10)
        s.text(x0 - 26, y + 26 if i == 0 else y - 20, u"T" if i == 0 else u"T_S", "end", u"b to")
        s.point(x0, gy, "fg", u"G", 26, 18)
        s.point(x0, sy, "p", u"S", 26 if i else -20, -8)
        if i == 0:
            s.arrow(x0 + 56, sy, x0 + 56, gy - 3, "p", 2, 9)
            s.text(x0 + 64, sy + 15, u"EZ < 0", css=u"s b tp")
    s.text(480, 170, u"=", "middle", u"h")
    s.text(480, 188, t["same"], "middle", u"s m")
    s.text(40, 322, u" ".join((t["g"], t["s"], t["v"])), css=u"s m")
    s.text(40, 354, u"T_S = T + EY·Vw − EZ·Vv", css=u"h")
    if lang == "nl":
        example = u"Voorbeeld van het venster: EY = 0, EZ = −35,3 mm, Vv = 2000 N, T = 20 N·m"
    else:
        example = u"Example of the window: EY = 0, EZ = −35.3 mm, Vv = 2000 N, T = 20 N·m"
    s.text(40, 380, u"{0}:  T_S = 20 + 70{1}7 = 90{1}7 N·m".format(
        example, u"," if lang == "nl" else u"."), css=u"s")
    s.save("fig_shear_centre_{0}.svg".format(lang))


# ---------------------------------------------------------------- (d) corner zone
CORNER = ((2047, 1.76, 1.035), (3832, 2.09, 1.044), (13413, 2.52, 1.050), (50402, 3.26, 1.054))


def fig_corner(lang):
    t = {"nl": dict(title=u"Rond een inspringende hoek", plot=u"Wringing, I-profiel zonder afronding",
                    zone=u"zone: straal r = 1 t", peak=u"✕ piek in de hoek",
                    outside=u"○ maximum buiten de hoek", x=u"elementen in de doorsnede",
                    y=u"τmax / (T·t/J)", all_=u"hele doorsnede: groeit met het mesh",
                    out=u"buiten r = 1 t: ongeveer 2 % verschil",
                    t=u"t: afstand tot de dichtstbijzijnde rand die niet aan de hoek grenst"),
         "en": dict(title=u"Around a re-entrant corner", plot=u"Torsion, I section without fillets",
                    zone=u"zone: radius r = 1 t", peak=u"✕ peak in the corner",
                    outside=u"○ maximum outside the corner", x=u"elements in the section",
                    y=u"τmax / (T·t/J)", all_=u"whole section: grows with the mesh",
                    out=u"outside r = 1 t: about 2 % difference",
                    t=u"t: distance to the nearest edge that does not touch the corner")}[lang]
    s = Svg(960, 400)
    s.text(40, 28, t["title"], css=u"h")
    cx, cy = 210, 170        # the corner
    wall = 92
    material = [(50, cy - wall), (420, cy - wall), (420, cy), (cx, cy), (cx, 345), (cx - wall - 40, 345),
                (cx - wall - 40, cy - wall)]
    material[0] = (cx - wall - 40, cy - wall)
    material = material[:-1]
    s.defs.append(u'<clipPath id="m"><polygon points="{0}"/></clipPath>'.format(_points(material)))
    s.defs.append(u'<radialGradient id="heat" gradientUnits="userSpaceOnUse" cx="{0}" cy="{1}" r="{2}">'
                  u'<stop offset="0" stop-color="{3}"/><stop offset=".35" stop-color="{3}" '
                  u'stop-opacity=".55"/><stop offset="1" stop-color="{3}" stop-opacity="0"/>'
                  u'</radialGradient>'.format(cx, cy, wall * 1.15, TENSION))
    s.polygon(material, "sec")
    s.raw(u'<circle cx="{0}" cy="{1}" r="{2}" fill="url(#heat)" clip-path="url(#m)"/>'.format(
        cx, cy, wall * 1.15))
    s.raw(u'<circle class="so" cx="{0}" cy="{1}" r="{2}" fill="none" stroke-width="2" '
          u'stroke-dasharray="7 5" clip-path="url(#m)"/>'.format(cx, cy, wall))
    s.polygon(material, "grid sfg")
    s.cross(cx, cy, 7, "o")
    s.line(cx + 30, cy + 30, cx + 8, cy + 8, "o", 1)
    s.text(cx + 34, cy + 40, t["peak"], css=u"b to")
    px, py = cx + wall + 16, cy - wall * 0.5
    s.circle(px, py, 8, "bg sf")
    s.raw(u'<circle class="sf" cx="{0}" cy="{1}" r="8" fill="none" stroke-width="2.5"/>'.format(px, py))
    s.line(px + 10, py + 10, px + 24, py + 56, "f", 1)
    s.text(cx + 34, cy + 66, t["outside"], css=u"b tf")
    s.arrow(cx, cy, cx, cy - wall, "fg", 1.5, 8, both=True)
    s.text(cx + 8, cy - wall * 0.4, u"t", css=u"b")
    s.text(cx + 34, cy + 92, t["zone"], css=u"s to")
    s.text(40, 372, t["t"], css=u"s m")
    s.text(610, 28, t["plot"], "middle", u"h")
    p = Plot(s, 520, 62, 380, 250, (1000, 100000), (0, 3.5), logx=True)
    p.frame([(u"1 000", 1000), (u"10 000", 10000), (u"100 000", 100000)],
            [(number(v, lang), v) for v in (0, 1, 2, 3)], t["x"], t["y"])
    p.line([(a[0], a[1]) for a in CORNER], "o")
    p.line([(a[0], a[2]) for a in CORNER], "f")
    for row in CORNER:
        s.cross(p.px(row[0]), p.py(row[1]), 4, "o")
        s.text(p.px(row[0]), p.py(row[1]) - 10, number(row[1], lang, 2), "middle", u"s to")
        s.raw(u'<circle class="bg sf" cx="{0:.1f}" cy="{1:.1f}" r="4" stroke-width="2"/>'.format(
            p.px(row[0]), p.py(row[2])))
        s.text(p.px(row[0]), p.py(row[2]) + 19, number(row[2], lang, 3), "middle", u"s tf")
    s.text(p.px(1500), p.py(3.05), t["all_"], css=u"s to")
    s.text(p.px(1500), p.py(0.28), t["out"], css=u"s tf")
    s.save("fig_corner_{0}.svg".format(lang))


# ----------------------------------------------------------- (e) distributed load
def fig_distributed(lang):
    t = {"nl": dict(load=u"q_v = −1000 N/m", el=u"één element, h = 1 m",
                    exact=u"echt verloop", mecway=u"Mecway: rechte lijn", mid=u"midden: −500 N",
                    const=(u"Mecway: constant,", u"de waarde in het midden"),
                    tool=u"De tool telt het verschil er weer bij op."),
         "en": dict(load=u"q_v = −1000 N/m", el=u"one element, h = 1 m",
                    exact=u"true course", mecway=u"Mecway: straight line", mid=u"middle: −500 N",
                    const=(u"Mecway: constant,", u"the value in the middle"),
                    tool=u"The tool adds the difference back.")}[lang]
    s = Svg(960, 462)
    # the beam
    x0, x1, y = 300, 660, 82
    s.line(x0, y, x1, y, "fg", 5)
    for k in range(7):
        s.line(x0, y - 24 + 8 * k, x0 - 10, y - 16 + 8 * k, "m", 1)
    s.line(x0, y - 26, x0, y + 26, "fg", 2)
    for k in range(9):
        x = x0 + (x1 - x0) * k / 8.0
        s.arrow(x, y - 44, x, y - 5, "f", 1.5, 7)
    s.line(x0, y - 44, x1, y - 44, "f", 1.5)
    s.text(x1 + 12, y - 36, t["load"], css=u"b tf")
    s.text(x0 - 22, y + 4, u"s = 0", "end", u"s m")
    s.text(x1 + 12, y + 5, u"s = h", css=u"s m")
    s.text((x0 + x1) / 2.0, y + 24, t["el"], "middle", u"s m")
    axes = (x1 + 150, y + 14)
    s.arrow(axes[0], axes[1], axes[0] + 30, axes[1], "m", 1.5, 7)
    s.arrow(axes[0], axes[1], axes[0], axes[1] - 30, "m", 1.5, 7)
    s.text(axes[0] + 33, axes[1] + 4, u"U", css=u"s m")
    s.text(axes[0] - 4, axes[1] - 32, u"V", "end", u"s m")
    # M
    p = Plot(s, 90, 150, 350, 240, (0, 1), (-520, 120))
    p.frame([(u"0", 0), (u"h/2", 0.5), (u"h", 1)],
            [(number(v, lang, 0), v) for v in (-400, -200, 0)], u"s", u"Mw (N·m)")
    s.line(p.x, p.py(0), p.x + p.w, p.py(0), "m", 1)
    exact = [(a / 50.0, -500.0 * (1 - a / 50.0) ** 2) for a in range(51)]
    p.line(exact, "g", 3)
    p.line([(0, -416.67), (1, 83.33)], "o", 3)
    s.arrow(p.px(0) + 16, p.py(-500), p.px(0) + 16, p.py(-416.67), "p", 1.8, 7, both=True)
    s.text(p.px(0) + 24, p.py(-468), u"q·h²/12 = 83{0}3 N·m".format(
        u"," if lang == "nl" else u"."), css=u"s b tp")
    s.arrow(p.px(1) - 10, p.py(0), p.px(1) - 10, p.py(83.33), "p", 1.8, 7, both=True)
    s.text(p.px(1) - 16, p.py(92), u"q·h²/12", "end", u"s b tp")
    s.text(p.px(0.56), p.py(-235), t["mecway"], css=u"s b to")
    s.text(p.px(0.07), p.py(-190), t["exact"], css=u"s b tg")
    s.text(p.px(0.45), p.py(-470), u"s = 0:", css=u"s m")
    s.text(p.px(0.45) + 42, p.py(-470), u"−500", css=u"s b tg")
    s.text(p.px(0.45) + 84, p.py(-470), number(-416.67, lang, 2), css=u"s b to")
    # V
    p = Plot(s, 550, 150, 350, 240, (0, 1), (-1050, 120))
    p.frame([(u"0", 0), (u"h/2", 0.5), (u"h", 1)],
            [(number(v, lang, 0), v) for v in (-1000, -500, 0)], u"s", u"Vv (N)")
    s.line(p.x, p.py(0), p.x + p.w, p.py(0), "m", 1)
    p.line([(0, -1000), (1, 0)], "g", 3)
    p.line([(0, -500), (1, -500)], "o", 3)
    s.circle(p.px(0.5), p.py(-500), 5, "fo")
    s.text(p.px(0.5) + 10, p.py(-500) + 19, t["mid"], css=u"s to")
    s.text(p.px(0.03), p.py(-500) - 25, t["const"][0], css=u"s b to")
    s.text(p.px(0.03), p.py(-500) - 9, t["const"][1], css=u"s b to")
    s.text(p.px(0.62), p.py(-155), t["exact"], css=u"s b tg")
    s.text(480, 444, t["tool"], "middle", u"h")
    s.save("fig_distributed_{0}.svg".format(lang))


# ----------------------------------------------------------------- (f) data flow
def _box(s, x, y, w, h, title, lines, colour):
    s.rect(x, y, w, h, "panel", 8)
    s.text(x + 12, y + 22, title, css=u"h t{0}".format(colour))
    for i, line in enumerate(lines):
        s.text(x + 12, y + 44 + 19 * i, line, css=u"s")


def fig_dataflow(lang):
    t = {"nl": dict(
        mecway=u"Mecway", api=u"Huidige oplossing (API)", file=u"Opgeslagen model (.liml)",
        window=u"Venster", wsub=u"IronPython, in Mecway", worker=u"Rekenproces",
        wosub=u"CPython 3, numpy, scipy",
        api_l=(u"selectie, element- en knoopnummers", u"snedekrachten per elementeinde",
               u"anders knoopwaarden", u"anders verplaatsing en rotatie"),
        file_l=(u"oriëntatie, materialen, lasten", u"SECTIE-VORM", u"#ASTER-SECTIE"),
        win_l=(u"lijsten, schuifbalk, vinkjes", u"vergelijkt bestand en API", u"tekent het beeld"),
        wo_l=(u"1. leest het model, bouwt de keten", u"2. per doorsnede: mesh, ω, ψ, φ,",
              u" eenheidsvelden (één keer)", u"3. krachten op plaats s, correctie",
              u" verdeelde last, T_S", u"4. optellen, kleuren, beeld maken"),
        a=u"krachten, nummers", b=u"pad van het bestand", c=u"verzoek: alleen het nieuwste",
        d=u"beeld en getallen", e=u"optie: tijdelijk element voor de positie",
        sol=u"Oplosser: Mecway intern, CCX, Mystran, of Code-Aster via de bridge"),
        "en": dict(
        mecway=u"Mecway", api=u"Current solution (API)", file=u"Saved model (.liml)",
        window=u"Window", wsub=u"IronPython, inside Mecway", worker=u"Calculation process",
        wosub=u"CPython 3, numpy, scipy",
        api_l=(u"selection, element and node numbers", u"section forces per element end",
               u"else node values", u"else displacement and rotation"),
        file_l=(u"orientation, materials, loads", u"SECTIE-VORM", u"#ASTER-SECTIE"),
        win_l=(u"lists, slider, check boxes", u"compares file and API", u"draws the picture"),
        wo_l=(u"1. reads the model, builds the chain", u"2. per section: mesh, ω, ψ, φ,",
              u" unit fields (once)", u"3. forces at position s, correction",
              u" distributed load, T_S", u"4. add up, colour, make the picture"),
        a=u"forces, numbers", b=u"path of the file", c=u"request: only the newest",
        d=u"picture and numbers", e=u"option: temporary element for the position",
        sol=u"Solver: Mecway internal, CCX, Mystran, or Code-Aster through the bridge")}[lang]
    s = Svg(960, 380)
    s.rect(14, 14, 300, 336, "grid", 10)
    s.text(30, 38, t["mecway"], css=u"h")
    _box(s, 30, 52, 268, 124, t["api"], t["api_l"], "f")
    _box(s, 30, 212, 268, 106, t["file"], t["file_l"], "o")
    s.text(30, 340, t["sol"], css=u"s m")
    _box(s, 436, 58, 214, 118, t["window"], (t["wsub"],) + t["win_l"], "g")
    _box(s, 684, 150, 262, 168, t["worker"], (t["wosub"],) + t["wo_l"], "p")
    s.arrow(298, 96, 436, 96, "f", 2.5, 10)
    s.text(367, 88, t["a"], "middle", u"s tf")
    s.arrow(436, 142, 298, 142, "m", 1.5, 8)
    s.text(306, 196, t["e"], css=u"s m")
    s.arrow(298, 264, 684, 264, "o", 2.5, 10)
    s.text(490, 256, t["b"], "middle", u"s to")
    # window <-> worker
    s.line(650, 112, 780, 112, "g", 2.5)
    s.arrow(780, 112, 780, 150, "g", 2.5, 10)
    s.text(662, 102, t["c"], css=u"s tg")
    s.polyline([(860, 150), (860, 34), (543, 34)], "p", 2.5)
    s.arrow(543, 34, 543, 58, "p", 2.5, 10)
    s.text(700, 26, t["d"], css=u"s tp")
    s.save("fig_dataflow_{0}.svg".format(lang))


# ---------------------------------------------------------------- (g) warping zone
# x/h from the clamp; sigma and tau error in %, load through S, then through G.
# tests/validation: scan.py on ulong_c4, L = 28.6 h, h = 100 mm, 1/k = 2.38 h.
SCAN_X = (2.3, 4.3, 6.3, 8.3, 10.3, 12.3, 13.3, 14.3, 15.3, 16.3, 18.3, 20.3, 22.3, 24.3, 26.3)
SCAN_S = ((0.155, 0.079, 0.037, 0.017, 0.007, None, 0.004, 0.009, 0.017, 0.030, 0.084, 0.245,
           0.750, 2.513, 8.868),
          (2.71, 1.48, 0.78, 0.48, 0.36, 0.33, 0.34, 0.36, 0.41, 0.49, 0.81, 1.56, 3.26, 7.03, 12.14))
SCAN_G = ((13.023, 6.403, 3.069, 1.464, 0.703, 0.344, 0.245, 0.180, 0.139, 0.120, 0.145, 0.318,
           0.915, 3.022, 10.589),
          (67.50, 21.35, 8.21, 3.44, 3.42, 3.50, 3.53, 3.54, 3.55, 3.56, 3.57, 3.59, 3.63, 3.72, 3.85))
SCAN_L = 28.6
ONE_K = 2.38


def fig_warping(lang):
    t = {"nl": dict(clamp=u"inklemming", load=u"belast einde", x=u"plaats x / h",
                    sig=u"fout in σ (%)", tau=u"fout in τ (%)",
                    s=u"last door S", g=u"last door G", m=u"marge",
                    zone=u"4/k van het einde: hier nog niet geldig"),
         "en": dict(clamp=u"clamped end", load=u"loaded end", x=u"position x / h",
                    sig=u"error in σ (%)", tau=u"error in τ (%)",
                    s=u"load through S", g=u"load through G", m=u"margin",
                    zone=u"4/k from the end: not valid here yet")}[lang]
    s = Svg(960, 560)
    s.line(60, 22, 96, 22, "f", 2.5)
    s.text(102, 26, t["s"], css=u"s")
    s.line(230, 22, 266, 22, "o", 2.5, "7 4")
    s.text(272, 26, t["g"], css=u"s")
    plots = (Plot(s, 80, 58, 830, 190, (0, SCAN_L), (0.002, 30), logy=True),
             Plot(s, 80, 294, 830, 190, (0, SCAN_L), (0.1, 100), logy=True))
    ticks = ((u"0,01", 0.01), (u"0,1", 0.1), (u"1", 1), (u"10", 10)), \
            ((u"0,1", 0.1), (u"1", 1), (u"10", 10), (u"100", 100))
    margins = (1.0, 5.0)
    names = (t["sig"], t["tau"])
    for i, p in enumerate(plots):
        zone = 4 * ONE_K
        s.rect(p.px(0), p.y, p.px(zone) - p.px(0), p.h, "zone")
        s.rect(p.px(SCAN_L - zone), p.y, p.px(SCAN_L) - p.px(SCAN_L - zone), p.h, "zone")
        marks = [(ONE_K, u"1/k"), (zone, u"4/k"), (SCAN_L - zone, u"4/k"), (SCAN_L - ONE_K, u"1/k")]
        for x, name in marks:
            s.line(p.px(x), p.y, p.px(x), p.y + p.h, "m", 1, "4 3")
            s.text(p.px(x), p.y - 4, name, "middle", u"s m")
        labels = [(label.replace(u",", u".") if lang == "en" else label, v) for label, v in ticks[i]]
        xticks = [(u"{0}".format(v), v) for v in (0, 5, 10, 15, 20, 25)] if i == 1 else []
        p.frame(xticks, labels, t["x"] if i == 1 else u"", u"")
        s.text(p.x + p.w / 2.0, p.y + 16, names[i], "middle", u"s b")
        s.line(p.x, p.py(margins[i]), p.x + p.w, p.py(margins[i]), "r", 1.2, "2 3")
        s.text(p.x + p.w / 2.0, p.py(margins[i]) - 5, u"{0} {1} %".format(t["m"], margins[i]
               if margins[i] != int(margins[i]) else int(margins[i])), "middle", u"s tr")
        for data, colour, dash in ((SCAN_S[i], "f", None), (SCAN_G[i], "o", "7 4")):
            p.line([(x, v) for x, v in zip(SCAN_X, data) if v is not None], colour, 2.5, dash)
            for x, v in zip(SCAN_X, data):
                if v is not None:
                    s.circle(p.px(x), p.py(v), 3, u"f" + colour)
    p = plots[1]
    s.text(p.px(0), p.y + p.h + 34, t["clamp"], css=u"s b")
    s.text(p.px(SCAN_L), p.y + p.h + 34, t["load"], "end", u"s b")
    s.text(480, 546, t["zone"], "middle", u"s m")
    s.save("fig_warping_{0}.svg".format(lang))


# ------------------------------------------------------------ (h) 3D magnification
def fig_magnify(lang):
    """The same chain twice in the 3D view: sections to scale, and magnified about
    their centroid G, which stays at its true position s. Viewer on the +U side, as
    the default camera of the window, so V is to the right and W up. The beam line
    is dashed here; the window draws it as a thin line."""
    t = {"nl": dict(true=u"Op ware schaal: 1×", big=u"Doorsneden 3× vergroot",
                    chain=u"gestippeld: de balk-as, op ware schaal",
                    g=u"G op zijn plaats s",
                    l1=u"Elke doorsnede wordt k× vergroot om zijn zwaartepunt G; de plaatsen s "
                       u"blijven op ware afstand.",
                    l2=u"De muis geeft altijd de ware V, W en spanning. Het venster zegt "
                       u"“doorsneden k× vergroot, niet op schaal”."),
         "en": dict(true=u"To scale: 1×", big=u"Sections magnified 3×",
                    chain=u"dashed: the beam line, to scale",
                    g=u"G at its position s",
                    l1=u"Each section is magnified k× about its centroid G; the positions s "
                       u"stay at their true distance.",
                    l2=u"The mouse always gives the true V, W and stress. The window says "
                       u"“sections k× magnified, not to scale”.")}[lang]
    s = Svg(960, 380)
    length = 360.0
    # walls thicker than the example, so that the shape reads at this size
    outline, _, _ = channel(160.0, 60.0, 14.0, 12.0)
    to_scale = length / 14.0 / 160.0          # beam 14 section heights long
    for panel, (x, k, title) in enumerate(((40, 1.0, t["true"]), (500, 3.0, t["big"]))):
        s.text(x, 34, title, css=u"h")
        view = Iso(x + 70, 130, eye=(0.55, -0.75, 0.38))
        start, end = view((0.0, 0.0, 0.0)), view((length, 0.0, 0.0))
        s.line(start[0], start[1], end[0], end[1], "m", 1, "5 4")
        for i in range(6):
            u = length * i / 5.0
            pts = [view((u, k * to_scale * v, k * to_scale * w)) for v, w in outline]
            s.polygon(pts, "sec")
            g = view((u, 0.0, 0.0))
            s.circle(g[0], g[1], 3, "ffg")
        if panel == 0:
            s.text(x, start[1] + 120, t["chain"], css=u"s m")
        else:
            g = view((0.0, 0.0, 0.0))
            s.arrow(g[0] - 34, g[1] + 46, g[0] - 3, g[1] + 4, "o", 1.5, 7)
            s.text(g[0] - 40, g[1] + 62, t["g"], "middle", u"s to")
    s.text(480, 326, t["l1"], "middle", u"s")
    s.text(480, 350, t["l2"], "middle", u"s m")
    s.save("fig_magnify_{0}.svg".format(lang))


# ------------------------------------------------------------------- the messages
_GROUPS = (("no_file", u"Venster en rekenproces", u"Window and calculation process"),
           ("empty", u"De keten", u"The chain"),
           ("bad_file", u"Het model", u"The model"),
           ("no_shape", u"De doorsnede", u"The section"))
_FIELDS = {"eid": (u"element", u"element"), "node": (u"knoop", u"node"), "name": (u"naam", u"name"),
           "what": (u"detail", u"detail"), "percent": (u"percentage", u"percentage"),
           "mm": (u"plaats", u"position"), "degrees": (u"hoek", u"angle"),
           "count": (u"aantal", u"count"), "reason": (u"reden", u"reason"),
           "selection": (u"selectie", u"selection"), "variable": (u"variabele", u"variable"),
           "material": (u"materiaal", u"material"), "tag": (u"item", u"item"),
           "log": (u"logboek", u"log"), "outline_ivv": (u"waarde", u"value"),
           "outline_iww": (u"waarde", u"value"), "iyy": (u"waarde", u"value"),
           "izz": (u"waarde", u"value")}


def message_rows(lang):
    """[(group title, [(code, text)])] in the order of beam_utils."""
    sys.path.insert(0, os.path.join(REPO, "Scripts"))
    import beam_utils
    index = 0 if lang == "nl" else 1
    out, group = [], None

    def field(match):
        key = match.group(1)
        return u"*{0}*".format(_FIELDS.get(key, (key, key))[index])
    starts = dict((g[0], g) for g in _GROUPS)
    for code, pair in beam_utils._WARNINGS.items():
        if code in starts:
            group = []
            out.append((starts[code][1 + index], group))
        text = re.sub(r"\{(\w+)\}", field, pair[index]).replace(u"|", u"\\|")
        group.append((code, text))
    return out


def message_codes_used():
    """Codes the code base can raise: _warn, ChainError, ModelError, Failure, "code"."""
    found = set()
    patterns = (r'_warn\(\s*[\w.]+,\s*"(\w+)"', r'(?:ChainError|ModelError|Failure|SectionError)\(\s*"(\w+)"',
                r'"code":\s*"(\w+)"', r'warnings\.append\(\s*"(\w+)"')
    folder = os.path.join(REPO, "Scripts")
    for name in os.listdir(folder):
        if name.endswith(".py"):
            with io.open(os.path.join(folder, name), encoding="utf-8") as f:
                text = f.read()
            for pattern in patterns:
                found.update(re.findall(pattern, text))
    return found


def write_messages():
    sys.path.insert(0, os.path.join(REPO, "Scripts"))
    import beam_utils
    missing = sorted(message_codes_used() - set(beam_utils._WARNINGS))
    if missing:
        sys.exit("codes without a text in beam_utils: {0}".format(", ".join(missing)))
    for lang, path in (("nl", os.path.join("docs", "MecwayAdvancedBeamAnalysis.md")),
                       ("en", os.path.join("docs", "en", "MecwayAdvancedBeamAnalysis.md"))):
        lines = [u"<!-- messages: written by tools/make_figures.py --messages -->", u""]
        for title, rows in message_rows(lang):
            lines += [u"**{0}**".format(title), u""]
            lines += [u"| Code | {0} |".format(u"Tekst" if lang == "nl" else u"Text"), u"|---|---|"]
            lines += [u"| `{0}` | {1} |".format(code, text) for code, text in rows]
            lines.append(u"")
        lines.append(u"<!-- end of messages -->")
        full = os.path.join(REPO, path)
        with io.open(full, encoding="utf-8") as f:
            text = f.read()
        new, n = re.subn(r"<!-- messages:.*?<!-- end of messages -->",
                         lambda m: u"\n".join(lines), text, flags=re.S)
        if n != 1:
            sys.exit("no block of messages in {0}".format(path))
        with io.open(full, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    for lang in ("nl", "en"):
        for figure in (fig_axes, fig_stress, fig_shear_centre, fig_corner, fig_distributed,
                       fig_dataflow, fig_warping, fig_magnify):
            figure(lang)
    if "--messages" in sys.argv:
        write_messages()


if __name__ == "__main__":
    main()
