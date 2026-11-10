# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# sections.py
# The own validation sections for V6 and V7. Every section is a union of
# rectangles on one tensor grid, so that the solid mesh can be a structured
# extrusion. Units: metres. Beam along X, V = Y, W = Z (U = V x W = X).
#
# A section is a dict:
#   name      short name
#   rects     material rectangles (y0, z0, y1, z1), not overlapping
#   loops     closed polygons for section_stress.SectionField, even-odd
#   wall      length that gets n_wall elements (the thinnest wall)
#   aspect    largest in-plane aspect ratio of the long spans
#   h         height, the unit of the distances along the beam
#   lines_y   grid lines that the mesh must contain
#   lines_z

MM = 1e-3


def _rect_loop(y0, z0, y1, z1):
    return [[y0, z0], [y1, z0], [y1, z1], [y0, z1]]


def rectangle(b=40.0, h=80.0):
    b, h = b * MM, h * MM
    return {"name": "rect", "rects": [(0.0, 0.0, b, h)], "loops": [_rect_loop(0.0, 0.0, b, h)],
            "wall": 10.0 * MM, "aspect": 1.0, "h": h, "lines_y": [0.0, b], "lines_z": [0.0, h]}


def i_section(h=100.0, b=60.0, tf=8.0, tw=6.0):
    h, b, tf, tw = h * MM, b * MM, tf * MM, tw * MM
    y0, y1 = (b - tw) / 2.0, (b + tw) / 2.0
    rects = [(0.0, 0.0, b, tf), (y0, tf, y1, h - tf), (0.0, h - tf, b, h)]
    loop = [[0.0, 0.0], [b, 0.0], [b, tf], [y1, tf], [y1, h - tf], [b, h - tf], [b, h],
            [0.0, h], [0.0, h - tf], [y0, h - tf], [y0, tf], [0.0, tf]]
    return {"name": "i", "rects": rects, "loops": [loop], "wall": min(tf, tw), "aspect": 2.0,
            "h": h, "lines_y": [0.0, y0, y1, b], "lines_z": [0.0, tf, h - tf, h]}


def box(h=100.0, b=60.0, t=6.0):
    h, b, t = h * MM, b * MM, t * MM
    rects = [(0.0, 0.0, b, t), (0.0, h - t, b, h), (0.0, t, t, h - t), (b - t, t, b, h - t)]
    return {"name": "box", "rects": rects,
            "loops": [_rect_loop(0.0, 0.0, b, h), _rect_loop(t, t, b - t, h - t)],
            "wall": t, "aspect": 2.0, "h": h, "lines_y": [0.0, t, b - t, b],
            "lines_z": [0.0, t, h - t, h]}


def l_section(a=80.0, b=50.0, t=8.0):
    """Unequal angle: leg a along Z, leg b along Y, so Ivw is not zero."""
    a, b, t = a * MM, b * MM, t * MM
    rects = [(0.0, 0.0, b, t), (0.0, t, t, a)]
    loop = [[0.0, 0.0], [b, 0.0], [b, t], [t, t], [t, a], [0.0, a]]
    return {"name": "l", "rects": rects, "loops": [loop], "wall": t, "aspect": 2.0, "h": a,
            "lines_y": [0.0, t, b], "lines_z": [0.0, t, a]}


def u_section(h=100.0, b=50.0, tf=8.0, tw=6.0):
    """Channel: web along Z at y = 0, flanges along +Y."""
    h, b, tf, tw = h * MM, b * MM, tf * MM, tw * MM
    rects = [(0.0, 0.0, b, tf), (0.0, tf, tw, h - tf), (0.0, h - tf, b, h)]
    loop = [[0.0, 0.0], [b, 0.0], [b, tf], [tw, tf], [tw, h - tf], [b, h - tf], [b, h], [0.0, h]]
    return {"name": "u", "rects": rects, "loops": [loop], "wall": min(tf, tw), "aspect": 2.0,
            "h": h, "lines_y": [0.0, tw, b], "lines_z": [0.0, tf, h - tf, h]}


ALL = {"rect": rectangle, "i": i_section, "box": box, "l": l_section, "u": u_section}


def properties(sec):
    """Exact A, centroid and second moments about the centroid, from the
    rectangles."""
    a = yg = zg = 0.0
    for y0, z0, y1, z1 in sec["rects"]:
        d = (y1 - y0) * (z1 - z0)
        a += d
        yg += d * 0.5 * (y0 + y1)
        zg += d * 0.5 * (z0 + z1)
    yg, zg = yg / a, zg / a
    iyy = izz = 0.0     # int (y-yg)^2, int (z-zg)^2
    for y0, z0, y1, z1 in sec["rects"]:
        d = (y1 - y0) * (z1 - z0)
        iyy += d * ((y1 - y0) ** 2 / 12.0 + (0.5 * (y0 + y1) - yg) ** 2)
        izz += d * ((z1 - z0) ** 2 / 12.0 + (0.5 * (z0 + z1) - zg) ** 2)
    return {"A": a, "yg": yg, "zg": zg, "ip": iyy + izz}
