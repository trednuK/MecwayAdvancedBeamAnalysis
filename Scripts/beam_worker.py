# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# beam_worker.py
# The calculating half of the window: a CPython 3 process with numpy and scipy
# that the panel starts once and talks to over stdin and stdout (protocol in
# beam_utils). NOT in IronPython.
#
#   python beam_worker.py <log file>
#
# One request at a time; the panel sends only the newest slider position. The
# process stops when stdin ends, so also when Mecway disappears. Nothing but
# protocol bytes may reach the pipe: fd 1 and 2 go to the log before anything
# heavy is imported, and the protocol uses a private copy of fd 1.
#
# Commands (JSON; every reply has "ok", a failure "code" and optional "data"):
#   scan    {path}   analysis, solver, configurations from the head of a file
#   open    {path, element_ids, raw, route, api, aster, gen}
#           read the model, build the chain and the section fields
#   forces  {raw, route, gen}   another result set, same beam
#   frame   {s, measure, on, shear_centre, width, height, view, coarse, scale,
#            arrows, contours, ui_scale, gen, frame}    one picture
#           with sheet {n} and band: n sections evenly along the chain, as one
#           picture with one colour scale
#           with sheet {n} and mode "3d", camera (dict, absent: the default
#           camera), z_top (Mecway's up axis is Z, for the default camera; absent:
#           Y) and magnify (absent: automatic): the same sections on their
#           place along the chain, drawn by beam_render3d. The reply has the tiles
#           (number, s, eid, outside, peak, anchor), camera (normalised, to keep),
#           magnify, magnify_auto and axes. Coarse is allowed here.
#   probe   {x, y, frame, gen}  the values under a pixel of the last picture; in
#           3D number and s, and the values unless the section is seen edge-on
#           (then hit is False, as beside the material of a 2D tile)
#
# What the 3D view expects of beam_render3d:
#   Camera(azimuth, elevation, up, target, zoom, pan, width, height),
#   Camera.from_dict(d, width, height), camera.as_dict() (finite numbers, JSON
#   after numpy values become lists), optionally camera.pixels_per_unit (pixels
#   per metre; without it the worker estimates it from zoom, for sharpness only);
#   default_camera(chain_points (n, 3), half_size, width, height, z_top=bool),
#   half_size the largest magnified section's reach from its centroid;
#   Placement(number, s, origin, v_axis, w_axis, magnify): origin the centroid on
#   the chain, V and W the drawn axes (mirrored for a reversed element);
#   Composer3D(width, height).render(items, camera, coarse), items
#   [(placement, texture)]; a texture is no beam_render.Scene but has its frame,
#   width, height, x0, y1, s, index, pixel_of and value_at. render returns
#   {"frame": uint32 BGRA (height, width) or flat, full size (a coarse one may be
#   about half), "anchors": per item in item order or {number: (x, y)}, "axes",
#   "times", "owner"}; probe(x, y) in full-size pixels, also after a coarse
#   render -> {"number", "v", "w"} with v, w unmagnified in the drawn axes,
#   {"number"} edge-on, or None.
#   node_maxima {on, shear_centre, gen}
#           per node of the chain the largest value outside the corners, in Pa, for
#           the variables of beam_utils.RESULT_VARS; on a shared node the largest of
#           the two sides. {values: {variable: {nid: value}}}
#
# A failing command answers with a code, its traceback goes to the log, and
# the worker lives on.

import math
import os
import sys
import time
import traceback

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import beam_utils as bu  # noqa: E402

COARSE_MIN = 8
MAX_SCENES = 4
MAX_TILES = 12
# The marker in Mecway: at least this part of the chain length, half width
MARKER_OF_LENGTH = 50.0
# Eccentric shear centre, as a fraction of sqrt(A): below this it is rounding.
ECCENTRIC = 0.01
PROPS_TOLERANCE = 0.01
# A section carries a bimoment this many 1/k from a clamp or load introduction,
# measured on solid models (research note par. 5.6) ...
WARPING_ZONE = 4.0
WARPING_OPEN = 0.5          # 1/k above this part of the section size: open
# ... which is worth a message when it is more than this part of the chain.
WARPING_PART = 0.05
# 3D view: magnification of the sections (research note 02, par. 5) ...
MAGNIFY_MIN = 1.0
MAGNIFY_MAX = 100.0
# ... textures: the long side in pixels, a power of two from the size on the
# screen, and the bytes the 3D slot keeps beyond the current frame (a texture
# scene holds about 20 to 40 bytes per pixel).
TEXTURE_MIN = 64
TEXTURE_MAX = 1024
TEXTURE_BUDGET = 64 * 1024 * 1024
# a texture pixel this far from the probed point still gives the value
PROBE_REACH = 1


class Failure(Exception):
    """A failure the window can explain: code and data for warning_text."""

    def __init__(self, code, data=None):
        Exception.__init__(self, code)
        self.code = code
        self.data = data or {}


def side_view(points):
    """2D polyline of the chain: the 3D points on their best-fit plane, first
    axis along the chain. A straight chain has y = 0."""
    import numpy as np
    p = np.asarray(points, dtype=float)
    if len(p) < 2:
        return [[0.0, 0.0] for _ in p]
    c = p - p.mean(axis=0)
    _, _, vt = np.linalg.svd(c, full_matrices=False)
    xy = c.dot(vt[:2].T) if vt.shape[0] >= 2 else np.column_stack(
        [c.dot(vt[0]), np.zeros(len(c))])
    if xy[-1, 0] < xy[0, 0]:
        xy = xy * np.array([-1.0, 1.0])
    return xy.tolist()


def _plain(data):
    """Warning data as JSON values."""
    out = {}
    for key, value in (data or {}).items():
        if isinstance(value, (set, tuple)):
            value = sorted(value)
        elif not isinstance(value, (int, float, str, list, dict, type(None))):
            value = str(value)
        out[key] = value
    return out


def _jsonable(value):
    """numpy scalars and arrays in a reply as plain JSON values."""
    if isinstance(value, dict):
        return dict((str(k), _jsonable(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "tolist"):
        return _jsonable(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _full_frame(frame, width, height, coarse):
    """The picture of the composer as width x height uint32 pixels, contiguous.
    Only a coarse picture may be smaller, as (rows, columns) of about half the
    size; it is stretched by nearest pixel. Anything else is an error."""
    import numpy as np
    f = np.asarray(frame)
    if f.dtype != np.uint32:
        raise ValueError("picture of type {0}, not uint32".format(f.dtype))
    if f.ndim == 2 and f.shape != (height, width) and coarse and \
            0 < f.shape[0] <= height and 0 < f.shape[1] <= width:
        rows = np.minimum(np.arange(height) * f.shape[0] // height, f.shape[0] - 1)
        cols = np.minimum(np.arange(width) * f.shape[1] // width, f.shape[1] - 1)
        f = f[rows[:, None], cols]
    if f.shape not in ((height, width), (width * height,)):
        raise ValueError("picture {0} for {1} x {2}".format(f.shape, width, height))
    return np.ascontiguousarray(f).reshape(-1)


def _anchors(anchors, placed):
    """(number, anchor) of every placed section: anchors keyed by number, or a
    list in the order of the items; another length is an error, not a guess."""
    if anchors is None:
        return []
    if isinstance(anchors, dict):
        return [(int(k), a) for k, a in anchors.items()]
    anchors = list(anchors)
    if len(anchors) != len(placed):
        raise ValueError("{0} anchors for {1} sections".format(len(anchors), len(placed)))
    return [(p.number, a) for (p, _), a in zip(placed, anchors)]


def _finite(value):
    """True when every number in a JSON-like value is finite."""
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    if hasattr(value, "tolist"):
        return _finite(value.tolist())
    if isinstance(value, float):
        return math.isfinite(value)
    return True


def _nbytes(scene):
    """Bytes of the arrays a texture scene keeps."""
    total = 0
    for value in scene.__dict__.values():
        if hasattr(value, "nbytes"):
            total += int(value.nbytes)
        elif hasattr(value, "data") and hasattr(value, "indices"):        # sparse
            total += int(value.data.nbytes + value.indices.nbytes + value.indptr.nbytes)
        elif isinstance(value, tuple):
            total += sum(int(v.nbytes) for v in value if hasattr(v, "nbytes"))
    return total


def _texture_value(scene, v, w):
    """(node numbers, weights) of the material at (v, w) of a texture: the pixel
    there, else the nearest within PROBE_REACH pixels (the edge of a pixel)."""
    x, y = scene.pixel_of(v, w)
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    i, j = int(math.floor(x)), int(math.floor(y))
    around = [(di, dj) for di in range(-PROBE_REACH, PROBE_REACH + 1)
              for dj in range(-PROBE_REACH, PROBE_REACH + 1)]
    around.sort(key=lambda d: (i + d[0] + 0.5 - x) ** 2 + (j + d[1] + 0.5 - y) ** 2)
    for di, dj in around:
        found = scene.value_at(i + di, j + dj)
        if found is not None:
            return found
    return None


class Texture(object):
    """A painted tile scene for the 3D view: its own copy of the frame, everything
    else shared with the scene, which a later tile of the same section repaints."""

    def __init__(self, scene, copy=True):
        self.scene = scene
        self.frame = scene.frame.copy() if copy else scene.frame

    def __getattr__(self, name):
        if name == "scene":             # not yet set: copy, pickle
            raise AttributeError(name)
        return getattr(self.scene, name)


class Section(object):
    """Field, unit fields and the failure, if any, of one section key."""

    def __init__(self, key, name):
        self.key = key
        self.name = name
        self.field = None
        self.units = None
        self.loops = None
        self.points = None
        self.failure = None


def _thick(rq):
    """Line width in device pixels."""
    return max(int(round(float(rq.get("ui_scale", 1.0)))), 1)


def sec_bounds(sec):
    import beam_render as br
    return br.loop_bounds(sec.loops)


def section_box(sec):
    """Box of the outline with the points G and S: the shear centre of an open
    section lies outside the outline, and must not fall off the picture."""
    vmin, wmin, vmax, wmax = sec_bounds(sec)
    for p in sec.points.values():
        vmin, wmin = min(vmin, p[0]), min(wmin, p[1])
        vmax, wmax = max(vmax, p[0]), max(wmax, p[1])
    return vmin, wmin, vmax, wmax


def marker_parts(model):
    """(nodes, elements) of the temporary marker in a saved file: a save with
    the option on writes it, and the API counts leave it out, so both sides must."""
    elements, nodes = 0, set()
    for element in model.elements.values():
        mat = model.materials.get(element.material)
        if bu.is_marker_name(element.material) and mat is not None and \
                mat.geometric.get("type") == "Plate":
            elements += 1
            nodes.update(element.nodes)
    return len(nodes), elements


def compare_with_api(model, api):
    """What differs between the saved file and the model in Mecway, as text
    with numbers, or None. Counts without the marker, then the coordinates of
    the chain."""
    if not api:
        return None
    nodes = model.nodes
    n_marker, e_marker = marker_parts(model)
    n_nodes, n_elements = len(nodes) - n_marker, len(model.elements) - e_marker
    if api.get("n_nodes") is not None and api["n_nodes"] != n_nodes:
        return u"{0} / {1} nodes".format(api["n_nodes"], n_nodes)
    if api.get("n_elements") is not None and api["n_elements"] != n_elements:
        return u"{0} / {1} elements".format(api["n_elements"], n_elements)
    for nid, xyz in (api.get("nodes") or {}).items():
        have = nodes.get(int(nid))
        if have is None:
            return u"node {0}".format(nid)
        if max(abs(float(a) - float(b)) for a, b in zip(xyz, have)) > 1e-9:
            return u"node {0}".format(nid)
    return None


class Session(object):
    """The state of the worker: one beam, its sections and forces."""

    def __init__(self):
        self.model = None
        self.chain = None
        self.raw = None
        self.route = "element"
        self.sections = {}
        self.scenes = {}
        self.scene_order = []
        self.last = None
        self.side = None
        self.out = None
        self.tile_size = None
        self.tile_scenes = {}
        self.aster = False
        self.points = None              # the chain in 3D, in chain order
        self.raw_version = 0            # counts the result sets of this beam
        self.sheet_items = None         # (key, items) of the last sheet
        # the 3D slot: textures apart from the 2D scenes, and one composer
        self.textures = {}
        self.texture_order = []
        self.composer = None
        self.cameras_refused = set()

    # ------------------------------------------------------------ commands
    def handle(self, request):
        """(reply, pixels or None, width, height) of one request."""
        try:
            return self._dispatch(request)
        except Exception as e:
            if type(e).__name__ in ("ChainError", "ModelError"):
                raise Failure(e.code, _plain(e.data))
            raise

    def _dispatch(self, request):
        cmd = request.get("cmd")
        if cmd == "scan":
            return {"ok": True, "cmd": "scan",
                    "head": bu.scan_head(request.get("path"))}, None, 0, 0
        if cmd == "open":
            return self.open(request), None, 0, 0
        if cmd == "forces":
            return self.forces(request), None, 0, 0
        if cmd == "frame":
            return self.frame(request)
        if cmd == "probe":
            return self.probe(request), None, 0, 0
        if cmd == "node_maxima":
            return self.node_maxima(request), None, 0, 0
        raise Failure("unknown_command", {"what": u"{0}".format(cmd)})

    def open(self, job):
        import beam_model as bm
        self.__init__()
        path = job.get("path")
        if not path or not os.path.isfile(path):
            raise Failure("no_file")
        model = bm.read_model(path, job.get("configuration"))
        differs = compare_with_api(model, job.get("api"))
        if differs:
            raise Failure("file_differs", {"what": differs})
        self.aster = bool(job.get("aster"))
        if self.aster:
            # the bridge takes a model with solver="ccx" too: the results decide
            model.solver = bm.ASTER
        chain = bm.build_chain(model, job["element_ids"])
        self.model, self.chain = model, chain
        self.set_raw(job)
        warnings = [self._warning(w) for w in chain.warnings]
        keys = []
        for element in chain.elements:
            key = bm.section_key(element)
            if key not in self.sections:
                self.sections[key] = self._build_section(key, element, job,
                                                         warnings)
                keys.append(key)
        for key in keys:
            sec = self.sections[key]
            if sec.failure:
                warnings.append(sec.failure)
        self.side = self._side()
        return {"ok": True, "cmd": "open", "gen": job.get("gen"),
                "length": float(chain.length), "n_elements": len(chain.elements),
                "side": self.side, "warnings": warnings,
                "measures": self._measures(),
                "elements": [[e.eid, float(e.s0), float(e.s1)]
                             for e in chain.elements]}

    def forces(self, job):
        self.set_raw(job)
        return {"ok": True, "cmd": "forces", "gen": job.get("gen"),
                "measures": self._measures()}

    def set_raw(self, job):
        raw = dict((int(k), v) for k, v in (job.get("raw") or {}).items())
        self.route = job.get("route", "element")
        if self.route == "kinematics":
            raw = self._from_kinematics(raw)
        self.raw = raw
        self.raw_version += 1
        self.sheet_items = None

    def _from_kinematics(self, raw):
        """Mecway-named end values from displacement and rotation, for sigma
        only: N, Mv and Mw (research note, fallback)."""
        import beam_model as bm
        return bm.forces_from_kinematics(self.chain, raw)

    def _warning(self, item):
        """A warning of beam_model as JSON."""
        return {"code": item["code"], "data": _plain(item.get("data"))}

    def _measures(self):
        """The measures every section of the chain can show."""
        names = [m[0] for m in bu.MEASURES]
        for sec in self.sections.values():
            if sec.units is None or not sec.units.has_tau:
                names = ["sigma"]
        return ["sigma"] if self.route == "kinematics" else names

    def _build_section(self, key, element, job, warnings):
        import beam_render as br
        import section_stress as ss
        import beam_model as bm
        sec = Section(key, u"{0}".format(element.material))
        try:
            spec = bm.section_spec(self.model, element, aster_offset=self.aster)
            field = ss.SectionField(spec["loops"], nu=spec["nu"],
                                    props=spec.get("props"),
                                    offset=spec.get("offset"))
        except ss.SectionError as e:
            sec.failure = {"code": e.code, "data": {"eid": element.eid,
                                                    "name": sec.name,
                                                    "what": u"{0}".format(e.detail)}}
            print("section {0}: {1}".format(sec.name, e))
            return sec
        sec.field = field
        sec.units = br.UnitFields(field)
        sec.loops = [l - field.centroid for l in field.loops_in]
        sec.points = {"G": (0.0, 0.0)}
        if field.shear_centre is not None:
            sec.points["S"] = tuple(float(x) for x in field.shear_centre)
        for code in field.warnings:
            warnings.append({"code": code, "data": None})
        self._check_section(sec, element, warnings)
        return sec

    def _check_offset(self, sec, element, warnings):
        """#ASTER-SECTIE against the shape: which of the two counts, when they differ
        (research note par. 5.2). In the drawn axes, as the field."""
        extra = element.aster_offset
        field = sec.field
        if extra is None or field.shear_centre is None:
            return
        sv, sw = element.signs
        d = math.hypot(sv * extra[0] + field.shear_centre[0],
                       sw * extra[1] + field.shear_centre[1])
        if d > ECCENTRIC * math.sqrt(field.mesh_props["A"]):
            warnings.append({"code": "aster_offset_used" if self.aster else "aster_offset_unused",
                             "data": {"name": sec.name, "length": float(d)}})

    def _check_section(self, sec, element, warnings):
        field = sec.field
        area = field.mesh_props["A"]
        worst = max(abs(field.props[k] / field.mesh_props[k] - 1.0)
                    for k in ("A", "ivv", "iww"))
        inertia = any(w["code"] == "shape_inertia" and w["data"]["material"] == element.material
                      for w in self.chain.warnings)
        if worst > PROPS_TOLERANCE and not inertia:
            warnings.append({"code": "props_differ",
                             "data": {"name": sec.name,
                                      "percent": u"{0:.1f}".format(100.0 * worst)}})
        if field.offset is not None and self.chain.solver is None:
            if math.hypot(*field.offset) > ECCENTRIC * math.sqrt(area):
                warnings.append({"code": "eccentric_shear_centre", "data": None})
        self._check_offset(sec, element, warnings)
        if field.warping_length is not None:
            zone = WARPING_ZONE * field.warping_length
            size = float((field.xy.max(axis=0) - field.xy.min(axis=0)).max())
            # A compact section has no bimoment worth the name: its 1/k is a
            # fraction of the section itself, the local end effect is known.
            if (zone > WARPING_PART * float(self.chain.length) and
                    field.warping_length > WARPING_OPEN * size):
                warnings.append({"code": "warping_zone",
                                 "data": {"name": sec.name, "length": float(zone)}})
        if field.corners:
            warnings.append({"code": "corner_peak", "data": None})

    def _side(self):
        """Polyline and arc lengths of the chain, in chain order; the file and
        the API were compared before."""
        nodes = self.model.nodes
        points, arc = [], []
        for i, e in enumerate(self.chain.elements):
            first, second = e.nodes[0], e.nodes[-1]
            if e.reversed:
                first, second = second, first
            if i == 0:
                points.append(nodes[first])
                arc.append(float(e.s0))
            points.append(nodes[second])
            arc.append(float(e.s1))
        self.points = [[float(c) for c in p] for p in points]
        return {"points": side_view(points), "s": arc}

    # ---------------------------------------------------------------- frame
    def _section_at(self, s):
        """(forces_at result, element, Section) at arc length s."""
        import beam_model as bm
        at = bm.forces_at(self.chain, self.raw, s)
        return at, at["element"], self.sections[bm.section_key(at["element"])]

    def _scene(self, sec, width, height, view, thick, coarse):
        import beam_render as br
        view_key = None if not view else (view.get("zoom"), view.get("cx"),
                                          view.get("cy"))
        key = (sec.key, width, height, view_key, thick, coarse)
        scene = self.scenes.get(key)
        if scene is not None:
            self.scene_order.remove(key)
            self.scene_order.append(key)
            return scene
        field = sec.field
        scene = br.Scene(field.xy, field.elements, sec.loops, width, height,
                         view, thick, sec.points,
                         margin=br.MARGIN / br.COARSE if coarse else br.MARGIN,
                         bounds=section_box(sec))
        self.scenes[key] = scene
        self.scene_order.append(key)
        while len(self.scene_order) > MAX_SCENES:
            del self.scenes[self.scene_order.pop(0)]
        return scene

    def _evaluate(self, s, rq):
        """One section at arc length s: (numbers for the reply, arrays for the
        picture or None without a section, (element, Section)). The arrays
        belong to UnitFields and are reused by the next call."""
        import beam_render as br
        import section_stress as ss
        at, element, sec = self._section_at(s)
        forces = at["forces"]
        on = rq.get("on") or {}
        masked = dict((k, float(forces.get(k, 0.0)) if on.get(k, True) else 0.0)
                      for k in bu.FORCES)
        info = {"s": s, "eid": element.eid, "xi": float(at.get("xi", 0.0)),
                "forces": dict((k, float(forces.get(k, 0.0))) for k in bu.FORCES),
                "notes": [self._warning(n) for n in at.get("warnings") or []]}
        if sec.field is None:
            info["no_section"] = sec.failure
            return info, None, (element, sec)
        field, units = sec.field, sec.units
        convert = bool(rq.get("shear_centre", True))
        _, term = field.torque_at_shear_centre(masked)
        ts = masked["T"] + (term if convert else 0.0)
        info.update({"T": masked["T"], "term": term, "T_S": ts, "converted": convert})
        sn, tv, tw = units.nodal([masked["N"], masked["Vv"], masked["Vw"], ts,
                                  masked["Mv"], masked["Mw"]])
        values = ss.measures({"sigma": sn, "tau_v": tv, "tau_w": tw})
        name = rq.get("measure", "sigma")
        if name not in values or self.route == "kinematics":
            name = "sigma"
        q = values[name]
        info["measure"] = name
        # the numbers come from the core, on the element nodes
        core = ss.measures(field.stress(masked, about_shear_centre=not convert))
        ext = field.extremes(core[name])
        info["outside"] = ext["outside"]
        info["overall"] = ext["overall"]
        info["outside_empty"] = bool(ext.get("outside_empty"))
        markers = []
        if not info["outside_empty"]:
            markers.append((ext["outside"]["point"], "max"))
        if field.corners and abs(ext["overall"]["value"]) > abs(ext["outside"]["value"]):
            markers.append((ext["overall"]["point"], "peak"))
            info["peak"] = ext["overall"]
        lo, hi = br.scale_range(q, units.outside)
        return info, {"sec": sec, "q": q, "sigma": sn, "tau_v": tv, "tau_w": tw,
                      "markers": markers, "lo": lo, "hi": hi}, (element, sec)

    def _chain_point(self, element, s):
        """The point of the chain line at arc length s, on that element."""
        nodes = self.model.nodes
        a, b = nodes[element.nodes[0]], nodes[element.nodes[-1]]
        if element.reversed:
            a, b = b, a
        t = (s - element.s0) / element.length
        return [p + t * (q - p) for p, q in zip(a, b)]

    def _place(self, element, sec, s):
        """Where the position sits in the model, for the marker in Mecway."""
        point = self._chain_point(element, s)
        size = float(self.chain.length) / MARKER_OF_LENGTH
        if sec.loops is not None:
            bounds = sec_bounds(sec)
            size = max(size, 0.75 * max(bounds[2] - bounds[0], bounds[3] - bounds[1]))
        return {"point": point, "v": list(element.frame[1]),
                "w": list(element.frame[2]), "half": size}

    def frame(self, rq):
        import numpy as np
        import beam_render as br
        t0 = time.perf_counter()
        if self.chain is None:
            raise Failure("not_open")
        if rq.get("sheet"):
            return self.sheet(rq)
        s = min(max(float(rq.get("s", 0.0)), 0.0), float(self.chain.length))
        info, data, where = self._evaluate(s, rq)
        info.update({"ok": True, "cmd": "frame", "gen": rq.get("gen"),
                     "frame": rq.get("frame"), "coarse": bool(rq.get("coarse")),
                     "place": self._place(where[0], where[1], s)})
        if data is None:
            self.last = None
            return info, None, 0, 0
        sec, q, tv, tw = data["sec"], data["q"], data["tau_v"], data["tau_w"]
        if rq.get("scale"):
            lo, hi = float(rq["scale"][0]), float(rq["scale"][1])
        else:
            lo, hi = data["lo"], data["hi"]
        info["lo"], info["hi"] = lo, hi
        info["clip_lo"] = bool(float(q.min()) < lo)
        info["clip_hi"] = bool(float(q.max()) > hi)
        width = max(int(rq.get("width", 64)), 4)
        height = max(int(rq.get("height", 64)), 4)
        view = rq.get("view")
        arrows = rq.get("arrows") or {}
        draw_tau = bool(arrows.get("on", True)) and tv is not None
        coarse = bool(rq.get("coarse"))
        t1 = time.perf_counter()
        if coarse:
            w2 = max(width // br.COARSE, COARSE_MIN)
            h2 = max(height // br.COARSE, COARSE_MIN)
            scene = self._scene(sec, w2, h2, view, 1, True)
        else:
            scene = self._scene(sec, width, height, view, _thick(rq), False)
        t2 = time.perf_counter()
        # a coarse frame is a stopgap, its lines would be four pixels wide
        top = scene.paint(q, lo, hi, tv if draw_tau else None,
                          tw if draw_tau else None,
                          float(arrows.get("length", 1.0)), data["markers"],
                          contours=bool(rq.get("contours")) and not coarse)
        info["arrow_max"] = top if draw_tau else None
        if coarse:
            if self.out is None or self.out.size != width * height:
                self.out = np.zeros(width * height, dtype=np.uint32)
            br.upscale(scene.frame.reshape(scene.height, scene.width), width,
                       height, self.out)
            pixels = self.out
        else:
            pixels = scene.frame
        x0, y1, sc = br.make_view(scene.bounds, width, height, view)
        info["view"] = {"x0": x0, "y1": y1, "s": sc,
                        "zoom": float((view or {}).get("zoom", 1.0))}
        self.last = {"frame": rq.get("frame"), "scene": scene, "q": q,
                     "sigma": data["sigma"], "tau_v": tv, "tau_w": tw, "x0": x0,
                     "y1": y1, "s": sc, "div": br.COARSE if coarse else 1,
                     "left": 0, "top": 0}
        t3 = time.perf_counter()
        info["ms"] = {"compute": (t1 - t0) * 1e3, "map": (t2 - t1) * 1e3,
                      "paint": (t3 - t2) * 1e3}
        return info, pixels, width, height

    def _sheet_items(self, rq):
        """(n, [(info, data)]) of the n sections evenly along the chain; the
        arrays copied, since the next section reuses those of UnitFields. Kept
        for the next sheet with the same forces and choices: turning the 3D view
        does not evaluate again. Nothing downstream changes them."""
        import json
        n = min(max(int(rq["sheet"].get("n", 2)), 2), MAX_TILES)
        key = (n, self.raw_version, rq.get("measure", "sigma"),
               json.dumps(rq.get("on") or {}, sort_keys=True),
               bool(rq.get("shear_centre", True)))
        if self.sheet_items is not None and self.sheet_items[0] == key:
            return n, self.sheet_items[1]
        items = self._evaluate_sheet(rq, n)
        self.sheet_items = (key, items)
        return n, items

    def _evaluate_sheet(self, rq, n):
        length = float(self.chain.length)
        items = []
        for i in range(n):
            s = length * i / (n - 1)
            info, data, _ = self._evaluate(s, rq)
            if data is not None:
                for key in ("q", "sigma", "tau_v", "tau_w"):
                    if data[key] is not None:
                        data[key] = data[key].copy()
            items.append((info, data))
        return items

    def _sheet_head(self, rq, items):
        """The reply of a sheet without its tiles: notes and the one colour
        scale over all sections (the fixed one of the request, if any)."""
        drawn = [d for _, d in items if d is not None]
        info = {"ok": True, "cmd": "frame", "gen": rq.get("gen"), "frame": rq.get("frame"),
                "coarse": False, "sheet": True, "notes": []}
        for item, _ in items:
            for note in item["notes"]:
                if note not in info["notes"]:
                    info["notes"].append(note)
        if rq.get("scale"):
            lo, hi = float(rq["scale"][0]), float(rq["scale"][1])
        else:
            lo = min([d["lo"] for d in drawn] or [0.0])
            hi = max([d["hi"] for d in drawn] or [1.0])
        info["lo"], info["hi"] = lo, hi
        info["clip_lo"] = any(float(d["q"].min()) < lo for d in drawn)
        info["clip_hi"] = any(float(d["q"].max()) > hi for d in drawn)
        return info

    def _sheet_summary(self, info, items):
        """Measure, largest value outside the corners and the peak over all tiles."""
        evaluated = [i for i, d in items if d is not None]
        if evaluated:
            info["measure"] = evaluated[0]["measure"]
            info["outside"] = max((i["outside"] for i in evaluated),
                                  key=lambda v: abs(v["value"]))
            info["outside_empty"] = all(i["outside_empty"] for i in evaluated)
            peaks = [i["peak"] for i in evaluated if "peak" in i]
            if peaks:
                top = max(peaks, key=lambda v: abs(v["value"]))
                # a lower peak in a corner of a quieter section is no headline
                if abs(top["value"]) > abs(info["outside"]["value"]):
                    info["peak"] = top

    def sheet(self, rq):
        """Sections evenly along the chain as one picture, on one colour scale
        and with one arrow length, at one scale of length."""
        import numpy as np
        import beam_render as br
        t0 = time.perf_counter()
        n, items = self._sheet_items(rq)
        if rq.get("mode") == "3d":
            return self.sheet3d(rq, n, items, t0)
        drawn = [d for _, d in items if d is not None]
        info = self._sheet_head(rq, items)
        lo, hi = info["lo"], info["hi"]
        width = max(int(rq.get("width", 64)), 4)
        height = max(int(rq.get("height", 64)), 4)
        if self.out is None or self.out.size != width * height:
            self.out = np.zeros(width * height, dtype=np.uint32)
        self.out[:] = br.BACKGROUND
        canvas = self.out.reshape(height, width)
        band = int(rq.get("band", 20))
        bounds = self._sheet_bounds(drawn)
        cols, rows, tw, th = br.tile_grid(n, width, height, bounds, band)
        arrows = rq.get("arrows") or {}
        draw_tau = bool(arrows.get("on", True)) and any(d["tau_v"] is not None for d in drawn)
        thick = _thick(rq)
        scenes = [self._tile_scene(d["sec"], tw, th - band, thick, bounds) if d else None
                  for _, d in items]
        reference = None
        if draw_tau:
            reference = max(scene.arrow_top(d["tau_v"], d["tau_w"])
                            for (_, d), scene in zip(items, scenes) if d is not None)
        tiles, last = [], []
        for i, (item, data) in enumerate(items):
            x, y = (i % cols) * tw, (i // cols) * th
            tile = {"number": i + 1, "x": x, "y": y, "w": tw, "h": th, "s": item["s"],
                    "eid": item["eid"], "band": band}
            if data is None:
                tile["no_section"] = item["no_section"]
                tiles.append(tile)
                last.append({"tile": (x, y + band, tw, th - band), "scene": None,
                             "s": item["s"], "number": i + 1})
                continue
            scene = scenes[i]
            tv = data["tau_v"] if draw_tau else None
            tw_ = data["tau_w"] if draw_tau else None
            scene.paint(data["q"], lo, hi, tv, tw_, float(arrows.get("length", 1.0)),
                        data["markers"], reference, contours=bool(rq.get("contours")))
            canvas[y + band:y + th, x:x + tw] = scene.frame.reshape(scene.height, scene.width)
            tile["outside"] = item["outside"]["value"]
            tile["peak"] = item.get("peak", {}).get("value")
            tiles.append(tile)
            last.append({"tile": (x, y + band, tw, th - band), "scene": scene, "s": item["s"],
                         "number": i + 1,
                         "q": data["q"], "sigma": data["sigma"], "tau_v": data["tau_v"],
                         "tau_w": data["tau_w"]})
        info["tiles"] = tiles
        info["n"] = n
        self._sheet_summary(info, items)
        info["arrow_max"] = reference
        self.last = {"frame": rq.get("frame"), "tiles": last}
        info["ms"] = {"compute": 0.0, "map": 0.0,
                      "paint": (time.perf_counter() - t0) * 1e3}
        return info, self.out, width, height

    def _sheet_bounds(self, drawn):
        """One box around the sections, so that one scale of length holds."""
        boxes = [section_box(d["sec"]) for d in drawn]
        if not boxes:
            return (-1.0, -1.0, 1.0, 1.0)
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _tile_scene(self, sec, width, height, thick, bounds):
        """Tile scenes live in their own cache, emptied when the size changes:
        a sheet needs one per section of the chain, on every frame."""
        import beam_render as br
        size = (width, height, thick, bounds)
        if self.tile_size != size:
            self.tile_size = size
            self.tile_scenes = {}
        scene = self.tile_scenes.get(sec.key)
        if scene is None:
            field = sec.field
            scene = br.Scene(field.xy, field.elements, sec.loops, width, height, None,
                             thick, sec.points, axes_at_corner=False,
                             margin=br.TILE_MARGIN, bounds=bounds)
            self.tile_scenes[sec.key] = scene
        return scene

    # ------------------------------------------------------------------ 3D
    def sheet3d(self, rq, n, items, t0):
        """The sections of a sheet on their place along the chain (research note
        02, par. 5): tile scenes as in 2D, painted on the same colour scale with
        contour lines, outline and markers but without arrows, as textures for
        beam_render3d (interface in the file header). Coarse: half the texture and
        no contour lines."""
        import beam_render3d as b3
        info = self._sheet_head(rq, items)
        coarse = bool(rq.get("coarse"))
        info["mode"] = "3d"
        info["coarse"] = coarse
        lo, hi = info["lo"], info["hi"]
        width = max(int(rq.get("width", 64)), 4)
        height = max(int(rq.get("height", 64)), 4)
        drawn = [d for _, d in items if d is not None]
        # the texture holds G and S too; the size of a section is its outline
        boxes = dict((d["sec"].key, section_box(d["sec"])) for d in drawn)
        outlines = [sec_bounds(d["sec"]) for d in drawn]
        size = max([max(b[2] - b[0], b[3] - b[1]) for b in outlines] or [0.0])
        radius = max([max(abs(x) for x in b) for b in boxes.values()] or [0.0])
        magnify, auto = self._magnify(rq, n, size)
        camera = self._camera(rq, b3, magnify * radius, width, height)
        scale = self._screen_scale(camera, magnify * radius, width, height)
        thick = _thick(rq)
        contours = bool(rq.get("contours")) and not coarse
        t1 = time.perf_counter()
        plan = []
        for i, (item, data) in enumerate(items):
            key = None
            if data is not None:
                box = boxes[data["sec"].key]
                tw, th = self._texture_size(box, magnify * scale, coarse)
                key = (data["sec"].key, tw, th, thick)
            plan.append(key)
        shared = set(k for k in plan if k is not None and plan.count(k) > 1)
        placed, tiles, last = [], [], {}
        for i, ((item, data), key) in enumerate(zip(items, plan)):
            number = i + 1
            tile = {"number": number, "s": item["s"], "eid": item["eid"], "anchor": None}
            tiles.append(tile)
            if data is None:
                tile["no_section"] = item["no_section"]
                continue
            sec = data["sec"]
            scene = self._texture(sec, key, boxes[sec.key])
            scene.paint(data["q"], lo, hi, None, None, 1.0, data["markers"], None,
                        contours=contours)
            # a later tile of the same section repaints the scene
            texture = Texture(scene, key in shared)
            element, _ = self.chain.element_at(item["s"])
            _, v_axis, w_axis = element.frame
            centre = sec.field.centroid
            origin = [p + float(centre[0]) * a + float(centre[1]) * b
                      for p, a, b in zip(self._chain_point(element, item["s"]), v_axis, w_axis)]
            placed.append((b3.Placement(number, item["s"], origin, list(v_axis),
                                        list(w_axis), magnify), texture))
            tile["outside"] = item["outside"]["value"]
            tile["peak"] = item.get("peak", {}).get("value")
            last[number] = {"texture": texture, "s": item["s"], "q": data["q"],
                            "sigma": data["sigma"], "tau_v": data["tau_v"],
                            "tau_w": data["tau_w"]}
        self._trim_textures(set(k for k in plan if k is not None))
        t2 = time.perf_counter()
        self.last = None                # a failure below leaves no stale picture
        composer = self._composer(b3, width, height)
        import numpy as np
        out = composer.render(placed, camera, coarse,
                              chain=np.asarray(self.points, dtype=float))
        pixels = _full_frame(out["frame"], width, height, coarse)
        for number, anchor in _anchors(out.get("anchors"), placed):
            anchor = _jsonable(list(anchor)[:2]) if anchor is not None else None
            if anchor and len(anchor) == 2 and all(isinstance(c, (int, float)) for c in anchor):
                tiles[number - 1]["anchor"] = [float(c) for c in anchor]
        t3 = time.perf_counter()
        info["tiles"] = tiles
        info["n"] = n
        self._sheet_summary(info, items)
        info["arrow_max"] = None
        info["camera"] = camera.as_dict()
        info["magnify"] = magnify
        info["magnify_auto"] = auto
        info["axes"] = out.get("axes")
        info["ms"] = {"compute": (t1 - t0) * 1e3, "map": (t2 - t1) * 1e3,
                      "paint": (t3 - t2) * 1e3, "render": out.get("times")}
        self.last = {"frame": rq.get("frame"), "composer": composer, "size": (width, height),
                     "tiles3d": last}
        return _jsonable(info), pixels, width, height

    def _magnify(self, rq, n, size):
        """(factor, automatic) of the sections in 3D: the one asked, or so that the
        largest section is about 1/(n+1) of the chain; within 1..100."""
        value = rq.get("magnify")
        if value is not None and not isinstance(value, bool):
            try:
                factor = float(value)
            except (TypeError, ValueError):
                factor = None
            if factor is not None and math.isfinite(factor):
                return min(max(factor, MAGNIFY_MIN), MAGNIFY_MAX), False
        if not size > 0.0:
            return MAGNIFY_MIN, True
        factor = float(self.chain.length) / (n + 1) / size
        return min(max(factor, MAGNIFY_MIN), MAGNIFY_MAX), True

    def _camera(self, rq, b3, half_size, width, height):
        """The camera of the request, or the default one when there is none, it
        cannot be read or it is not finite. Said once per camera in the log."""
        import json
        import numpy as np
        wanted = rq.get("camera")
        if isinstance(wanted, dict):
            why = None
            try:
                camera = b3.Camera.from_dict(wanted, width, height)
                if _finite(camera.as_dict()):
                    return camera
                why = "not finite"
            except Exception as e:
                why = repr(e)
            text = json.dumps(wanted, sort_keys=True, default=str)
            if text not in self.cameras_refused:
                self.cameras_refused.add(text)
                print("camera not read, default instead: {0}".format(why))
        return b3.default_camera(np.asarray(self.points, dtype=float), half_size,
                                 width, height, z_top=bool(rq.get("z_top", False)))

    def _screen_scale(self, camera, half_size, width, height):
        """Pixels per metre of the camera: its own pixels_per_unit when it has one,
        otherwise estimated from its zoom on the default fit (the chain and the
        magnified sections). Only the sharpness of the textures depends on it."""
        import numpy as np
        for name in ("pixels_per_unit", "scale"):
            try:
                value = float(getattr(camera, name))
            except (AttributeError, TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0.0:
                return value
        try:
            zoom = float(getattr(camera, "zoom", 1.0))
        except (TypeError, ValueError):
            zoom = 1.0
        if not (math.isfinite(zoom) and zoom > 0.0):
            zoom = 1.0
        p = np.asarray(self.points, dtype=float)
        reach = 0.5 * float(np.linalg.norm(p.max(axis=0) - p.min(axis=0))) + half_size
        return zoom * min(width, height) / (2.0 * max(reach, 1e-12))

    def _texture_size(self, box, pixels_per_unit, coarse):
        """(width, height) of a texture: the long side a power of two near the
        size of the section on the screen, the short side at the same scale, both
        with the margins of the scene."""
        import beam_render as br
        du = max(box[2] - box[0], 1e-12)
        dv = max(box[3] - box[1], 1e-12)
        on_screen = pixels_per_unit * max(du, dv)
        side = TEXTURE_MIN
        while side < on_screen and side < TEXTURE_MAX:
            side *= 2
        if coarse:
            side //= 2
        m = 2 * br.TILE_MARGIN
        scale = (side - m) / max(du, dv)
        # a thin section still keeps some pixels across
        short = max(int(round(scale * min(du, dv))) + m, m + 8)
        return (side, short) if du >= dv else (short, side)

    def _texture(self, sec, key, bounds):
        """A texture scene from the 3D slot: apart from the 2D scenes, so that
        turning the view does not push those out. key: (section, width, height,
        line width)."""
        import beam_render as br
        scene = self.textures.get(key)
        if scene is not None:
            self.texture_order.remove(key)
            self.texture_order.append(key)
            return scene
        field = sec.field
        _, width, height, thick = key
        scene = br.Scene(field.xy, field.elements, sec.loops, width, height, None, thick,
                         sec.points, axes_at_corner=False, margin=br.TILE_MARGIN,
                         bounds=bounds)
        self.textures[key] = scene
        self.texture_order.append(key)
        return scene

    def _trim_textures(self, used):
        """Forget the oldest textures beyond the budget in bytes, never one of this
        frame."""
        sizes = dict((k, _nbytes(self.textures[k])) for k in self.texture_order)
        total = sum(sizes.values())
        for key in list(self.texture_order):
            if total <= TEXTURE_BUDGET:
                break
            if key in used:
                continue
            self.texture_order.remove(key)
            del self.textures[key]
            total -= sizes[key]

    def _composer(self, b3, width, height):
        if self.composer is None or self.composer[0] != (width, height):
            self.composer = ((width, height), b3.Composer3D(width, height))
        return self.composer[1]

    def _probe3d(self, last, x, y, out):
        """Number and s of the section under the pixel, and its values unless it
        is seen edge-on."""
        width, height = last["size"]
        if not (0 <= x < width and 0 <= y < height):
            return out
        hit = last["composer"].probe(x, y)
        if not hit or hit.get("number") is None:
            return out
        try:
            number = int(hit["number"])
        except (TypeError, ValueError):
            return out
        tile = last["tiles3d"].get(number)
        if tile is None:
            return out
        out["number"] = number
        out["s"] = tile["s"]
        try:
            v, w = float(hit["v"]), float(hit["w"])
        except (KeyError, TypeError, ValueError):
            return out
        if not (math.isfinite(v) and math.isfinite(w)):
            return out
        found = _texture_value(tile["texture"], v, w)
        if found is None:
            return out
        nodes, weights = found

        def at(values):
            return None if values is None else float((values[nodes] * weights).sum())
        out.update({"hit": True, "v": v, "w": w, "value": at(tile["q"]),
                    "sigma": at(tile["sigma"]), "tau_v": at(tile["tau_v"]),
                    "tau_w": at(tile["tau_w"])})
        return _jsonable(out)

    def _stations(self):
        """(arc length just inside the element, node) of every node of every element."""
        out = []
        for e in self.chain.elements:
            nodes = list(e.nodes)
            if e.reversed:
                nodes.reverse()
            inside = 1.0e-9 * e.length
            out.append((e.s0 + inside, nodes[0]))
            if len(nodes) == 3:
                out.append((0.5 * (e.s0 + e.s1), nodes[1]))
            out.append((e.s1 - inside, nodes[-1]))
        return out

    def node_maxima(self, rq):
        import section_stress as ss
        if self.chain is None:
            raise Failure("not_open")
        on = rq.get("on") or {}
        convert = bool(rq.get("shear_centre", True))
        values = dict((name, {}) for _, name in bu.RESULT_VARS)
        for s, nid in self._stations():
            at, _, sec = self._section_at(s)
            if sec.field is None:
                continue
            forces = at["forces"]
            masked = dict((k, float(forces.get(k, 0.0)) if on.get(k, True) else 0.0)
                          for k in bu.FORCES)
            core = ss.measures(sec.field.stress(masked, about_shear_centre=not convert))
            sigma_only = self.route == "kinematics" or not sec.units.has_tau
            for key, name in bu.RESULT_VARS:
                if key not in core or (sigma_only and key != "sigma"):
                    continue
                ext = sec.field.extremes(core[key])
                pick = ext["overall"] if ext.get("outside_empty") else ext["outside"]
                here = values[name]
                here[str(nid)] = max(here.get(str(nid), 0.0), abs(float(pick["value"])))
        return {"ok": True, "cmd": "node_maxima", "gen": rq.get("gen"),
                "values": dict((k, v) for k, v in values.items() if v)}

    def probe(self, rq):
        last = self.last
        out = {"ok": True, "cmd": "probe", "gen": rq.get("gen"),
               "frame": rq.get("frame"), "hit": False}
        if last is None or last["frame"] != rq.get("frame"):
            return out
        x, y = int(rq.get("x", -1)), int(rq.get("y", -1))
        if "tiles3d" in last:
            return self._probe3d(last, x, y, out)
        if "tiles" in last:
            pick = None
            for tile in last["tiles"]:
                tx, ty, tw, th = tile["tile"]
                if tx <= x < tx + tw and ty <= y < ty + th:
                    pick = tile
                    x, y = x - tx, y - ty
                    break
            if pick is None:
                return out
            if pick["scene"] is None:
                out.update({"s": pick["s"], "number": pick["number"], "no_section": True})
                return out
            div = 1
            scene = pick["scene"]
            x0, y1, sc = scene.x0, scene.y1, scene.s
            out["s"] = pick["s"]
            out["number"] = pick["number"]
        else:
            pick = last
            div = last["div"]
            scene = last["scene"]
            x0, y1, sc = last["x0"], last["y1"], last["s"]
        hit = scene.value_at(x // div, y // div)
        if hit is None:
            return out
        nodes, weights = hit

        def at(values):
            return None if values is None else float((values[nodes] * weights).sum())
        out.update({"hit": True, "v": x0 + (x + 0.5) / sc, "w": y1 - (y + 0.5) / sc,
                    "value": at(pick["q"]), "sigma": at(pick["sigma"]),
                    "tau_v": at(pick["tau_v"]), "tau_w": at(pick["tau_w"])})
        return out


# ==========================================
# THE PIPE
# ==========================================
def read_exact(fd, n):
    """n bytes from fd, or None at the end of the stream."""
    parts = bytearray()
    while len(parts) < n:
        chunk = os.read(fd, n - len(parts))
        if not chunk:
            return None
        parts += chunk
    return bytes(parts)


def write_all(fd, data):
    view = memoryview(data)
    while len(view):
        view = view[os.write(fd, view):]


def serve(fd_in, fd_out):
    """Answer requests until stdin ends."""
    import json
    session = Session()
    first = True
    while True:
        head = read_exact(fd_in, bu.REQUEST.size)
        if head is None:
            return
        if first:
            # A console on code page 65001 makes .NET put a UTF-8 byte order mark
            # in front of stdin (measured): skip it, the protocol is binary.
            first = False
            if head[:3] == b"\xef\xbb\xbf":
                rest = read_exact(fd_in, 3)
                if rest is None:
                    return
                head = head[3:] + rest
        seq, n = bu.REQUEST.unpack(head)
        body = read_exact(fd_in, n)
        if body is None:
            return
        pixels, width, height = None, 0, 0
        try:
            request = json.loads(body.decode("utf-8"))
            reply, pixels, width, height = session.handle(request)
        except Failure as e:
            reply = {"ok": False, "code": e.code, "data": e.data}
            print("{0}: {1}".format(e.code, e.data))
        except Exception:
            traceback.print_exc()       # fd 2 is the worker's log
            reply = {"ok": False, "code": "internal", "data": None}
        write_all(fd_out, bu.pack_reply_head(seq, reply, width, height))
        if pixels is not None:
            write_all(fd_out, memoryview(pixels).cast("B"))


def redirect(path):
    """Protocol on private copies of the two descriptors; fd 0, 1 and 2 no longer
    touch the pipe, whatever a library prints. Before numpy is imported."""
    fd_in = os.dup(0)
    fd_out = os.dup(1)
    log = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    sys.stdout = sys.stderr = log
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    try:
        import msvcrt
        msvcrt.setmode(fd_in, os.O_BINARY)
        msvcrt.setmode(fd_out, os.O_BINARY)
    except ImportError:
        pass
    return fd_in, fd_out


def main(argv):
    path = argv[1] if len(argv) > 1 else os.devnull
    fd_in, fd_out = redirect(path)
    try:
        import numpy
        import scipy
        import beam_model  # noqa: F401
        import beam_render  # noqa: F401
        import section_stress  # noqa: F401
    except Exception:
        traceback.print_exc()
        write_all(fd_out, bu.pack_reply_head(0, {
            "ok": False, "cmd": "hello", "code": "import_failed",
            "data": {"what": traceback.format_exc(limit=1)}}))
        return 1
    write_all(fd_out, bu.pack_reply_head(0, {
        "ok": True, "cmd": "hello", "python": sys.version.split()[0],
        "numpy": numpy.__version__, "scipy": scipy.__version__,
        "pid": os.getpid()}))
    serve(fd_in, fd_out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
