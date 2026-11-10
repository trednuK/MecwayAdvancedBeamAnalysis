# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test of Scripts/beam_worker.py over the real pipe, and of the parts of
# Scripts/beam_utils.py that both sides of the pipe share. The model is an own
# small .liml in the temp folder. Run with a Python with numpy and scipy:
#
#   python tests/test_beam_worker.py
#
# Reruns itself with the Python of a Code-Aster installation if needed.

from __future__ import print_function

import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

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

FAILED = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (" ({0})".format(detail) if detail != "" else ""))
    if not ok:
        FAILED.append(name)


def near(a, b, tol):
    return abs(a - b) <= tol * max(abs(b), 1e-300)


MODEL = u"""<liml version="33">
  <analysis type="S30" %(solver)s/>
  %(nodes)s
  <elset name="B" material="%(material)s">
    %(elements)s
  </elset>
  <mat name="I">
    <geometric type="I" ia="200 mm" ib="100 mm" ic="10 mm" id="6 mm" pointy="0 m" pointz="0 m" />
    <mechanical type="Isotropic" youngsmodulus="210 GPa" poissonratio="0.3" />
  </mat>
  <mat name="U">
    <geometric type="C" ca="60 mm" cb="160 mm" cc="8 mm" cd="6 mm" pointy="0 m" pointz="0 m" />
    <mechanical type="Isotropic" youngsmodulus="210 GPa" poissonratio="0.3" />
  </mat>
  <mat name="G">
    <geometric type="UniformGeneralSection" area="2000 mm²" iyy="2000000 mm⁴" izz="8000000 mm⁴" />
    <mechanical type="Isotropic" youngsmodulus="210 GPa" poissonratio="0.3" />
  </mat>
  <configuration name="A" /><configuration name="B" />
  <nodeselection name="points"><node nid="1" /></nodeselection>
  <solution><results><elementselection name="not_read" /></results></solution>
</liml>
"""


def write_model(path, material="I", n=10, length=2.0, solver=u""):
    nodes = u"".join(u'<node nid="{0}" x="{1!r}" y="0" z="0" />'.format(i + 1, length * i / n)
                     for i in range(n + 1))
    elements = u"".join(u'<elem eid="{0}" shape="line2" nodes="{0} {1}" />'.format(i + 1, i + 2)
                        for i in range(n))
    with open(path, "w", encoding="utf-8") as f:
        f.write(MODEL % {"nodes": nodes, "elements": elements, "material": material,
                         "solver": solver})


def raw_constant(n=10, **values):
    out = {}
    names = {"N": "tensileforce", "Vv": "sheary", "Vw": "shearz", "T": "torsion",
             "Mv": "bendingmomv", "Mw": "bendingmomw"}
    for i in range(n):
        out[str(i + 1)] = dict((names[k], [float(values.get(k, 0.0))] * 2) for k in names)
    return out


class Pipe(object):
    def __init__(self, tmp):
        self.seq = 0
        self.log = os.path.join(tmp, "worker.log")
        self.proc = subprocess.Popen([sys.executable, "-E", os.path.join(SCRIPTS, "beam_worker.py"),
                                      self.log], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     creationflags=0x08000000)

    def read(self, n):
        b = self.proc.stdout.read(n)
        if len(b) != n:
            raise IOError("pipe ended: {0} of {1}".format(len(b), n))
        return b

    def reply(self):
        magic, seq, nj, w, h, np_ = bu.REPLY.unpack(self.read(bu.REPLY.size))
        if not bu.valid_reply_head(magic, nj, w, h, np_):
            raise IOError("bad header")
        info = json.loads(self.read(nj).decode("utf-8"))
        pixels = self.read(np_) if np_ else None
        return seq, info, pixels, w, h

    def ask(self, obj):
        self.seq += 1
        self.proc.stdin.write(bu.pack_request(self.seq, obj))
        self.proc.stdin.flush()
        seq, info, pixels, w, h = self.reply()
        if seq != self.seq:
            raise IOError("out of step")
        return info, pixels, w, h

    def raw(self, data):
        self.seq += 1
        self.proc.stdin.write(bu.REQUEST.pack(self.seq, len(data)) + data)
        self.proc.stdin.flush()
        return self.reply()


FRAME = {"cmd": "frame", "gen": 1, "frame": 1, "s": 1.05, "measure": "vonmises", "width": 300,
         "height": 200, "view": None, "coarse": False, "scale": None, "shear_centre": True,
         "arrows": {"on": True, "length": 1.0}, "ui_scale": 1.0}


def frame(pipe, **kw):
    rq = dict(FRAME)
    rq.update(kw)
    return pipe.ask(rq)


def session_open(path, raw, n=10, route="element", api=None):
    return {"cmd": "open", "gen": 1, "path": path, "element_ids": list(range(1, n + 1)),
            "raw": raw, "route": route, "api": api}


tmp = tempfile.mkdtemp(prefix="beam_worker_")
try:
    path = os.path.join(tmp, u"modèl.liml")
    write_model(path)
    pipe = Pipe(tmp)
    seq, hello, _, _, _ = pipe.reply()
    check("hello first, seq 0, with versions", seq == 0 and hello.get("ok") and "numpy" in hello, hello)

    info, _, _, _ = frame(pipe)
    check("frame before open is refused, worker lives", info == {"ok": False, "code": "not_open", "data": {}},
          info)

    info, _, _, _ = pipe.ask({"cmd": "scan", "path": path})
    head = info["head"]
    check("scan: analysis, solver, configurations", head["analysis"] == "S30" and head["solver"] == "internal"
          and head["configurations"] == ["A", "B"], head)
    check("scan: node selections, stops at the solution", head["node_selections"] == ["points"], head)

    info, _, _, _ = pipe.ask(session_open(path, raw_constant(N=1000.0, Mw=500.0, Vv=-200.0, T=30.0)))
    check("open with an accent in the path", info.get("ok") and near(info["length"], 2.0, 1e-9), info.get("code"))
    check("open: measures", info["measures"][:2] == ["vonmises", "sigma"] and len(info["measures"]) == 8)
    check("open: side view straight, in order", len(info["side"]["points"]) == 11 and
          near(abs(info["side"]["points"][-1][0] - info["side"]["points"][0][0]), 2.0, 1e-9) and
          max(abs(p[1]) for p in info["side"]["points"]) < 1e-9)
    codes = [w["code"] for w in info["warnings"]]
    check("open: corner warning on a sharp I section", "corner_peak" in codes, codes)
    check("open: no eccentric warning on a symmetric section", "eccentric_shear_centre" not in codes, codes)

    W, H = 300, 200
    info, pixels, w, h = frame(pipe)
    check("frame: size in pixels", (w, h) == (W, H) and len(pixels) == W * H * 4)
    check("frame: echoes generation and number", info["gen"] == 1 and info["frame"] == 1)
    check("frame: forces at the place", near(info["forces"]["N"], 1000.0, 1e-9) and
          near(info["forces"]["Mw"], 500.0, 1e-9))
    check("frame: element and xi", info["eid"] == 6 and 0.0 <= info["xi"] <= 1.0, (info["eid"], info["xi"]))
    img = np.frombuffer(pixels, dtype=np.uint32)
    check("frame: colours and background", int((img != 0xFFFFFFFF).sum()) > 1000 and
          int(img[0]) == 0xFFFFFFFF)
    check("frame: max outside corners is a number", info["outside"]["value"] > 0 and "point" in info["outside"])
    check("frame: peak marker when the corner is higher",
          ("peak" not in info) or abs(info["peak"]["value"]) >= abs(info["outside"]["value"]))
    check("frame: speed reported", info["ms"]["paint"] >= 0.0)

    # sigma of the worker against beam theory, I section: bending about W
    info, _, _, _ = frame(pipe, measure="sigma", on={"N": False, "Vv": False, "Vw": False, "T": False,
                                                     "Mv": False, "Mw": True})
    # V runs along the height of an I (section_profiles), so Iww is the strong axis
    a, b, c, d = 0.2, 0.1, 0.01, 0.006
    iww = (b * a ** 3 - (b - d) * (a - 2 * c) ** 3) / 12.0
    check("sigma from Mw alone: Mw a/2 / Iww", near(abs(info["overall"]["value"]), 500.0 * (a / 2) / iww, 1e-3),
          info["overall"]["value"])
    check("the frame says what it drew", info["measure"] == "sigma")
    lo, hi = info["lo"], info["hi"]
    check("auto scale from the nodal values", lo < 0.0 < hi)

    # fixed scale
    info, _, _, _ = frame(pipe, measure="sigma", scale=[-1e5, 1e5])
    check("fixed scale is kept and marks clipping", info["lo"] == -1e5 and info["hi"] == 1e5 and
          info["clip_hi"] and info["clip_lo"])

    # coarse
    info, pixels, w, h = frame(pipe, coarse=True, width=320, height=240)
    check("coarse frame has the full size", (w, h) == (320, 240) and len(pixels) == 320 * 240 * 4 and info["coarse"])
    info2, pixels2, _, _ = frame(pipe, coarse=False, width=320, height=240)
    check("coarse and fine agree on the view", info["view"]["s"] == info2["view"]["s"])
    fine_img = np.frombuffer(pixels2, dtype=np.uint32) != 0xFFFFFFFF
    coarse_img = np.frombuffer(pixels, dtype=np.uint32) != 0xFFFFFFFF
    check("coarse picture covers the same place", (fine_img & coarse_img).sum() > 0.6 * fine_img.sum(),
          ((fine_img & coarse_img).sum(), fine_img.sum()))

    # view and probe
    v = info2["view"]
    x, y = int((0.0 - v["x0"]) * v["s"]), int((v["y1"] - 0.0) * v["s"])
    info, _, _, _ = frame(pipe, frame=7, measure="sigma", width=320, height=240,
                          on={"N": True, "Vv": False, "Vw": False, "T": False, "Mv": False, "Mw": False})
    p, _, _, _ = pipe.ask({"cmd": "probe", "gen": 1, "frame": 7, "x": x, "y": y})
    check("probe in the web, at the centroid", p["hit"] and abs(p["v"]) < 2.0 / v["s"] and abs(p["w"]) < 2.0 / v["s"], p)
    check("probe value: N/A at the neutral axis", near(p["sigma"], 1000.0 / (2 * b * c + (a - 2 * c) * d), 0.02), p["sigma"])
    p, _, _, _ = pipe.ask({"cmd": "probe", "gen": 1, "frame": 7, "x": 1, "y": 1})
    check("probe beside the section", not p["hit"])
    p, _, _, _ = pipe.ask({"cmd": "probe", "gen": 1, "frame": 6, "x": x, "y": y})
    check("probe of an older frame is not answered", not p["hit"] and p["frame"] == 6)

    zoom = {"zoom": 4.0, "cx": 0.0, "cy": 0.0}
    info, _, _, _ = frame(pipe, view=zoom, width=320, height=240)
    check("zoom: scale times the factor", near(info["view"]["s"], 4.0 * v["s"], 1e-9) and info["view"]["zoom"] == 4.0)

    info, _, _, _ = frame(pipe, measure="tresca", arrows={"on": False})
    check("no arrows: no arrow value", info["arrow_max"] is None)
    info, _, _, _ = frame(pipe, s=99.0)
    check("position beyond the end is clamped", near(info["s"], 2.0, 1e-9) and info["eid"] == 10, info["s"])

    # another result set
    info, _, _, _ = pipe.ask({"cmd": "forces", "gen": 1, "raw": raw_constant(N=4000.0), "route": "element"})
    check("forces keeps the beam", info["ok"])
    info, _, _, _ = frame(pipe)
    check("the new forces are used", near(info["forces"]["N"], 4000.0, 1e-9))

    # failures
    info, _, _, _ = pipe.ask(session_open(path, raw_constant(), api={"n_nodes": 12}))
    check("file against API: other count", info == {"ok": False, "code": "file_differs",
                                                    "data": {"what": "12 / 11 nodes"}}, info)
    info, _, _, _ = pipe.ask(session_open(path, raw_constant(), api={"nodes": {"3": [9.0, 0.0, 0.0]}}))
    check("file against API: other coordinate", info["code"] == "file_differs", info)
    info, _, _, _ = pipe.ask(session_open(os.path.join(tmp, "missing.liml"), raw_constant()))
    check("no file", info["code"] == "no_file")
    info, _, _, _ = pipe.ask({"cmd": "open", "gen": 1, "path": path, "element_ids": [1, 3], "raw": {}})
    check("not a line: chain error with code", info["code"] == "gap", info)
    info, _, _, _ = pipe.ask({"cmd": "nonsense"})
    check("unknown command", info["code"] == "unknown_command")
    seq, info, _, _, _ = pipe.raw(b"{not json")
    check("bad JSON: answered, worker lives", info["code"] == "internal" and seq == pipe.seq)

    # U section: the term of the shear centre (research note 5.2)
    pu = os.path.join(tmp, "u.liml")
    write_model(pu, "U")
    info, _, _, _ = pipe.ask(session_open(pu, raw_constant(Vw=1000.0, Vv=400.0)))
    codes = [w["code"] for w in info["warnings"]]
    check("U section: eccentric shear centre warned", "eccentric_shear_centre" in codes, codes)
    only_v = {"N": False, "Vv": True, "Vw": True, "T": False, "Mv": False, "Mw": False}
    on, _, _, _ = frame(pipe, on=only_v)
    off, _, _, _ = frame(pipe, on=only_v, shear_centre=False)
    check("T unchecked, shear checked: term is still there", abs(on["term"]) > 1.0 and
          on["T_S"] == on["term"] and on["T"] == 0.0, on["term"])
    check("switch off: T_S = T, the term is still shown", off["T_S"] == 0.0 and off["term"] == on["term"])
    check("switch changes the stress", abs(on["overall"]["value"] - off["overall"]["value"]) >
          1e-3 * abs(off["overall"]["value"]), (on["overall"]["value"], off["overall"]["value"]))
    none, _, _, _ = frame(pipe, on={"N": True, "Vv": False, "Vw": False, "T": True, "Mv": True, "Mw": True})
    check("shear unchecked: no term", none["term"] == 0.0)

    # no shape: forces still shown
    pg = os.path.join(tmp, "g.liml")
    write_model(pg, "G")
    info, _, _, _ = pipe.ask(session_open(pg, raw_constant(N=10.0)))
    codes = [w["code"] for w in info["warnings"]]
    check("general section without a shape opens with a warning", info["ok"] and "no_shape" in codes, codes)
    check("without a shape: sigma only", info["measures"] == ["sigma"], info["measures"])
    info, pixels, _, _ = frame(pipe)
    check("without a shape: forces, no picture", pixels is None and info["no_section"]["code"] == "no_shape" and
          near(info["forces"]["N"], 10.0, 1e-9))

    # kinematics: cantilever, tip load along +Y gives Mw from the curvature
    n, length, force = 10, 2.0, 1000.0
    e = 210e9
    izz = iww
    def v(x):
        return force * x ** 2 * (3 * length - x) / (6 * e * izz)

    def slope(x):
        return force * x * (2 * length - x) / (2 * e * izz)
    raw = {}
    for i in range(n):
        x0, x1 = length * i / n, length * (i + 1) / n
        raw[str(i + 1)] = {"displx": [0.0, 0.0], "disply": [v(x0), v(x1)], "displz": [0.0, 0.0],
                           "rotx": [0.0, 0.0], "roty": [0.0, 0.0], "rotz": [slope(x0), slope(x1)]}
    info, _, _, _ = pipe.ask(session_open(path, raw, route="kinematics"))
    check("kinematics: sigma only", info["ok"] and info["measures"] == ["sigma"], info.get("measures"))
    info, _, _, _ = frame(pipe, s=0.0)
    check("kinematics: asked von Mises, gets sigma", info["measure"] == "sigma")
    check("kinematics: Mw at the root from the curvature", near(abs(info["forces"]["Mw"]), force * length, 0.1),
          info["forces"]["Mw"])
    check("kinematics: says so", "kinematics_only" in [w["code"] for w in info["notes"]], info["notes"])
    check("kinematics: tau is zero, not invented", info["forces"]["Vv"] == 0.0 and info["forces"]["T"] == 0.0)
    bad = os.path.join(tmp, "bad_props.liml")
    write_model(bad, "G")
    with open(bad, encoding="utf-8") as f:
        text = f.read()
    with open(bad, "w", encoding="utf-8") as f:
        f.write(text.replace(u'area="2000 mm²"', u'area="0 mm²"'))
    info, _, _, _ = pipe.ask(session_open(bad, raw, route="kinematics"))
    check("kinematics, bad section: coded failure", not info["ok"] and info["code"] == "bad_properties" and
          info["data"].get("name") == "G", info)

    # configurations: a load suppressed in A
    conf = os.path.join(tmp, "conf.liml")
    write_model(conf)
    with open(conf, encoding="utf-8") as f:
        text = f.read()
    with open(conf, "w", encoding="utf-8") as f:
        f.write(text.replace(u'  <nodeselection name="points">',
                             u'  <elementselection name="all">' +
                             u"".join(u'<element eid="{0}" />'.format(i) for i in range(1, 11)) +
                             u'</elementselection>\n  <linepressure selection="all"><y>-1 N/mm</y>'
                             u'<suppressed configuration="A" /></linepressure>\n'
                             u'  <nodeselection name="points">'))

    def skipped(job):
        info, _, _, _ = pipe.ask(job)
        return info, [w["data"].get("reason") for w in info.get("warnings") or []
                      if w["code"] == "correction_skipped"]
    job = session_open(conf, raw_constant())
    info, reasons = skipped(job)
    check("configuration unknown: correction off, said", info["ok"] and reasons == ["configuration"],
          info.get("warnings"))
    job["configuration"] = "A"
    info, reasons = skipped(job)
    check("configuration A: load left out", info["ok"] and reasons == [], info.get("warnings"))
    job["configuration"] = "X"
    info, _ = skipped(job)
    check("a configuration not in the file: coded failure", not info["ok"] and
          info["code"] == "configuration_missing", info)

    # several sections, one colour scale
    n = 10
    raw = {}
    for i in range(n):
        x0, x1 = length * i / n, length * (i + 1) / n
        raw[str(i + 1)] = {"tensileforce": [100.0, 100.0], "sheary": [500.0, 500.0],
                           "shearz": [0.0, 0.0], "torsion": [10.0, 10.0],
                           "bendingmomv": [0.0, 0.0],
                           "bendingmomw": [500.0 * (length - x0), 500.0 * (length - x1)]}
    info, _, _, _ = pipe.ask(session_open(path, raw))
    check("open for the sheet", info["ok"])
    sheet = {"sheet": {"n": 5}, "band": 18, "measure": "sigma", "width": 640, "height": 400}
    info, pixels, w, h = frame(pipe, **sheet)
    check("sheet: one picture of canvas size", (w, h) == (640, 400) and len(pixels) == 640 * 400 * 4)
    tiles = info["tiles"]
    check("sheet: evenly along the beam, both ends in", len(tiles) == 5 and
          [round(t["s"], 9) for t in tiles] == [0.0, 0.5, 1.0, 1.5, 2.0], [t["s"] for t in tiles])
    check("sheet: numbered along the chain", [t["number"] for t in tiles] == [1, 2, 3, 4, 5] and
          [t["s"] for t in tiles] == sorted(t["s"] for t in tiles), [t["number"] for t in tiles])
    check("sheet: rectangles inside the canvas", all(t["x"] + t["w"] <= 640 and t["y"] + t["h"] <= 400
                                                     for t in tiles))
    rq = dict((k, v) for k, v in sheet.items() if k not in ("sheet", "band"))
    singles = [frame(pipe, s=t["s"], **rq)[0] for t in tiles]
    check("sheet: one scale over all sections", info["lo"] == min(i["lo"] for i in singles) and
          info["hi"] == max(i["hi"] for i in singles), (info["lo"], info["hi"]))
    check("sheet: each tile says what its section says",
          all(near(t["outside"], i["outside"]["value"], 1e-9) for t, i in zip(tiles, singles)))
    check("sheet: the largest over all tiles", near(abs(info["outside"]["value"]),
                                                    max(abs(i["outside"]["value"]) for i in singles), 1e-12))
    check("sheet: arrows on one reference, the largest of the tiles", near(info["arrow_max"], max(i["arrow_max"] for i in singles), 0.25),
          info["arrow_max"])
    check("sheet: the root is the hottest tile", abs(tiles[0]["outside"]) > abs(tiles[3]["outside"]))
    img = np.frombuffer(pixels, dtype=np.uint32).reshape(400, 640)
    last_tile = tiles[-1]
    # at s = L the moment is gone: only N and V, so the colours differ from the root
    a0 = img[tiles[0]["y"] + 18:tiles[0]["y"] + tiles[0]["h"], :tiles[0]["w"]]
    a1 = img[last_tile["y"] + 18:last_tile["y"] + last_tile["h"],
             last_tile["x"]:last_tile["x"] + last_tile["w"]]
    check("sheet: tiles are drawn, each in its place", (a0 != 0xFFFFFFFF).sum() > 200 and
          (a1 != 0xFFFFFFFF).sum() > 200 and (a0 != a1).any())
    check("sheet: label band stays free", (img[:18, :tiles[0]["w"]] == 0xFFFFFFFF).all())
    fixed, _, _, _ = frame(pipe, scale=[-1e6, 1e6], **sheet)
    check("sheet: fixed scale", fixed["lo"] == -1e6 and fixed["hi"] == 1e6 and fixed["clip_hi"])
    info, _, _, _ = frame(pipe, frame=21, **sheet)
    t = info["tiles"][2]
    p, _, _, _ = pipe.ask({"cmd": "probe", "gen": 1, "frame": 21, "x": t["x"] + t["w"] // 2,
                           "y": t["y"] + 18 + (t["h"] - 18) // 2})
    check("sheet: hover finds the tile", p["hit"] and near(p["s"], t["s"], 1e-9), p)
    check("sheet: hover says its number", p["number"] == t["number"] == 3, p.get("number"))
    p, _, _, _ = pipe.ask({"cmd": "probe", "gen": 1, "frame": 21, "x": t["x"] + 5, "y": t["y"] + 3})
    check("sheet: hover on a label finds nothing", not p["hit"])
    info, _, _, _ = frame(pipe, measure="sigma", s=0.5)
    pl = info["place"]
    check("place: point on the beam", near(pl["point"][0], 0.5, 1e-9) and abs(pl["point"][1]) < 1e-12 and
          near(sum(c * c for c in pl["v"]), 1.0, 1e-9) and pl["half"] > 0.1, pl)

    # a chain with two sections, one without a shape
    pm = os.path.join(tmp, "mixed.liml")
    write_model(pm)
    with open(pm, encoding="utf-8") as f:
        text = f.read()
    text = text.replace(u'<elem eid="6"', u'</elset><elset name="C" material="G"><elem eid="6"')
    with open(pm, "w", encoding="utf-8") as f:
        f.write(text)
    info, _, _, _ = pipe.ask(session_open(pm, raw))
    check("mixed chain opens", info["ok"] and "section_change" in [w["code"] for w in info["warnings"]],
          info.get("warnings"))
    info, pixels, _, _ = frame(pipe, **sheet)
    check("sheet: a tile without a shape is told, the others draw", pixels is not None and
          "no_section" in info["tiles"][-1] and "outside" in info["tiles"][0], info["tiles"][-1])
    t = info["tiles"][-1]
    p, _, _, _ = pipe.ask({"cmd": "probe", "gen": 1, "frame": info["frame"],
                           "x": t["x"] + t["w"] // 2,
                           "y": t["y"] + t["band"] + (t["h"] - t["band"]) // 2})
    check("sheet: hover on a tile without a shape says so, with its number and place",
          not p["hit"] and p.get("no_section") and p["number"] == t["number"] and
          near(p["s"], t["s"], 1e-12), p)
    p, _, _, _ = pipe.ask({"cmd": "probe", "gen": 1, "frame": info["frame"],
                           "x": t["x"] + t["w"] // 2, "y": t["y"] + 2})
    check("sheet: its title band is not the tile", not p["hit"] and not p.get("no_section"), p)

    # the marker in a saved file counts for neither side
    pk = os.path.join(tmp, "marker.liml")
    write_model(pk)
    with open(pk, encoding="utf-8") as f:
        text = f.read()
    text = text.replace(u'<mat name="I">', u'''<node nid="12" x="1" y="1" z="0" /><node nid="13" x="1" y="2" z="0" />
      <node nid="14" x="1" y="2" z="1" /><node nid="15" x="1" y="1" z="1" />
      <elset name="Balkpositie_tijdelijk" material="Balkpositie_tijdelijk">
        <elem eid="11" shape="quad4" nodes="12 13 14 15" /></elset>
      <mat name="Balkpositie_tijdelijk"><geometric type="Plate" thickness="0.1 mm" />
      <mechanical type="Isotropic" youngsmodulus="1000 Pa" poissonratio="0.3" /></mat>
      <mat name="I">''')
    with open(pk, "w", encoding="utf-8") as f:
        f.write(text)
    info, _, _, _ = pipe.ask(session_open(pk, raw_constant(N=1.0), api={"n_nodes": 11, "n_elements": 10}))
    check("marker in the file is not a difference", info["ok"], info)
    info, _, _, _ = pipe.ask(session_open(pk, raw_constant(N=1.0), api={"n_nodes": 12, "n_elements": 10}))
    check("a real difference still shows", info.get("code") == "file_differs", info)

    # warping: the stress holds only from 4/k away from a clamp, for an open section
    import section_profiles
    import section_stress
    pu = os.path.join(tmp, "channel.liml")
    write_model(pu, "U", length=2.0)
    info, _, _, _ = pipe.ask(session_open(pu, raw))
    zone = [w for w in info["warnings"] if w["code"] == "warping_zone"]
    with open(pu, encoding="utf-8") as f:
        attrs = {"type": "C", "ca": "60 mm", "cb": "160 mm", "cc": "8 mm", "cd": "6 mm"}
    field = section_stress.SectionField(section_profiles.loops_for(attrs)["loops"], nu=0.3)
    check("warping zone: one message, 4/k, with the section", len(zone) == 1 and
          near(zone[0]["data"]["length"], 4.0 * field.warping_length, 1e-9) and
          zone[0]["data"]["name"] == "U", zone)
    bu.settings()["language"] = "en"
    text = bu.warning_text("warping_zone", zone[0]["data"])
    check("warping zone: in mm", u"{0} (4/k)".format(bu.fmt_length(zone[0]["data"]["length"])) in text, text)
    plain = 4.0 * field.warping_length
    write_model(pu, "U", length=plain / 0.05 * 1.01)
    info, _, _, _ = pipe.ask(session_open(pu, raw))
    check("warping zone: not on a chain 20 times as long", "warping_zone" not in [w["code"] for w in info["warnings"]])
    write_model(pu, "U", length=plain / 0.05 * 0.99)
    info, _, _, _ = pipe.ask(session_open(pu, raw))
    check("warping zone: just above the limit", "warping_zone" in [w["code"] for w in info["warnings"]])

    # #ASTER-SECTIE counts for a Code-Aster solution, and only then
    pg = os.path.join(tmp, "aster.liml")
    write_model(pg, "G")
    with open(pg, encoding="utf-8") as f:
        text = f.read()
    text = text.replace(u"  <configuration", u"  <ccxmodeldefinition>"
                        u"** SECTIE-VORM G: N=4, A=0.002\n"
                        u"** SECTIE-VORM G: P=-0.05 -0.01 0.05 -0.01 0.05 0.01\n"
                        u"** SECTIE-VORM G: P=-0.05 0.01\n"
                        u"** ASTER-SECTIE G: A=0.002, IY=2e-06, IZ=8e-06, JX=1e-06, EY=0.003, "
                        u"EZ=-0.004</ccxmodeldefinition>\n  <configuration", 1)
    text = text.replace(u'iyy="2000000 mm⁴" izz="8000000 mm⁴"',
                        u'iyy="2000000 mm⁴" izz="8000000 mm⁴" torsionconstant="1000000 mm⁴"')
    with open(pg, "w", encoding="utf-8") as f:
        f.write(text)
    rg = raw_constant(Vv=100.0, Vw=50.0)
    job = session_open(pg, rg)
    job["aster"] = True
    info, _, _, _ = pipe.ask(job)
    check("outline far from the material: shape_inertia, not also props_differ",
          "shape_inertia" in [w["code"] for w in info["warnings"]] and
          "props_differ" not in [w["code"] for w in info["warnings"]], info.get("warnings"))
    check("Code-Aster: the offset differs and counts", info["ok"] and
          "aster_offset_used" in [w["code"] for w in info["warnings"]], info.get("warnings"))
    check("Code-Aster: no warning about Mecway's own beam",
          "eccentric_shear_centre" not in [w["code"] for w in info["warnings"]])
    used, _, _, _ = frame(pipe, s=0.5, measure="sigma")
    check("Code-Aster: the term is EY Vw - EZ Vv of the line",
          near(used["term"], 0.003 * 50.0 + 0.004 * 100.0, 1e-9), used["term"])
    info, _, _, _ = pipe.ask(session_open(pg, rg))
    check("another solution: the shape counts, and it says so",
          "aster_offset_unused" in [w["code"] for w in info["warnings"]], info.get("warnings"))
    own, _, _, _ = frame(pipe, s=0.5, measure="sigma")
    check("another solution: the term of the shape", abs(own["term"]) < 1e-6, own["term"])

    # a small difference stays props_differ
    near_shape = os.path.join(tmp, "near_shape.liml")
    close = text.replace(u'iyy="2000000 mm⁴"', u'iyy="68000 mm⁴"').replace(
        u'izz="8000000 mm⁴"', u'izz="1700000 mm⁴"')
    with open(near_shape, "w", encoding="utf-8") as f:
        f.write(close)
    info, _, _, _ = pipe.ask(session_open(near_shape, rg))
    check("outline 2 % from the material: props_differ, not shape_inertia",
          "props_differ" in [w["code"] for w in info["warnings"]] and
          "shape_inertia" not in [w["code"] for w in info["warnings"]], info.get("warnings"))

    # solver="ccx" in the file does not count against the trace of the bridge
    pc = os.path.join(tmp, "ccx_aster.liml")
    write_model(pc, "I", solver=u'solver="ccx"')
    job = session_open(pc, raw)
    job["aster"] = True
    info, _, _, _ = pipe.ask(job)
    check("a bridge solution of a CCX model is Code-Aster's: no CCX warnings",
          info["ok"] and "ccx_line2" not in [w["code"] for w in info["warnings"]], info.get("warnings"))

    # contour lines over the pipe
    pl = os.path.join(tmp, "lines.liml")
    write_model(pl, "I")
    info, _, _, _ = pipe.ask(session_open(pl, raw_constant(N=1.0e4, Mw=2.0e3)))
    _, plain_px, _, _ = frame(pipe, measure="sigma", s=0.5, width=400, height=300)
    plain_img = np.frombuffer(plain_px, dtype=np.uint32).copy()
    info, px, _, _ = frame(pipe, measure="sigma", s=0.5, width=400, height=300, contours=True)
    check("contours: lines in the picture", (np.frombuffer(px, dtype=np.uint32) != plain_img).sum() > 20)
    info, px, _, _ = frame(pipe, measure="sigma", s=0.5, width=400, height=300, contours=True, coarse=True)
    _, plain_coarse, _, _ = frame(pipe, measure="sigma", s=0.5, width=400, height=300, coarse=True)
    check("contours: not in a coarse frame", px == plain_coarse)
    tiles_off, a, _, _ = frame(pipe, sheet={"n": 3}, band=18, measure="sigma", width=600, height=300)
    a = np.frombuffer(a, dtype=np.uint32).copy()
    tiles_on, b, _, _ = frame(pipe, sheet={"n": 3}, band=18, measure="sigma", width=600, height=300,
                              contours=True)
    check("contours: also on several sections", (np.frombuffer(b, dtype=np.uint32) != a).sum() > 20)
    sc_on, b, _, _ = frame(pipe, sheet={"n": 3}, band=18, measure="sigma", width=600, height=300,
                           contours=True, scale=[-1e6, 1e6])
    _, c, _, _ = frame(pipe, sheet={"n": 3}, band=18, measure="sigma", width=600, height=300,
                       scale=[-1e6, 1e6])
    check("contours: on a fixed scale", sc_on["lo"] == -1e6 and
          (np.frombuffer(b, dtype=np.uint32) != np.frombuffer(c, dtype=np.uint32)).sum() > 20)

    # the maxima per node, for the results of Mecway
    ph = os.path.join(tmp, "maxima.liml")
    write_model(ph, "I")
    rh = dict((str(i + 1), {"tensileforce": [0.0, 0.0], "sheary": [0.0, 0.0], "shearz": [0.0, 0.0],
                            "torsion": [0.0, 0.0], "bendingmomv": [0.0, 0.0],
                            "bendingmomw": [100.0 * (10 - i), 100.0 * (9 - i)]}) for i in range(10))
    info, _, _, _ = pipe.ask(session_open(ph, rh))
    info, _, _, _ = pipe.ask({"cmd": "node_maxima", "gen": 1})
    sig = info["values"]["sectionsigma"]
    check("maxima: all nodes of the chain", sorted(int(k) for k in sig) == list(range(1, 12)), sorted(sig))
    one, _, _, _ = frame(pipe, s=0.2, measure="sigma")
    check("maxima: what the window says at that node", near(sig["2"], abs(one["outside"]["value"]), 1e-9),
          (sig["2"], one["outside"]["value"]))
    check("maxima: von Mises is sigma here, tau is zero", near(info["values"]["sectionvonmises"]["2"], sig["2"], 1e-9) and
          info["values"].get("sectiontau", {}).get("2", 0.0) < 1e-6 * sig["2"])
    off, _, _, _ = pipe.ask({"cmd": "node_maxima", "gen": 1, "on": {"Mw": False}})
    check("maxima: follows the check boxes", not off["values"]["sectionsigma"] or
          max(off["values"]["sectionsigma"].values()) < 1e-6, off["values"]["sectionsigma"])

    # a section of two parts has no tau: only sigma goes to the results
    pp = os.path.join(tmp, "parts.liml")
    write_model(pp, "G")
    with open(pp, encoding="utf-8") as f:
        text = f.read()
    text = text.replace(u"  <configuration", u"  <ccxmodeldefinition>"
                        u"** SECTIE-VORM G: N=4 4, A=0.002\n"
                        u"** SECTIE-VORM G: P=-0.05 0.01 0.05 0.01 0.05 0.02\n"
                        u"** SECTIE-VORM G: P=-0.05 0.02\n"
                        u"** SECTIE-VORM G: P=-0.05 -0.02 0.05 -0.02 0.05 -0.01\n"
                        u"** SECTIE-VORM G: P=-0.05 -0.01</ccxmodeldefinition>\n  <configuration", 1)
    with open(pp, "w", encoding="utf-8") as f:
        f.write(text)
    info, _, _, _ = pipe.ask(session_open(pp, raw_constant(N=1.0e4, Mw=2.0e3)))
    check("two parts: open", info["ok"] and info["measures"] == ["sigma"], info.get("measures"))
    info, _, _, _ = pipe.ask({"cmd": "node_maxima", "gen": 1})
    check("two parts: no tau and von Mises as if measured", sorted(info["values"]) == ["sectionsigma"],
          sorted(info["values"]))

    # stdin end stops the worker
    pipe.proc.stdin.close()
    t0 = time.time()
    code = pipe.proc.wait(timeout=10)
    with open(pipe.log, encoding="utf-8") as f:
        logged = f.read()
    check("a failure of the worker is in its own log", "JSONDecodeError" in logged and "file_differs" in logged)
    check("stdin end stops the worker", code == 0 and time.time() - t0 < 5, (code, time.time() - t0))

    # a print in a library must not reach the pipe
    code = ("import sys, os; sys.path.insert(0, {0!r}); import beam_worker as w; "
            "i, o = w.redirect({1!r}); print('stray'); os.write(1, b'stray2'); "
            "sys.stderr.write('stray3'); w.write_all(o, b'OK')").format(
        SCRIPTS, os.path.join(tmp, "redirect.log"))
    out = subprocess.run([sys.executable, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         creationflags=0x08000000, timeout=30).stdout
    check("prints of a library end in the log", out == b"OK", out)
    with open(os.path.join(tmp, "redirect.log"), encoding="utf-8") as f:
        logged = f.read()
    check("the log holds them", "stray" in logged and "stray3" in logged, logged[:60])

    # a Python without the packages: hello says so
    code = ("import sys; sys.path.insert(0, {0!r}); sys.modules['scipy'] = None; "
            "import beam_worker as w; sys.exit(w.main(['x', {1!r}]))").format(
        SCRIPTS, os.path.join(tmp, "noscipy.log"))
    pr = subprocess.run([sys.executable, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        creationflags=0x08000000, timeout=60)
    magic, seq, nj, _, _, _ = bu.REPLY.unpack(pr.stdout[:bu.REPLY.size])
    reply = json.loads(pr.stdout[bu.REPLY.size:bu.REPLY.size + nj].decode("utf-8"))
    check("import failure is reported as hello", magic == bu.MAGIC and seq == 0 and
          reply["code"] == "import_failed" and pr.returncode == 1, reply.get("code"))
finally:
    try:
        pipe.proc.kill()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)

# ------------------------------------------------------------------ side view
p = bw.side_view([[0, 0, 0], [1, 0, 0], [1, 0, 1]])
d = [math.hypot(p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1]) for i in range(2)]
check("side view keeps the lengths of a kinked chain", near(d[0], 1.0, 1e-9) and near(d[1], 1.0, 1e-9), d)
p = bw.side_view([[0, 0, 0], [0, 3, 0], [0, 7, 0]])
check("side view runs left to right", p[0][0] < p[-1][0])

# ---------------------------------------------------------------- request slot
slot = bu.RequestSlot()
check("empty slot", slot.take() is None and slot.empty())
slot.put_frame({"cmd": "frame", "frame": 1})
slot.put_frame({"cmd": "frame", "frame": 2})
slot.put_frame({"cmd": "frame", "frame": 3})
check("only the newest frame", slot.take()["frame"] == 3 and slot.take() is None and slot.replaced == 2)
slot.put_frame({"cmd": "frame", "frame": 1})
slot.put_control({"cmd": "forces", "n": 1})
slot.put_control({"cmd": "forces", "n": 2})
slot.put_probe({"cmd": "probe", "x": 1})
slot.put_probe({"cmd": "probe", "x": 2})
got = [slot.take() for _ in range(5)]
check("control first and in order, then frame, then the newest probe",
      [g and (g.get("n") or g.get("frame") or g.get("x")) for g in got] == [1, 2, 1, 2, None], got)
slot.put_frame({"cmd": "frame", "frame": 5, "coarse": True})
a_ = slot.take()
b_ = slot.take()
check("after a coarse frame the fine one follows", a_["coarse"] and not b_["coarse"] and b_["frame"] == 5)
slot.put_frame({"cmd": "frame", "frame": 6, "coarse": True})
slot.take()
slot.put_frame({"cmd": "frame", "frame": 7, "coarse": True})
a_ = slot.take()
b_ = slot.take()
check("a newer frame replaces the fine one", a_["frame"] == 7 and b_["frame"] == 7 and slot.take() is None)
slot.put_frame({"cmd": "frame", "frame": 8})
slot.put_probe({"cmd": "probe"})
slot.put_control({"cmd": "open"})
check("open drops what belongs to the old beam", slot.take()["cmd"] == "open" and slot.take() is None)

# ------------------------------------------------------------------- protocol
data = bu.pack_request(9, {"text": u"µ è"})
seq, n = bu.REQUEST.unpack(data[:bu.REQUEST.size])
check("request round trip", seq == 9 and json.loads(data[bu.REQUEST.size:].decode("utf-8"))["text"] == u"µ è")
check("garbage header refused", not bu.valid_reply_head(0x12345678, 5, 1, 1, 4) and
      not bu.valid_reply_head(bu.MAGIC, 5, 100000, 1, 400000) and
      not bu.valid_reply_head(bu.MAGIC, 5, 10, 10, 399))

# -------------------------------------------------------------------- numbers
check("zero", bu._num(0.0) == u"0")
check("four digits", bu._num(1234.0) == u"1234" and bu._num(-12.34) == u"-12.34", bu._num(-12.34))
check("number below one", bu._num(0.01234) == u"0.01234", bu._num(0.01234))
check("big number", u"e" in bu._num(3.2e9), bu._num(3.2e9))
check("stress in MPa", bu.fmt_stress(2.5e8) == u"250.0 MPa", bu.fmt_stress(2.5e8))
check("no value", bu.fmt_stress(None) == u"– MPa")
check("parse with comma and point", bu.parse_number(" 12,5 ") == 12.5 and bu.parse_number("3.5") == 3.5 and
      bu.parse_number("x") is None)

# ------------------------------------------------------------------- warnings
codes = set()
for name in ("beam_model.py", "section_profiles.py", "section_stress.py", "beam_worker.py",
             "beam_gui.py"):
    with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
        text = f.read()
    codes.update(re.findall(r'_warn\([\w.]+, "(\w+)"', text))
    codes.update(re.findall(r'(?:ChainError|ModelError|SectionError|Failure)\("(\w+)"', text))
    codes.update(re.findall(r'"code": "(\w+)"', text))
codes.update(re.findall(r'"code": "(\w+)" if', text))
missing = sorted(c for c in codes if c not in bu._WARNINGS)
check("every code in the code has its text", not missing, missing)
same = sorted(k for k, v in bu._WARNINGS.items() if v[0] == v[1] or not v[0] or not v[1])
check("every text exists in two languages", not same, same)
sample = {"eid": 3, "s": 1.25, "degrees": 12.3456, "elements": [1, 2], "reason": "gravity_area",
          "name": "M", "what": "x", "material": "M", "percent": "2", "selection": "S",
          "variable": "torsion", "node": 7, "tag": "fixed", "length": 0.25, "outline_ivv": 1e-7,
          "outline_iww": 2e-7, "iyy": 3e-7, "izz": 4e-7}
for code in sorted(bu._WARNINGS):
    for lang in ("nl", "en"):
        bu.settings()["language"] = lang
        text = bu.warning_text(code, sample)
        if "{" in text or "?" in text:
            check("text of {0} complete".format(code), False, text)
bu.settings()["language"] = "en"
check("kink text has the place", u"1250" in bu.warning_text("kink", sample), bu.warning_text("kink", sample))
check("reason in words", u"gravity" in bu.warning_text("correction_skipped", sample))
check("unknown code is not lost", u"new_code" in bu.warning_text("new_code", {"a": 1}))
check("a code without data does not fail", u"?" in bu.warning_text("kink"))

print("{0} failed".format(len(FAILED)) if FAILED else "All checks OK")
sys.exit(1 if FAILED else 0)
