# -*- coding: ascii -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Runs inside real, hidden Mecway (IronPython), started by run_e2e.py: the real
# beam_mecway, beam_gui and worker with the real mw and solution, on the threads
# of run_window. The form gets handles and is never shown. No modal API; every
# measured value goes to a JSON result, after every step, so that a killed run
# still tells how far it came.
import hashlib
import io
import json
import os
import sys
import time
import traceback

with open(os.environ["E2E_JOB"]) as _job:
    JOB = json.loads(_job.read())
FOLDER = JOB["folder"]
RESULT = {"case": JOB["case"], "errors": [], "steps": []}
CANVAS = (1240, 840)
VALUE_VARS = ("tensileforce", "sheary", "shearz", "torsion", "bendingmomv", "bendingmomw")
NODE_VARS = ("displx", "disply", "displz", "rotx", "roty", "rotz")


def save():
    text = json.dumps(RESULT, sort_keys=True, ensure_ascii=False)
    f = io.open(JOB["out"], "w", encoding="utf-8")
    f.write(u"{0}".format(text))
    f.close()


def step(name, fn, *args):
    """One step; a failure is recorded and the run goes on."""
    t0 = time.time()
    try:
        value = fn(*args)
    except Exception:
        RESULT["errors"].append(u"{0}: {1}".format(name, traceback.format_exc()))
        save()
        return None
    RESULT["steps"].append({"name": name, "s": time.time() - t0})
    save()
    return value


def names(obj):
    return sorted(n for n in dir(obj) if not n.startswith("__"))


# ---------------------------------------------------------------- the API
def api_case():
    """What the API offers on this model."""
    RESULT["version"] = str(mw.version())
    mw.solve()
    out = {"solution_names": names(solution), "mw_names": names(mw)}
    for name in ("time_steps", "modes"):
        out[name] = [repr(x) for x in (getattr(solution, name)() or [])]
    out["n_elements"] = len(list(solution.all_elements()))
    out["n_nodes"] = len(list(solution.all_nodes()))
    out["selected"] = [len(list(mw.selected_elements() or [])),
                       len(list(mw.selected_faces() or []))]
    out["selections"] = [str(n) for n in mw.named_selections()]
    # one sample of every variable, on the first set of this solution
    kw = {}
    if out["modes"]:
        kw["mode"] = 1
    elif out["time_steps"]:
        kw["time_step"] = int(out["time_steps"][-1])
    sample = {}
    eid = [int(e) for e in solution.all_elements()][0]
    nid = list(mw.nodes(eid))[-1]
    for var in VALUE_VARS + NODE_VARS + ("vonmises", "stressptu", "reactionforcey"):
        row = []
        for fn, args in ((solution.element_node_value, (var, eid, 1)),
                         (solution.element_node_value, (var, eid, 2)),
                         (solution.node_value, (var, nid))):
            try:
                row.append(repr(fn(*args, **kw)))
            except Exception as e:
                row.append(u"raises {0}".format(type(e).__name__))
        sample[var] = row
    out["sample"] = sample
    RESULT["api"] = out


# ---------------------------------------------------------------- reading state
def solution_values():
    """Every section force and motion of the current solution, as text of the
    exact doubles."""
    out = {}
    for eid in [int(e) for e in solution.all_elements()]:
        for var in VALUE_VARS:
            for k in (1, 2):
                try:
                    out["e{0}.{1}.{2}".format(eid, var, k)] = repr(
                        solution.element_node_value(var, eid, k))
                except Exception:
                    pass
    for nid in [int(n) for n in solution.all_nodes()]:
        for var in NODE_VARS:
            try:
                out["n{0}.{1}".format(nid, var)] = repr(solution.node_value(var, nid))
            except Exception:
                pass
    return out


def model_state():
    return {"nodes": len(list(mw.all_nodes())), "elements": len(list(mw.all_elements())),
            "components": sorted(u"{0}".format(c) for c in mw.components()),
            "solution_nodes": len(list(solution.all_nodes())),
            "solution_elements": len(list(solution.all_elements()))}


def sha(path):
    f = open(path, "rb")
    data = f.read()
    f.close()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "marker": b"Balkpositie" in data, "solution": b"<solution" in data}


# ---------------------------------------------------------------- the window
class Offscreen(object):
    """The real window on the threads of run_window, never shown: Application.Run is
    replaced by the pump of the test body, nothing else."""

    def __init__(self):
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        import beam_gui as gui
        import beam_mecway as bmw
        import beam_utils as bu
        bu.settings()["language"] = "en"
        self.gui, self.bmw, self.bu = gui, bmw, bu
        self.caller = gui._MwCaller(False)
        self.window = None
        self.F = self.D = None

    def run(self, body):
        from System.Threading import ApartmentState, Thread, ThreadStart
        from System.Windows.Forms import Application

        def main():
            try:
                self._build()
                body(self)
            except Exception:
                RESULT["errors"].append(u"window: {0}".format(traceback.format_exc()))
            finally:
                save()
                if self.window is not None:
                    self.window._on_closing(None, None)
                    self.window.shutdown()
                    self.window.form.Dispose()
        thread = Thread(ThreadStart(main))
        thread.SetApartmentState(ApartmentState.STA)
        thread.IsBackground = True
        thread.Start()
        on_ui = Thread.CurrentThread.GetApartmentState() == ApartmentState.STA
        RESULT["script_thread_sta"] = bool(on_ui)
        self.caller.serve(thread, Application.DoEvents if on_ui else None)
        self.gui.after_window(mw, self.window)

    def _build(self):
        import System.Drawing as D
        import System.Windows.Forms as F
        self.D, self.F = D, F
        w = self.gui._Window(mw, solution, self.caller, scale=1.0, vector=Vector)
        self.window = w
        form = w.form
        form.StartPosition = F.FormStartPosition.Manual
        form.Location = D.Point(-8000, -8000)
        form.ClientSize = D.Size(CANVAS[0], CANVAS[1])
        self.handles(form)

    def handles(self, control):
        h = control.Handle
        for child in control.Controls:
            self.handles(child)
        return h

    def pump(self, condition, seconds=60.0):
        from System.Threading import Thread
        end = time.time() + seconds
        while time.time() < end:
            self.F.Application.DoEvents()
            if condition():
                return True
            Thread.CurrentThread.Join(2)
        return False

    def call(self, fn):
        """fn on the script thread, as the window does it."""
        ticket = self.caller.post(fn)
        if not self.pump(lambda: self.caller.result(ticket)[0], 300.0):
            raise RuntimeError("the script thread did not answer")
        done, result, error = self.caller.result(ticket)
        if error:
            raise RuntimeError(error)
        return result

    def quiet(self):
        w = self.window
        link = w.link
        last = w.last or {}
        # a frame without a section is never painted: it counts when it has come
        shown = (w.painted == w.frame_no or
                 (last.get("no_section") and last.get("frame") == w.frame_no))
        return (link is not None and link.slot.empty() and link.busy_since is None and
                not w.tickets and not w.inbox and not w.marker_busy and
                not w.marker_timer.Enabled and not w.marker_due and
                (not w.opened or shown))

    def idle(self, seconds=120.0):
        """The worker and the mw calls have nothing left to do, for 100 ms in a row."""
        state = {"since": None}

        def settled():
            if not self.quiet():
                state["since"] = None
                return False
            if state["since"] is None:
                state["since"] = time.time()
            return time.time() - state["since"] > 0.1
        return self.pump(settled, seconds)

    def start(self):
        w = self.window
        w.start()
        self.pump(lambda: w.link is not None and w.link.alive, 60.0)
        return self.pump(lambda: w.head is not None, 60.0) and self.idle()

    def select(self, name):
        w = self.window
        keys = [s["key"] for s in w.source_items]
        w.updating = True
        w.source_box.SelectedIndex = keys.index(name)
        w.updating = False
        w._apply()
        return self.idle()

    def slide(self, value):
        w = self.window
        w.slider.Value = value
        w._on_slider()
        return self.idle()

    def shown(self):
        """What the window shows now."""
        w = self.window
        last = dict(w.last or {})
        return {"last": last, "forces_label": w.forces_label.Text,
                "place_label": w.place_label.Text, "torque_label": w.torque_label.Text,
                "status": w.status_label.Text, "warnings": w.warn_box.Text,
                "solver": w.solver_label.Text, "hover": w.hover_label.Text,
                "source": w.source_box.Text, "result": w.result_box.Text,
                "opened": bool(w.opened)}

    def measures(self):
        w = self.window
        out = {}
        for i, key in enumerate(w.measure_keys):
            w.measure_list.SelectedIndex = i
            self.idle()
            last = w.last or {}
            out[key] = {"measure": last.get("measure"), "outside": last.get("outside"),
                        "overall": last.get("overall"), "lo": last.get("lo"),
                        "hi": last.get("hi")}
        return out

    def png(self, name):
        from System.Drawing.Imaging import ImageFormat, PixelFormat
        D = self.D
        form = self.window.form
        self.F.Application.DoEvents()
        bmp = D.Bitmap(form.Width, form.Height, PixelFormat.Format32bppArgb)
        form.DrawToBitmap(bmp, D.Rectangle(0, 0, form.Width, form.Height))
        path = os.path.join(FOLDER, name)
        bmp.Save(path, ImageFormat.Png)
        colours = set()
        for i in range(0, bmp.Width, 11):
            for j in range(0, bmp.Height, 11):
                c = bmp.GetPixel(i, j)
                colours.add((c.R, c.G, c.B))
        bmp.Dispose()
        return {"file": name, "size": os.path.getsize(path), "colours": len(colours)}


def read_beam(h, name):
    """The direct route: selection, elements, route, chunk."""
    bmw = h.bmw
    result = {"kind": "active", "time_step": None, "mode": None, "configuration": None}
    info = h.call(lambda: bmw.beam_elements(mw, name))
    route = h.call(lambda: bmw.pick_route(mw, solution, info["ids"][0], result))
    chunk = h.call(lambda: bmw.read_chunk(mw, solution, info["ids"], result, route))
    return {"elements": info, "route": route, "chunk": chunk}


def view_beam(h, name, slider=(0, 125, 500, 875, 1000)):
    w = h.window
    out = {"select_ok": h.select(name), "chain": dict(w.chain or {}), "views": []}
    out.update(read_beam(h, name))
    for value in slider:
        h.slide(value)
        out["views"].append(h.shown())
    return out


def window_lists(h):
    w = h.window
    return {"sources": [dict(s) for s in w.source_items],
            "source_box": [w.source_box.Items[i] for i in range(w.source_box.Items.Count)],
            "results": [dict(r) for r in w.result_items or []],
            "result_box": [w.result_box.Items[i] for i in range(w.result_box.Items.Count)],
            "api_sources": h.call(lambda: h.bmw.sources(mw)),
            "api_results": h.call(lambda: h.bmw.result_sets(solution)),
            "current_selection": h.call(lambda: h.bmw.current_selection(mw)),
            "file": h.call(lambda: h.bmw.file_path(mw)) == mw_file()}


def mw_file():
    return JOB["model"]


def hover(h):
    """Down the middle of the picture, until the mouse is on the section."""
    w = h.window
    size = w.canvas.ClientSize
    for y in range(0, size.Height, 6):
        w.on_canvas_move(size.Width // 2, y, False)
        h.pump(lambda: False, 0.03)
        h.idle(5.0)
        if w.hover_label.Text:
            return w.hover_label.Text
    return u""


def node_values(names, nodes):
    return dict((name, [float(solution.node_value(name, n)) for n in nodes]) for name in names)


def write_button(h, name, nodes, key):
    """The button: on the chain of a selection; what it wrote, and what it left."""
    w = h.window
    h.select(name)
    base = h.call(solution_values)
    w._on_write()
    h.idle()
    log = {"status": w.status_label.Text, "chain": nodes,
           "values": h.call(lambda: node_values(("sectionvonmises", "sectionsigma", "sectiontau"),
                                                nodes)),
           "other_node": h.call(lambda: float(solution.node_value("sectionvonmises", key))),
           "compare": compare(base, h.call(solution_values))}
    h.slide(0)
    log["root"] = h.shown()["last"].get("outside")
    w.measure_list.SelectedIndex = w.measure_keys.index("sigma")
    h.idle()
    log["root_sigma"] = (w.last or {}).get("outside")
    w.measure_list.SelectedIndex = w.measure_keys.index("tau")
    h.idle()
    log["root_tau"] = (w.last or {}).get("outside")
    w.measure_list.SelectedIndex = 0
    h.idle()
    path = os.path.join(FOLDER, "written.liml")
    h.call(lambda: mw.save_as(path))
    f = io.open(path, encoding="utf-8")
    text = f.read()
    f.close()
    log["in_file"] = [v for v in ("sectionvonmises", "sectionsigma", "sectiontau")
                      if u'name="{0}"'.format(v) in text]
    return log


def frame_times(h, on):
    """Median round trip of frames over the real pipe, ms, with or without lines."""
    w = h.window
    w.contour_check.Checked = on
    h.idle()
    w.times = []
    for i in range(60):
        w.slider.Value = 200 + i
        w._on_slider()
        h.pump(lambda: w.painted == w.frame_no, 5.0)
    return h.gui._median([t[0] for t in w.times[10:]])


def contour_lines(h):
    h.bu.save_setting = lambda key, value: None         # not into the real settings
    h.select("sel_gen_u")
    out = {"off_ms": frame_times(h, False), "on_ms": frame_times(h, True)}
    h.slide(300)
    out["png"] = h.png("internal_contours.png")
    h.window.contour_check.Checked = False
    h.idle()
    return out


def internal_body(h):
    w = h.window
    RESULT["start_ok"] = h.start()
    RESULT["lists"] = window_lists(h)
    save()
    beams = {}
    for name in ("sel_gen_r", "sel_gen_u", "sel_ip", "face_ip", "sel_ip_q", "sel_ip_rev"):
        beams[name] = step("view " + name, view_beam, h, name)
    RESULT["beams"] = beams
    h.select("sel_gen_u")
    h.slide(300)
    RESULT["measures_gen_u"] = step("measures", h.measures)
    w.measure_list.SelectedIndex = 0
    h.idle()
    RESULT["hover"] = step("hover", hover, h)
    RESULT["png"] = [step("png", h.png, "internal_gen_u.png")]
    w.shear_check.Checked = False
    h.idle()
    RESULT["no_shear_centre"] = h.shown()
    w.shear_check.Checked = True
    h.idle()
    w.sheet_check.Checked = True
    h.idle()
    RESULT["sheet"] = h.shown()
    RESULT["png"].append(step("png sheet", h.png, "internal_sheet.png"))
    w.sheet_check.Checked = False
    h.idle()
    eids = sorted(RESULT["beams"]["sel_ip"]["elements"]["ids"])
    nodes = h.call(lambda: sorted(set(int(n) for e in eids for n in mw.nodes(e))))
    RESULT["written"] = step("write", write_button, h, "sel_ip", nodes, 1)
    RESULT["contours"] = step("contour lines", contour_lines, h)


def internal_case():
    mw.solve()
    RESULT["version"] = str(mw.version())
    Offscreen().run(internal_body)


def ccx_body(h):
    RESULT["start_ok"] = h.start()
    RESULT["lists"] = window_lists(h)
    beams = {}
    for name in ("sel_ip", "face_ip", "sel_rect_q"):
        beams[name] = step("view " + name, view_beam, h, name)
    RESULT["beams"] = beams
    RESULT["png"] = [step("png", h.png, "ccx.png")]


def ccx_case():
    mw.solve()
    Offscreen().run(ccx_body)


def frequencies():
    out = []
    for m in list(solution.modes() or []):
        out.append([repr(solution.frequency(m)), repr(solution.buckling_factor(m))])
    return out


def set_values(h, names, nid):
    """{name: [value per result set]} at a node; None where the set has none."""
    bmw = h.bmw
    results = h.window.result_items

    def work():
        out = {}
        for name in names:
            row = []
            for result in results:
                try:
                    value = solution.node_value(name, nid, **bmw._keywords(result))
                    row.append(None if value is None else float(value))
                except Exception:
                    row.append(None)
            out[name] = row
        return out
    return h.call(work)


def write_per_set(h):
    """The button on one set, then on another: what lands where, what stays."""
    w = h.window
    log = RESULT["per_set"] = {}
    results = w.result_items
    w.updating = True
    w.result_box.SelectedIndex = 0
    w.updating = False
    w._apply()
    h.idle()
    nid = h.call(lambda: int(list(mw.nodes(int(list(mw.named_selection("sel_ip"))[0])))[0]))
    log["node"] = nid
    log["motion_before"] = set_values(h, NODE_VARS, nid)
    w._on_write()
    h.idle()
    RESULT["write_status"] = w.status_label.Text
    names = ("sectionvonmises", "sectionsigma", "sectiontau")
    log["after_first"] = set_values(h, names, nid)
    if len(results) > 1 and not results[1].get("configuration"):
        w.updating = True
        w.result_box.SelectedIndex = 1
        w.updating = False
        w._apply()
        h.idle()
        w._on_write()
        h.idle()
        log["status_second"] = w.status_label.Text
        log["after_second"] = set_values(h, names, nid)
    log["motion_after"] = set_values(h, NODE_VARS, nid)
    log["sets"] = len(results)


def sets_body(h):
    """Every entry of the result list, at root and tip."""
    w = h.window
    RESULT["start_ok"] = h.start()
    RESULT["lists"] = window_lists(h)
    h.select("sel_ip")
    rows = []
    for i in range(w.result_box.Items.Count):
        w.updating = True
        w.result_box.SelectedIndex = i
        w.updating = False
        w._apply()
        h.idle()
        row = {"text": w.result_box.Text}
        h.slide(0)
        row["root"] = h.shown()
        h.slide(1000)
        row["tip"] = h.shown()
        rows.append(row)
    RESULT["sets"] = rows
    step("written per set", write_per_set, h)
    RESULT["frequencies"] = h.call(frequencies)
    RESULT["png"] = [step("png", h.png, "sets.png")]


def sets_case():
    mw.solve()
    Offscreen().run(sets_body)


def _describe(obj):
    """Python and .NET type of an API value, as text."""
    out = {"py": type(obj).__name__}
    try:
        out["net"] = str(obj.GetType().FullName)
    except Exception as e:
        out["net"] = u"raises {0}".format(type(e).__name__)
    return out


def selection_items(name):
    """What named_selection gives for one name: its type, the types of the items,
    the numbers."""
    value = mw.named_selection(name)
    items = list(value)
    out = {"value": _describe(value), "count": len(items)}
    if items:
        out["item"] = _describe(items[0])
    out["ids"] = [int(getattr(i, "element_id", i)) for i in items][:20]
    out["faces"] = [int(i.face_number) for i in items if hasattr(i, "face_number")][:20]
    return out


def selections_body(h):
    """The real window on selections made after the last save."""
    if not h.start():
        raise RuntimeError("the window did not start")
    w = h.window
    RESULT["window"] = {
        "sources": [{"key": s["key"], "count": s["count"],
                     "uncertain": bool(s.get("uncertain"))} for s in w.source_items],
        "labels": [w.source_box.Items[i] for i in range(w.source_box.Items.Count)]}
    save()
    made = RESULT["selections"]
    # the names Mecway gave; a missing one fails here (index raises)
    for kind, name in (("node", made["new_node"]), ("element", made["new_element"]),
                       ("face", made["new_face"]), ("file", "sel_ip")):
        h.select(name)
        RESULT["window"][kind] = {
            "name": name, "opened": bool(w.opened),
            "ids": list(w.opened_key[1]) if w.opened and w.opened_key else None,
            "warnings": w.warn_box.Text, "status": w.status_label.Text,
            "source": w.source_box.Text}
        save()


def selections_case():
    """What named_selection returns for a node, element and face selection made by
    the script after the last save; the file itself has its own node and element
    selections."""
    RESULT["version"] = str(mw.version())
    step("solve", mw.solve)
    out = {"mw_selection_names": [n for n in names(mw) if "selection" in n.lower()]}
    # Mecway 35: new_*_selection(name) makes an empty selection and
    # add_to_named_selection(name, item) takes one item (measured: a second argument,
    # and a list, are refused)
    def make(new, name, items):
        made = u"{0}".format(new(name))
        for item in items:
            mw.add_to_named_selection(made, item)
        return made
    out["new_node"] = step("new node", make, mw.new_node_selection, "probe_nodes",
                           [1, 2, 3, 4, 5])
    out["new_element"] = step("new element", make, mw.new_element_selection, "probe_elems",
                              [1, 2])
    out["new_face"] = step("new face", make, mw.new_face_selection, "probe_faces",
                           [FaceId(9, 1), FaceId(10, 1)])
    # a number beyond the last node: what the program does with it
    beyond = u"{0}".format(mw.new_node_selection("probe_beyond"))
    out["node_beyond"] = attempt(mw.add_to_named_selection, beyond, 99)
    out["listed"] = [u"{0}".format(n) for n in mw.named_selections()]
    per = {}
    for name in out["listed"]:
        per[name] = step("items " + name, selection_items, name)
    out["per_selection"] = per
    out["max_node"] = max(int(n) for n in mw.all_nodes())
    out["max_element"] = max(int(e) for e in mw.all_elements())
    RESULT["selections"] = out
    save()
    h = Offscreen()
    h.bu.save_setting = lambda key, value: None
    h.run(selections_body)


def config_body(h):
    """sets_body, then the second beam in every entry: root and s = 0.125, and the
    element values Mecway gives there, in both configurations."""
    sets_body(h)
    w = h.window
    h.select("sel_oc")
    rows = []
    for i in range(w.result_box.Items.Count):
        w.updating = True
        w.result_box.SelectedIndex = i
        w.updating = False
        w._apply()
        h.idle()
        row = {"text": w.result_box.Text, "configuration": w.result_items[i].get("configuration")}
        h.slide(0)
        row["root"] = h.shown()
        h.slide(125)
        row["inside"] = h.shown()
        rows.append(row)
    RESULT["sets_oc"] = rows


def bogus_name():
    """What set_active_configuration does with a name the model does not have."""
    out = {}
    step("activate B", mw.set_active_configuration, "B")
    try:
        mw.set_active_configuration("no such configuration")
        out["raised"] = None
    except Exception as e:
        out["raised"] = u"{0}: {1}".format(type(e).__name__, e)
    eid = int(list(mw.named_selection("sel_ip"))[0])
    try:
        out["Mw_after"] = float(solution.element_node_value("bendingmomw", eid, 1))
    except Exception as e:
        out["Mw_after"] = u"{0}".format(e)
    return out


def config_case():
    """One solution per configuration; the window switches between them."""
    for name in ("A", "B"):
        step("activate " + name, mw.set_active_configuration, name)
        step("solve " + name, mw.solve)
    RESULT["bogus"] = step("bogus name", bogus_name)
    Offscreen().run(config_body)


# ---------------------------------------------------------------- V10, the marker
def compare(base, now):
    """(number of values, how many differ, the first few)."""
    bad = [k for k in base if now.get(k) != base[k]]
    return {"values": len(base), "differ": len(bad), "missing": len(set(base) - set(now)),
            "added": len(set(now) - set(base)), "first": sorted(bad)[:5]}


def marker_nodes(h):
    def work():
        out = []
        for comp in h.bmw._our_components(mw):
            for e in mw.component_elements(comp):
                for n in mw.nodes(e):
                    out.append([float(c) for c in h.bmw._xyz(mw.node(n))])
        return out
    return h.call(work)


def marker_body(h):
    w = h.window
    log = RESULT["marker"] = {}
    base = h.call(solution_values)
    log["state_before"] = h.call(model_state)
    path_a = os.path.join(FOLDER, "a_no_marker.liml")
    h.call(lambda: mw.save_as(path_a))
    log["file_a"] = sha(path_a)
    h.start()
    log["first_view_ok"] = h.select("sel_ip")
    h.slide(500)
    log["view_before"] = h.shown()

    def snapshot(name):
        log[name] = {"state": h.call(model_state), "compare": compare(base, h.call(solution_values)),
                     "marker_nodes": marker_nodes(h)}
        save()
    w.marker_check.Checked = True
    h.idle()
    log["switched"] = bool(w.switched)
    log["marker_on_text"] = w.warn_box.Text
    snapshot("after_show")
    h.slide(800)
    h.pump(lambda: False, 0.5)
    h.idle()
    snapshot("after_move")
    log["view_marker"] = h.shown()
    path_m = os.path.join(FOLDER, "m_with_marker.liml")
    h.call(lambda: mw.save_as(path_m))
    log["file_m"] = sha(path_m)
    w._refresh()
    log["reread_ok"] = h.idle()
    log["view_reread"] = h.shown()
    w.marker_check.Checked = False
    h.idle()
    snapshot("after_remove")
    log["counts"] = h.call(lambda: h.bmw.marker_counts(mw))
    path_b = os.path.join(FOLDER, "b_after_marker.liml")
    h.call(lambda: mw.save_as(path_b))
    log["file_b"] = sha(path_b)
    # direct calls, and the same again with two markers in a row
    place = {"point": [0.5, 0.5, 0.0], "v": [0.0, 1.0, 0.0], "w": [0.0, 0.0, 1.0], "half": 0.05}
    log["direct_show"] = h.call(lambda: h.bmw.show_marker(mw, Vector, place))
    log["direct_show_again"] = h.call(lambda: h.bmw.show_marker(mw, Vector, place))
    moved = dict(place, point=[0.75, 0.5, 0.0])
    log["direct_move"] = h.call(lambda: h.bmw.move_marker(mw, moved))
    log["direct_nodes"] = marker_nodes(h)
    log["direct_remove"] = h.call(lambda: h.bmw.remove_marker(mw))
    log["direct_remove_nothing"] = h.call(lambda: h.bmw.remove_marker(mw))
    log["direct_move_nothing"] = h.call(lambda: h.bmw.move_marker(mw, moved))
    snapshot("after_direct")
    path_c = os.path.join(FOLDER, "c_after_direct.liml")
    h.call(lambda: mw.save_as(path_c))
    log["file_c"] = sha(path_c)


def marker_case():
    mw.solve()
    Offscreen().run(marker_body)


def marker_solve_case():
    """Solve with the marker in the model, on the private desktop of the runner: an
    error dialog would stay there and the runner reports its title."""
    import beam_mecway as bmw
    mw.solve()
    base = solution_values()
    log = RESULT["marker_solve"] = {"values": len(base), "modes_before": frequencies()}
    place = {"point": [0.5, 0.5, 0.0], "v": [0.0, 1.0, 0.0], "w": [0.0, 0.0, 1.0], "half": 0.05}
    bmw.show_marker(mw, Vector, place)
    log["state_with"] = model_state()
    save()
    t0 = time.time()
    mw.solve()
    log["solve_s"] = time.time() - t0
    log["state_after_solve"] = model_state()
    log["modes_with"] = frequencies()
    now = solution_values()
    log["compare_solved"] = compare(base, now)
    # the rounding of a solve is not exact: largest relative difference
    worst = 0.0
    for key, value in base.items():
        other = now.get(key)
        if other not in (None, "None") and value != "None":
            a, b = float(value), float(other)
            worst = max(worst, abs(a - b) / max(abs(a), 1e-3))
    log["worst_relative"] = worst
    save()
    log["removed"] = bmw.remove_marker(mw)
    log["state_removed"] = model_state()
    save()
    mw.solve()
    log["compare_after"] = compare(base, solution_values())
    log["state_end"] = model_state()


def marker_stale_case():
    """Solved with the marker, marker removed, saved: the solution in the file then
    holds one element more than the model. crash_file: for the next run to open."""
    import beam_mecway as bmw
    place = {"point": [0.5, 0.5, 0.0], "v": [0.0, 1.0, 0.0], "w": [0.0, 0.0, 1.0], "half": 0.05}
    bmw.show_marker(mw, Vector, place)
    mw.solve()
    RESULT["state_solved"] = model_state()
    bmw.remove_marker(mw)
    RESULT["state"] = model_state()
    mw.save_as(JOB["crash_file"])
    RESULT["file"] = sha(JOB["crash_file"])


def crash_save_case():
    """A file saved with the marker in it, as a crash leaves it."""
    import beam_mecway as bmw
    mw.solve()
    place = {"point": [0.5, 0.5, 0.0], "v": [0.0, 1.0, 0.0], "w": [0.0, 0.0, 1.0], "half": 0.05}
    bmw.show_marker(mw, Vector, place)
    RESULT["state"] = model_state()
    mw.save_as(JOB["crash_file"])
    RESULT["file"] = sha(JOB["crash_file"])


def crash_open_body(h):
    w = h.window
    log = RESULT["crash"] = {}
    log["state_open"] = h.call(model_state)
    log["start_ok"] = h.start()
    log["warnings"] = w.warn_box.Text
    log["warn_start"] = list(w.warn_start)
    log["state_clean"] = h.call(model_state)
    log["counts"] = h.call(lambda: h.bmw.marker_counts(mw))
    log["select_ok"] = h.select("sel_ip")
    h.slide(0)
    log["view"] = h.shown()
    path = os.path.join(FOLDER, "cleaned.liml")
    h.call(lambda: mw.save_as(path))
    log["file"] = sha(path)


def crash_open_case():
    Offscreen().run(crash_open_body)


def big_case():
    """Cost of the marker calls on a model of 100 000 elements, hidden: display does
    not draw here, so these are lower limits for a visible Mecway."""
    import beam_mecway as bmw
    t = RESULT["big"] = {}

    def timed(name, fn, n=1):
        spent = []
        for _ in range(n):
            t0 = time.time()
            fn()
            spent.append(time.time() - t0)
        t[name] = sorted(spent)[len(spent) // 2]
        save()
    t["elements"] = len(list(mw.all_elements()))
    t["nodes"] = len(list(mw.all_nodes()))
    timed("display_unchanged", mw.display, 5)
    place = {"point": [0.5, -0.1, 0.0], "v": [0.0, 1.0, 0.0], "w": [0.0, 0.0, 1.0], "half": 0.05}
    timed("set_model_active", mw.set_model_active, 3)
    timed("show_marker", lambda: bmw.show_marker(mw, Vector, place))
    timed("display_unchanged_with_marker", mw.display, 5)

    def move():
        bmw.move_marker(mw, dict(place, point=[0.25, -0.1, 0.0]))
    timed("move_marker", move, 5)

    def move_only():
        nid = [int(n) for c in bmw._our_components(mw) for e in mw.component_elements(c)
               for n in mw.nodes(e)]
        for n in nid:
            mw.set_node_x(n, 0.26)
            mw.set_node_y(n, -0.1)
            mw.set_node_z(n, 0.01)
    timed("move_without_display", move_only, 5)

    def move_and_display():
        mw.set_node_x(int(list(mw.nodes(int(list(mw.component_elements(
            bmw._our_components(mw)[0]))[0])))[0]), 0.27)
        mw.display()
    timed("one_node_and_display", move_and_display, 5)
    timed("remove_marker", lambda: bmw.remove_marker(mw))
    bmw.show_marker(mw, Vector, place)
    comps = bmw._our_components(mw)
    comp = comps[0]
    timed("api_components", lambda: list(mw.components()), 5)
    timed("api_material", lambda: mw.material(comp), 5)
    timed("api_our_components", lambda: bmw._our_components(mw), 5)
    timed("api_component_elements", lambda: list(mw.component_elements(comp)), 5)
    elements = [int(e) for c in comps for e in mw.component_elements(c)]
    timed("api_nodes", lambda: list(mw.nodes(elements[0])), 5)
    nodes = sorted(set(int(n) for e in elements for n in mw.nodes(e)))
    timed("api_set_node_x", lambda: mw.set_node_x(nodes[0], 0.5), 5)
    timed("api_set_node_z", lambda: mw.set_node_z(nodes[0], 0.0), 5)
    t["marker_elements"] = len(elements)
    t["marker_nodes"] = len(nodes)
    timed("delete_elements", lambda: mw.delete_elements(elements))
    t["elements_after_delete"] = len(list(mw.all_elements()))

    def scan():
        used = set()
        for e in mw.all_elements():
            used.update(int(n) for n in mw.nodes(e))
    timed("scan_all_elements", scan)
    timed("all_elements", lambda: list(mw.all_elements()))
    timed("delete_nodes", lambda: mw.delete_nodes(nodes))
    t["nodes_after_delete"] = len(list(mw.all_nodes()))
    timed("remove_rest", lambda: bmw.remove_marker(mw))
    t["state"] = {"nodes": len(list(mw.all_nodes())), "elements": len(list(mw.all_elements())),
                  "components": sorted(u"{0}".format(c) for c in mw.components())}


def aster_body(h):
    w = h.window
    RESULT["start_ok"] = h.start()
    RESULT["lists"] = window_lists(h)
    RESULT["beams"] = {"sel_b": step("view", view_beam, h, "sel_b")}
    h.slide(500)
    RESULT["measures"] = step("measures", h.measures)
    w.measure_list.SelectedIndex = 0
    h.idle()
    RESULT["warping_label"] = w.warping_label.Text
    RESULT["png"] = [step("png", h.png, "aster.png")]
    w._on_write()
    h.idle()
    RESULT["write_status"] = w.status_label.Text
    RESULT["written_root"] = h.call(lambda: float(solution.node_value("sectionvonmises", 1)))


def aster_case():
    """An opened Code-Aster solution; the file is saved again by Mecway afterwards."""
    RESULT["version"] = str(mw.version())
    Offscreen().run(aster_body)
    mw.save_as(os.path.join(FOLDER, "resaved.liml"))


def attempt(fn, *args):
    try:
        return {"ok": True, "returned": u"{0}".format(fn(*args)), "error": None}
    except Exception as e:
        return {"ok": False, "error": u"{0}: {1}".format(type(e).__name__, e)}


def read_variable(name, n):
    out = []
    for nid in [int(i) for i in solution.all_nodes()][:n]:
        try:
            out.append(repr(solution.node_value(name, nid)))
        except Exception as e:
            out.append(u"raises {0}".format(type(e).__name__))
    return out


def gap_body(h):
    """Node numbers out of order: the real button, and a value that names its node."""
    w = h.window
    log = RESULT["gap"] = {"all_nodes": h.call(lambda: [int(n) for n in solution.all_nodes()]),
                           "mw_nodes": h.call(lambda: [int(n) for n in mw.all_nodes()])}
    h.start()
    log["select_ok"] = h.select("sel_b")
    log["chain"] = dict(w.chain or {})
    h.slide(0)
    log["root"] = h.shown()
    w._on_write()
    h.idle()
    log["status"] = w.status_label.Text
    along = log["along"] = json.loads(open(JOB["info"]).read())["along"]
    log["written"] = h.call(lambda: node_values(("sectionvonmises", "sectionsigma", "sectiontau"),
                                                along))
    named = {"sectionnodeid": dict((str(n), 10.0 * n) for n in along)}
    active = {"time_step": None, "mode": None}
    h.call(lambda: h.bmw.write_results(mw, solution, named, active))
    log["named"] = h.call(lambda: node_values(("sectionnodeid",), along))["sectionnodeid"]
    h.call(lambda: mw.save_as(JOB["crash_file"]))


# ---------------------------------------------------------------- several sections
NUMBER_BEAMS = ("sel_dir_ok", "sel_dir_vz", "sel_dir_rev", "sel_swap", "sel_noshape",
                "sel_mixed", "sel_bent")
SHEET_BEAMS = ("sel_dir_ok", "sel_mixed", "sel_bent")


def overlaps(boxes):
    """Pairs of (number, left, top, width, height) that overlap."""
    out = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i][1:], boxes[j][1:]
            if (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and
                    a[1] < b[1] + b[3] and b[1] < a[1] + a[3]):
                out.append([boxes[i][0], boxes[j][0]])
    return out


def tile_hover(h, tile):
    """Down the middle of one tile, until the mouse is on the section."""
    w = h.window
    x = int(tile["x"] + tile["w"] // 2)
    for y in range(int(tile["y"]) + 2, int(tile["y"] + tile["h"]) - 1, 4):
        w.on_canvas_move(x, y, False)
        h.pump(lambda: False, 0.02)
        h.idle(5.0)
        if w.hover_label.Text:
            return w.hover_label.Text
    return u""


def sheet(h, name, n):
    from System import Decimal
    w = h.window
    w.sheet_count.Value = Decimal(n)
    h.idle()
    out = {"n": n, "png": h.png("numbers_{0}_{1}.png".format(name, n))}
    tiles = list(w.canvas.tiles or [])
    out["tiles"] = [{"number": t.get("number"), "s": t.get("s"), "title": w.tile_title(t),
                     "no_section": "no_section" in t} for t in tiles]
    labels = [[int(b[0])] + [float(v) for v in b[1:]] for b in w.side_labels]
    out["side_labels"] = labels
    out["side_overlaps"] = overlaps(labels)
    out["hover"] = [tile_hover(h, t) for t in tiles]
    numbers = [t["number"] for t in out["tiles"]]
    s = [t["s"] for t in out["tiles"]]
    hovered = []
    for text in out["hover"]:
        hovered.append(int(text.split(u",")[0].split()[-1]) if text else None)
    out["check"] = {
        "count": len(tiles) == n,
        "numbers_in_order": numbers == list(range(1, len(tiles) + 1)),
        "s_rising": all(a < b for a, b in zip(s, s[1:])),
        "titles_numbered": all(t["title"].startswith(u"{0}.".format(t["number"]))
                               for t in out["tiles"]),
        "side_numbers": sorted(b[0] for b in labels) == numbers,
        "side_no_overlap": not out["side_overlaps"],
        "hover_number": all(hv is None and t["no_section"] or hv == t["number"]
                            for hv, t in zip(hovered, out["tiles"])),
        "hover_none_only_without_section": all((hv is None) == t["no_section"]
                                               for hv, t in zip(hovered, out["tiles"]))}
    return out


def numbers_body(h):
    w = h.window
    RESULT["start_ok"] = h.start()
    beams = RESULT["beams"] = {}
    for name in NUMBER_BEAMS:
        row = beams[name] = {"select_ok": h.select(name)}
        row["warnings"] = w.warn_box.Text
        row["status"] = w.status_label.Text
        row["measures"] = list(w.measure_keys)
        row["opened"] = bool(w.opened)
        h.slide(250)
        row["view"] = h.shown()
        save()
    for name in SHEET_BEAMS:
        h.select(name)
        w.sheet_check.Checked = True
        h.idle()
        beams[name]["sheets"] = [step("sheet {0} {1}".format(name, n), sheet, h, name, n)
                                 for n in (2, 12)]
        w.sheet_check.Checked = False
        h.idle()
        save()
    h.bu.settings()["language"] = "nl"          # in memory, not into the real settings
    w._retext()
    h.idle()
    RESULT["dutch"] = {}
    for name in ("sel_dir_rev", "sel_swap", "sel_noshape"):
        h.select(name)
        RESULT["dutch"][name] = w.warn_box.Text


def numbers_case():
    mw.solve()
    RESULT["version"] = str(mw.version())
    Offscreen().run(numbers_body)


def chain_nodes(h):
    """The nodes along the chain of the window, from root to tip, with their s."""
    w = h.window
    rows = w.chain["elements"]

    def work():
        ends = [[int(n) for n in mw.nodes(int(r[0]))] for r in rows]
        return [[e[0], e[-1]] for e in ends]
    ends = h.call(work)
    out = []
    for i, (row, pair) in enumerate(zip(rows, ends)):
        if i == 0:
            other = set(ends[1]) if len(ends) > 1 else set()
            first = [n for n in pair if n not in other][0]
            out.append([first, float(row[1])])
        nxt = [n for n in pair if n != out[-1][0]][0]
        out.append([nxt, float(row[2])])
    return out


def panel_at(h, slider):
    """|outside value| (or overall when no outside) per written variable at a slider."""
    w = h.window
    h.slide(slider)
    out = {}
    keys = list(w.measure_keys)
    for key, name in (("vonmises", "sectionvonmises"), ("sigma", "sectionsigma"),
                      ("tau", "sectiontau")):
        if key not in keys:
            continue
        w.measure_list.SelectedIndex = keys.index(key)
        h.idle()
        last = w.last or {}
        pick = last.get("outside") or last.get("overall") or {}
        out[name] = None if pick.get("value") is None else abs(float(pick["value"]))
    w.measure_list.SelectedIndex = 0
    h.idle()
    return out


def written_against_panel(h):
    """The written node values along the chain against the window: root and tip are one
    side only, so equal; an inner node holds the larger of both sides."""
    w = h.window
    nodes = chain_nodes(h)
    length = float(w.chain["length"])
    names = ("sectionvonmises", "sectionsigma", "sectiontau")

    def read():
        out = {}
        for name in names:
            row = []
            for nid, _ in nodes:
                try:
                    v = solution.node_value(name, nid)
                    row.append(None if v is None else float(v))
                except Exception:
                    row.append(None)
            out[name] = row
        return out
    written = h.call(read)
    rows, worst = [], 0.0
    for i, (nid, s) in enumerate(nodes):
        panel = panel_at(h, int(round(1000.0 * s / length)))
        row = {"node": nid, "s": s, "panel": panel, "written": {}}
        for name in names:
            got = written[name][i]
            row["written"][name] = got
            want = panel.get(name)
            if want is None or got is None:
                continue
            rel = (got - want) / max(abs(want), 1.0)
            if i in (0, len(nodes) - 1):
                worst = max(worst, abs(rel))
            elif rel < -1e-6:
                worst = max(worst, abs(rel))
        rows.append(row)
    return {"nodes": rows, "worst_relative": worst}


def shells_body(h):
    """The button on a solved file. The question of the window is answered from the job."""
    w = h.window
    log = RESULT["shells"] = {"asked": [], "answer": JOB["answer"]}

    def ask(text):
        log["asked"].append(text)
        return h.F.DialogResult.Yes if JOB["answer"] == "yes" else h.F.DialogResult.No
    w._ask = ask
    log["start_ok"] = h.start()
    log["head"] = dict((k, w.head.get(k)) for k in ("aster", "shells", "solver"))
    log["before"] = h.call(counts)
    log["opened"] = bool(w.opened)
    names = ("sectionvonmises", "sectionsigma", "sectiontau")
    log["variables_before"] = h.call(lambda: read_names(names))
    base = h.call(solution_values)
    w._on_write()
    h.pump(lambda: not w.solving and h.quiet(), 120.0)
    h.idle()
    log["status"] = w.status_label.Text
    log["after"] = h.call(counts)
    log["compare_after"] = compare(base, h.call(solution_values))
    log["variables_after"] = h.call(lambda: read_names(names))
    if w.chain and u"sectionvonmises" in w.status_label.Text:
        log["against_panel"] = step("against panel", written_against_panel, h)
    log["enabled"] = [bool(w.write_button.Enabled), bool(w.refresh_button.Enabled),
                      bool(w.source_box.Enabled), bool(w.result_box.Enabled),
                      bool(w.marker_check.Enabled)]

    def written():
        try:
            return repr(solution.node_value("sectionsigma", 1))
        except Exception as e:
            return u"raises {0}".format(type(e).__name__)
    log["written"] = h.call(written)
    if w.chain and JOB["answer"] == "yes" and u"sectionvonmises" in w.status_label.Text:
        eid = w.chain["elements"][0][0]
        nid = h.call(lambda: int(list(mw.nodes(eid))[0]))
        log["root"] = h.call(lambda: float(solution.node_value("sectionvonmises", nid)))
        h.slide(0)
        log["window"] = (w.last or {}).get("outside", {}).get("value")
    # the real error of Mecway through the real code, past the check of the sizes
    active = {"time_step": None, "mode": None}
    log["set_all"] = h.call(lambda: h.bmw.set_all(solution, {"sectionprobe": {"1": 1.0}}, active))


def shells_case():
    Offscreen().run(shells_body)


def gap_case():
    mw.solve()
    Offscreen().run(gap_body)


def gap_open_case():
    """Opened again: the numbers and what stands on them."""
    along = json.loads(open(os.path.join(JOB["repo"], "tests", "mecway", "models",
                                         "e2e_gap.info.json")).read())["along"]
    RESULT["gap_open"] = {"all_nodes": [int(n) for n in solution.all_nodes()],
                          "along": along,
                          "named": node_values(("sectionnodeid",), along)["sectionnodeid"]}


def try_write():
    """set_variable once: what comes back, or the exception as it is."""
    ids = [int(i) for i in solution.all_nodes()]
    try:
        out = solution.set_variable("sectionprobe", [float(i) for i in ids])
        return {"ok": True, "returned": u"{0}".format(out),
                "read": repr(solution.node_value("sectionprobe", ids[-1]))}
    except Exception as e:
        inner = getattr(e, "clsException", None)
        return {"ok": False, "python": type(e).__name__, "text": u"{0}".format(e),
                "net": None if inner is None else inner.GetType().Name}


def read_names(names):
    """Is a variable there: its value at the first node, or what reading it raises."""
    nid = [int(n) for n in solution.all_nodes()][0]
    out = {}
    for name in names:
        try:
            out[name] = repr(solution.node_value(name, nid))
        except Exception as e:
            out[name] = u"raises {0}".format(type(e).__name__)
    return out


def counts():
    return {"model": len(list(mw.all_nodes())), "solution": len(list(solution.all_nodes())),
            "elements": len(list(mw.all_elements())),
            "solution_elements": len(list(solution.all_elements()))}


def diff_case():
    """Which difference between model and solution set_variable refuses."""
    import beam_mecway as bmw
    log = RESULT["diff"] = {"kind": JOB["kind"], "before": counts()}
    place = {"point": [0.5, 0.5, 0.0], "v": [0.0, 1.0, 0.0], "w": [0.0, 0.0, 1.0], "half": 0.05}
    kind = JOB["kind"]
    if kind == "node_added":
        mw.new_node(Vector(9.0, 9.0, 9.0))
    elif kind == "marker":
        bmw.show_marker(mw, Vector, place)
    elif kind == "last_element_deleted":
        last = max(int(e) for e in mw.all_elements())
        nodes = [int(n) for n in mw.nodes(last)]
        mw.delete_elements([last])
        used = set(int(n) for e in mw.all_elements() for n in mw.nodes(e))
        mw.delete_nodes([n for n in nodes if n not in used])
    elif kind == "node_moved":
        mw.set_node_z(1, 0.001)
    elif kind == "solved":
        mw.solve()
    log["after"] = counts()
    log["write"] = try_write()


def setvar_case():
    """Does set_variable take a new name, what stays, what lands in the file."""
    from System import Array, Double
    log = RESULT["setvar"] = {}
    mw.solve()
    base = solution_values()
    ids = [int(i) for i in solution.all_nodes()]
    n = len(ids)
    log["n_nodes"], log["n_elements"] = n, len(list(solution.all_elements()))
    log["before"] = read_variable("sectionvonmises", 3)
    values = [1000.0 + i for i in range(n)]
    log["python_list"] = attempt(solution.set_variable, "sectionvonmises", values)
    if not log["python_list"]["ok"]:
        log["net_array"] = attempt(solution.set_variable, "sectionvonmises",
                                   Array[Double](values))
    log["after"] = read_variable("sectionvonmises", 3)
    log["last_node"] = read_variable("sectionvonmises", n)[-1]
    log["compare_new"] = compare(base, solution_values())
    log["short_list"] = attempt(solution.set_variable, "sectionshort", values[:3])
    log["short_read"] = read_variable("sectionshort", 3)
    log["element_values"] = attempt(solution.set_variable, "section_el",
                                    [1.0] * log["n_elements"])
    log["element_read"] = read_variable("section_el", 3)
    try:
        log["element_node"] = repr(solution.element_node_value("sectionvonmises", 1, 1))
    except Exception as e:
        log["element_node"] = u"raises {0}".format(type(e).__name__)
    log["overwrite"] = attempt(solution.set_variable, "disply", [7.0] * n)
    log["compare_overwrite"] = compare(base, solution_values())
    save()
    mw.display()
    mw.save_as(JOB["crash_file"])
    log["file"] = sha(JOB["crash_file"])
    f = io.open(JOB["crash_file"], encoding="utf-8")
    text = f.read()
    f.close()
    log["in_file"] = [name for name in ("sectionvonmises", "sectionshort", "section_el")
                      if u'name="{0}"'.format(name) in text]


def setvar_open_case():
    """The saved file again: is the new variable still there."""
    log = RESULT["setvar"] = {}
    log["read"] = read_variable("sectionvonmises", 3)
    log["elements"] = len(list(solution.all_elements()))
    log["disply"] = read_variable("disply", 3)
    log["forces"] = repr(solution.element_node_value("bendingmomw", 1, 1))


# ---------------------------------------------------------------- 3D and detail
# The 3D view and the detail of one section in real Mecway: the real panel, its
# canvas handlers fed with synthetic MouseEventArgs in this process (no input of
# the system, the form is never shown), the real worker over the pipe. One wheel
# step goes through the WndProc of the canvas as a posted message. Not covered:
# clicks, moves and focus through WndProc (WinForms reads the buttons of a move from
# the real mouse and raises a double click only under the real cursor).
MAGNIFIED = u"%c magnified" % 0xd7          # the sign of the magnification text
SPY = None
PREFIX = [u""]                  # the beam of the PNG names


class Spy(object):
    """Every frame reply and every pick reply the window gets, in order."""

    def __init__(self, w):
        self.frames, self.picks = [], []
        on_frame, on_pick = w._on_frame, w._on_pick

        def frame(item):
            rq, rp = item["request"], item["reply"]
            self.frames.append({
                "frame": rq.get("frame"), "gen": rq.get("gen"), "window_gen": w.gen,
                "asked_coarse": bool(rq.get("coarse")), "coarse": bool(rp.get("coarse")),
                "camera_sent": "camera" in rq, "three": "camera" in rp,
                "sheet": bool(rp.get("sheet")), "ok": bool(rp.get("ok")), "n": rp.get("n"),
                "ms": item["ms"], "worker": rp.get("ms")})
            return on_frame(item)

        def pick(reply, request):
            self.picks.append({"number": reply.get("number"), "hit": reply.get("hit"),
                               "frame": reply.get("frame"), "painted": w.painted,
                               "v": reply.get("v")})
            return on_pick(reply, request)
        w._on_frame = frame
        w._on_pick = pick

    def mark(self):
        return len(self.frames)

    def since(self, mark):
        return self.frames[mark:]


def mouse(h, button, clicks, x, y, delta=0):
    F = h.F
    buttons = {"left": F.MouseButtons.Left, "right": F.MouseButtons.Right,
               "middle": F.MouseButtons.Middle, None: getattr(F.MouseButtons, "None")}
    return F.MouseEventArgs(buttons[button], clicks, int(x), int(y), int(delta))


def one_step(frames):
    """A step of the 3D view: exactly one coarse and one fine reply, same request."""
    coarse = [f for f in frames if f["coarse"]]
    fine = [f for f in frames if not f["coarse"]]
    return (len(coarse) == 1 and len(fine) == 1 and coarse[0]["frame"] == fine[0]["frame"] and
            coarse[0]["three"] and fine[0]["three"])


def wrap(angle):
    a = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def same_angle(a, b, tol=1e-6):
    return abs(wrap(float(a) - float(b))) < tol


def same_camera(a, b, tol=1e-9):
    if a is None or b is None or sorted(a) != sorted(b):
        return False
    for key in a:
        x, y = a[key], b[key]
        if isinstance(x, (list, tuple)):
            if len(x) != len(y) or any(abs(float(p) - float(q)) > tol * max(1.0, abs(float(q)))
                                       for p, q in zip(x, y)):
                return False
        elif abs(float(x) - float(y)) > tol * max(1.0, abs(float(y))):
            return False
    return True


def canvas_png(h, name):
    """The canvas alone, as the user would see it, with the text drawn over it."""
    from System.Drawing.Imaging import ImageFormat, PixelFormat
    D = h.D
    c = h.window.canvas
    name = PREFIX[0] + name
    h.F.Application.DoEvents()
    bmp = D.Bitmap(c.Width, c.Height, PixelFormat.Format32bppArgb)
    c.DrawToBitmap(bmp, D.Rectangle(0, 0, c.Width, c.Height))
    path = os.path.join(FOLDER, name)
    bmp.Save(path, ImageFormat.Png)
    bmp.Dispose()
    out = sha(path)
    return {"file": name, "sha256": out["sha256"], "size": out["size"]}


def overlay(h, n):
    """What the 3D frame on the canvas shows: tiles, anchors and the drawn texts."""
    w = h.window
    shown = w.canvas.shown or {}
    tiles = shown.get("tiles") or []
    size = w.canvas.ClientSize
    labels = [[u"{0}".format(l[0])] + [float(v) for v in l[1:]] for l in (w.canvas_labels or [])]
    texts = [l[0] for l in labels]
    anchors = [t.get("anchor") for t in tiles]
    inside = all(a is not None and 0 <= a[0] < size.Width and 0 <= a[1] < size.Height
                 for a in anchors)
    apart = None
    for i in range(len(anchors)):
        for j in range(i + 1, len(anchors)):
            if anchors[i] and anchors[j]:
                d = ((anchors[i][0] - anchors[j][0]) ** 2 +
                     (anchors[i][1] - anchors[j][1]) ** 2) ** 0.5
                apart = d if apart is None else min(apart, d)
    numbers = [[int(l[0])] + l[1:] for l in labels if l[0].isdigit()]
    k = shown.get("magnify")
    said = [t for t in texts if MAGNIFIED in t]
    want = None if k is None or abs(float(k) - 1.0) < 1e-6 else u"sections {0}{1}".format(
        h.gui.fmt_times(k), MAGNIFIED)
    return {"n": shown.get("n"), "magnify": k, "magnify_auto": shown.get("magnify_auto"),
            "texts": texts, "anchors": anchors, "closest_anchors_px": apart,
            "tile_s": [t.get("s") for t in tiles], "tile_eid": [t.get("eid") for t in tiles],
            "no_section": [t["number"] for t in tiles if "no_section" in t],
            "magnify_box": w.magnify_value.Text,
            "check": {"three": "camera" in shown, "count": shown.get("n") == n and len(tiles) == n,
                      "anchors_inside": inside, "anchors_apart": apart is not None and apart > 3.0,
                      "numbers": sorted(b[0] for b in numbers) == list(range(1, n + 1)),
                      "numbers_apart": not overlaps(numbers),
                      "axes": all(a in texts for a in (u"X", u"Y", u"Z")),
                      "magnify_text": (not said) if want is None else
                      (len(said) == 1 and said[0].startswith(want))}}


def view(h, n, three, name):
    """Several sections: n, 2D or 3D; the canvas as a PNG and what it shows."""
    from System import Decimal
    w = h.window
    mark = SPY.mark()
    if not w.sheet_check.Checked:
        w.sheet_check.Checked = True
    if int(w.sheet_count.Value) != n:
        w.sheet_count.Value = Decimal(n)
    (w.radio_3d if three else w.radio_2d).Checked = True
    ok = h.idle()
    out = {"idle": ok, "png": canvas_png(h, name), "frames": SPY.since(mark),
           "tool_parts": list(w.tool_parts), "mode3d": w.mode3d,
           "hint": w.torque_label.Text, "place": w.place_label.Text}
    last = w.last or {}
    out["camera"] = last.get("camera")
    if three:
        out["overlay"] = overlay(h, n)
    else:
        out["check"] = {"two_d": bool(last.get("sheet")) and "camera" not in last,
                        "count": last.get("n") == n and len(last.get("tiles") or []) == n}
    return out


def drag(h, button, dx, dy):
    """Press, one move, wait, release: turn (left) or move (right, middle)."""
    w = h.window
    c = w.canvas
    size = c.ClientSize
    x0, y0 = size.Width // 2, size.Height // 2
    before = dict(w.camera or (w.canvas.shown or {}).get("camera") or {})
    mark = SPY.mark()
    c.OnMouseDown(mouse(h, button, 1, x0, y0))
    c.OnMouseMove(mouse(h, button, 0, x0 + dx, y0 + dy))
    h.idle()
    c.OnMouseUp(mouse(h, button, 1, x0 + dx, y0 + dy))
    h.idle()
    after = dict(w.camera or {})
    frames = SPY.since(mark)
    k = w.k
    out = {"button": button, "dx": dx, "dy": dy, "frames": frames, "before": before,
           "after": after, "one_coarse_one_fine": one_step(frames),
           "camera_is_reply": after == (w.last or {}).get("camera")}
    if button == "left":
        el = min(max(before["elevation"] + dy * h.gui._ROT_DEG / k, -90.0), 90.0)
        out["check"] = (same_angle(after["azimuth"], before["azimuth"] - dx * h.gui._ROT_DEG / k)
                        and abs(after["elevation"] - el) < 1e-6 and
                        after["zoom"] == before["zoom"])
    else:
        p0, p1 = h.gui._pan_of(before), h.gui._pan_of(after)
        out["check"] = (abs(p1[0] - p0[0] - dx) < 1e-6 and abs(p1[1] - p0[1] - dy) < 1e-6 and
                        same_angle(after["azimuth"], before["azimuth"]))
    return out


def post_wheel(h, x, y, delta):
    """WM_MOUSEWHEEL posted to the canvas of this process, through its WndProc."""
    import ctypes
    c = h.window.canvas
    p = c.PointToScreen(h.D.Point(int(x), int(y)))
    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                    ctypes.c_ssize_t]
    lparam = ((p.Y & 0xFFFF) << 16) | (p.X & 0xFFFF)
    if lparam & 0x80000000:
        lparam -= 1 << 32
    return bool(user32.PostMessageW(ctypes.c_void_p(c.Handle.ToInt64()), 0x020A,
                                    (delta & 0xFFFF) << 16, lparam))


def wheel(h, posted):
    """Click, then one wheel step towards a point: zoom by 1.25."""
    w = h.window
    c = w.canvas
    size = c.ClientSize
    x, y = size.Width // 2 + 40, size.Height // 2 - 30
    c.OnMouseDown(mouse(h, "left", 1, x, y))
    c.OnMouseUp(mouse(h, "left", 1, x, y))
    h.idle()
    before = dict(w.camera)
    mark = SPY.mark()
    sent = None
    if posted:
        sent = post_wheel(h, x, y, 120)
    else:
        c.OnMouseWheel(mouse(h, None, 0, x, y, 120))
    h.pump(lambda: SPY.mark() > mark, 5.0)
    h.idle()
    after = dict(w.camera)
    frames = SPY.since(mark)
    cx, cy = 0.5 * size.Width, 0.5 * size.Height
    p0, p1 = h.gui._pan_of(before), h.gui._pan_of(after)
    stay = [abs((x - cx - p0[0]) / before["zoom"] - (x - cx - p1[0]) / after["zoom"]),
            abs((y - cy - p0[1]) / before["zoom"] - (y - cy - p1[1]) / after["zoom"])]
    return {"posted": posted, "post_ok": sent, "frames": frames,
            "one_coarse_one_fine": one_step(frames),
            "zoom": [before["zoom"], after["zoom"]],
            "check": abs(after["zoom"] / before["zoom"] - 1.25) < 1e-9 and max(stay) < 1e-9}


def hover_on(h, number, reach=90, step=5, limit=400):
    """Mouse moves without a button around the anchor of a section, until the
    hover line gives that section with a value on its material."""
    w = h.window
    c = w.canvas
    tile = [t for t in (w.last or {}).get("tiles") or [] if t["number"] == number][0]
    a = tile["anchor"]
    points = [(dx, dy) for dx in range(-reach, reach + 1, step)
              for dy in range(-reach, reach + 1, step)]
    points.sort(key=lambda p: p[0] * p[0] + p[1] * p[1])
    head = u"Section {0}, {1}:".format(number, h.bu.fmt_length(tile["s"]))
    seen = []
    for i, (dx, dy) in enumerate(points[:limit]):
        x, y = int(a[0]) + dx, int(a[1]) + dy
        w.hover_label.Text = u""
        c.OnMouseMove(mouse(h, None, 0, x, y))
        h.idle(5.0)
        text = w.hover_label.Text
        if text and len(seen) < 5 and text not in seen:
            seen.append(text)
        if text.startswith(head) and u"V = " in text and u"value " in text:
            return {"found": True, "x": x, "y": y, "text": text, "tried": i + 1, "seen": seen,
                    "s": tile["s"]}
    return {"found": False, "tried": min(limit, len(points)), "seen": seen, "s": tile["s"]}


def double(h, x, y):
    """A double click as WinForms raises it: down, up, down, double click, up."""
    w = h.window
    c = w.canvas
    picks = len(SPY.picks)
    c.OnMouseDown(mouse(h, "left", 1, x, y))
    c.OnMouseUp(mouse(h, "left", 1, x, y))
    c.OnMouseDown(mouse(h, "left", 2, x, y))
    c.OnMouseDoubleClick(mouse(h, "left", 2, x, y))
    c.OnMouseUp(mouse(h, "left", 2, x, y))
    h.pump(lambda: w.detail is not None, 5.0)
    h.idle()
    return SPY.picks[picks:]


def chain_points(h):
    """The chain as Mecway has it: per element of the window's chain its start and
    end point in the order of the chain."""
    rows = [list(r) for r in h.window.chain["elements"]]

    def work():
        return [[[float(c) for c in h.bmw._xyz(mw.node(n))] for n in mw.nodes(int(r[0]))]
                for r in rows]
    ends = h.call(work)

    def near(p, q):
        return sum((a - b) ** 2 for a, b in zip(p, q)) < 1e-18
    out = []
    for i, (row, pts) in enumerate(zip(rows, ends)):
        a, b = pts[0], pts[-1]
        if i == 0 and len(ends) > 1:
            nxt = ends[1]
            if near(a, nxt[0]) or near(a, nxt[-1]):
                a, b = b, a
        elif i > 0 and not near(a, out[-1][4]):
            a, b = b, a
        out.append([row[0], row[1], row[2], a, b])
    return out


def point_at(chain, s):
    for eid, s0, s1, a, b in chain:
        if s0 - 1e-12 <= s <= s1 + 1e-12:
            t = (s - s0) / (s1 - s0)
            return [p + t * (q - p) for p, q in zip(a, b)]
    return None


def oracle(h, beam, s):
    """|F| and |M| of the cut at s by hand: a cantilever with the tip load of the
    model (F and M at the tip node, no other load)."""
    tip = h.call(lambda: [float(c) for c in h.bmw._xyz(mw.node(beam["tip"]))])
    r = point_at(chain_points(h), s)
    F, M = beam["F"], beam["M"]
    d = [t - p for t, p in zip(tip, r)]
    cross = [d[1] * F[2] - d[2] * F[1], d[2] * F[0] - d[0] * F[2], d[0] * F[1] - d[1] * F[0]]
    m = [a + b for a, b in zip(M, cross)]
    return sum(f * f for f in F) ** 0.5, sum(v * v for v in m) ** 0.5, r


def detail_state(h, tile, n, beam):
    w = h.window
    last = w.last or {}
    f = last.get("forces") or {}
    fmag = sum(f.get(k, 0.0) ** 2 for k in ("N", "Vv", "Vw")) ** 0.5
    mmag = sum(f.get(k, 0.0) ** 2 for k in ("T", "Mv", "Mw")) ** 0.5
    want_f, want_m, r = oracle(h, beam, tile["s"])
    outside = (last.get("outside") or {}).get("value")
    title = u"Section {0} of {1}, s = {2}".format(tile["number"], n,
                                                  h.bu.fmt_length(tile["s"]))
    out = {"number": (w.detail or {}).get("number"), "title": w.detail_title.Text,
           "position": w._position(), "s": last.get("s"), "tile_s": tile["s"],
           "eid": last.get("eid"), "tile_eid": tile.get("eid"), "outside": outside,
           "tile_outside": tile.get("outside"), "forces": f, "hand": [want_f, want_m],
           "point": r, "lo_hi": [last.get("lo"), last.get("hi")], "sheet": last.get("sheet"),
           "tool_parts": list(w.tool_parts), "back": w.back_button.Text,
           "slider": w.slider.Value, "slider_enabled": bool(w.slider.Enabled),
           "marker_enabled": bool(w.marker_check.Enabled)}
    out["check"] = {
        "opened": w.detail is not None and out["number"] == tile["number"],
        "one_section": not last.get("sheet") and not w.sheet_check.Checked,
        "exact_s": out["position"] == tile["s"] and out["s"] == tile["s"],
        "same_element": out["eid"] == out["tile_eid"],
        "same_outside": outside is not None and tile.get("outside") is not None and
        abs(outside - tile["outside"]) <= 1e-9 * max(1.0, abs(tile["outside"])),
        "forces_by_hand": abs(fmag - want_f) <= 1e-6 * max(1.0, want_f) and
        abs(mmag - want_m) <= 1e-6 * max(1.0, want_m),
        "title": out["title"] == title, "row": out["tool_parts"] == [False, False, True],
        "back_text": out["back"] == u"Back to {0} sections".format(n)}
    return out


def shared_scale(h, sheet_lo_hi):
    w = h.window
    out = {"enabled": bool(w.shared_check.Enabled), "own": [w.last.get("lo"), w.last.get("hi")]}
    w.shared_check.Checked = True
    h.idle()
    out["shared"] = [w.last.get("lo"), w.last.get("hi")]
    out["png"] = canvas_png(h, "shared.png")
    w.shared_check.Checked = False
    h.idle()
    out["own_again"] = [w.last.get("lo"), w.last.get("hi")]
    out["check"] = (out["enabled"] and out["shared"] == list(sheet_lo_hi) and
                    out["own_again"] == out["own"])
    return out


def markers(h):
    return list(h.call(lambda: h.bmw.marker_counts(mw)))


def detail_2d(h, beam, n=6):
    """Round 1: a 2D tile by double click, the detail, Shared scale, the slider, Back."""
    w = h.window
    v = view(h, n, False, "detail2d_before.png")
    sheet = dict(w.last)
    tile = sheet["tiles"][2]
    log = {"before": v["png"]}
    log["picks"] = double(h, tile["x"] + tile["w"] // 2, tile["y"] + tile["h"] // 2)
    log["state"] = detail_state(h, tile, n, beam)
    log["png"] = canvas_png(h, "detail2d.png")
    log["shared"] = shared_scale(h, (sheet["lo"], sheet["hi"]))
    length = w.chain["length"]
    w.slider.Value = w.slider.Value + 7
    h.idle()
    log["slider"] = {"title": w.detail_title.Text, "s": w.last.get("s"),
                     "check": w.detail_title.Text == u"" and w.detail_s is None and
                     abs(w.last.get("s") - length * w.slider.Value / 1000.0) < 1e-12}
    w.on_back()
    h.idle()
    after = canvas_png(h, "detail2d_back.png")
    log["back"] = {"png": after, "n": w.last.get("n"), "mode3d": w.mode3d,
                   "check": bool(w.sheet_check.Checked) and w.detail is None and
                   not w.mode3d and w.last.get("n") == n and "camera" not in w.last and
                   after["sha256"] == v["png"]["sha256"]}
    return log


def detail_3d(h, beam, n=12):
    """Round 2: 3D with a turned camera, magnify, a fixed scale; hover to find a
    pixel on the material, double click on it, the detail with the marker on and
    another fixed scale; Back restores all, to the pixel."""
    w = h.window
    log = {}
    view(h, n, True, "detail3d_setup.png")
    log["turn"] = drag(h, "left", 30, -12)
    w.magnify_slider.Value = 11
    h.idle()
    w.fixed_check.Checked = True
    h.idle()
    measure = w._measure()
    hi = max(abs(w.last["lo"]), abs(w.last["hi"])) / 1e6
    w.fixed_lo.Text = u"0"
    w.fixed_hi.Text = h.bu._num(round(1.5 * hi, 1) or 1.0)
    h.idle()
    fixed = list(w.fixed.get(measure) or [])
    camera = dict(w.camera)
    log["fixed"] = fixed
    log["sheet_lo_hi"] = [w.last["lo"], w.last["hi"]]
    number = 3 if n >= 3 else 1
    log["hover"] = hover_on(h, number)
    w.hover_label.Text = u""
    log["before"] = canvas_png(h, "detail3d_before.png")
    log["overlay_before"] = overlay(h, n)
    tile = [t for t in w.last["tiles"] if t["number"] == number][0]
    if log["hover"]["found"]:
        x, y = log["hover"]["x"], log["hover"]["y"]
    else:
        x, y = int(tile["anchor"][0]), int(tile["anchor"][1])
    log["picks"] = double(h, x, y)
    log["by_pixel"] = bool(log["picks"]) and log["picks"][-1]["number"] == number
    log["state"] = detail_state(h, tile, n, beam)
    log["saved"] = dict((k, v) for k, v in (w.detail or {}).items() if k != "shared")
    log["png"] = canvas_png(h, "detail3d.png")
    # in the detail: another fixed scale and the position in Mecway
    w.fixed_hi.Text = h.bu._num(round(3.0 * hi, 1) or 2.0)
    h.idle()
    log["detail_fixed"] = list(w.fixed.get(measure) or [])
    log["markers_before"] = markers(h)
    w.marker_check.Checked = True
    h.pump(lambda: w.marker_on, 30.0)
    h.idle()
    log["markers_on"] = markers(h)
    w.on_back()
    h.pump(lambda: not w.tickets, 30.0)
    h.idle()
    log["markers_after"] = markers(h)
    w.hover_label.Text = u""
    after = canvas_png(h, "detail3d_back.png")
    last = w.last or {}
    log["back"] = {
        "png": after, "n": last.get("n"), "camera": last.get("camera"),
        "magnify": last.get("magnify"), "lo_hi": [last.get("lo"), last.get("hi")],
        "fixed": list(w.fixed.get(measure) or []),
        "check": {"n": int(w.sheet_count.Value) == n and last.get("n") == n,
                  "three": w.mode3d and "camera" in last and
                  tuple(w.tool_parts) == (True, True, False),
                  "camera": same_camera(last.get("camera"), camera),
                  "magnify": w.magnify_slider.Value == 11 and
                  abs(last.get("magnify", 0) - h.gui.magnify_of(11)) < 1e-12,
                  "fixed": bool(w.fixed_check.Checked) and
                  list(w.fixed.get(measure) or []) == fixed and
                  [last.get("lo"), last.get("hi")] == fixed,
                  "marker_option": not w.marker_check.Checked and not w.marker_on,
                  "marker_in_detail": log["markers_on"][1] > 0,
                  "marker_gone": log["markers_after"] == [0, 0],
                  "same_pixels": after["sha256"] == log["before"]["sha256"]}}
    path = os.path.join(FOLDER, "after_back.liml")
    h.call(lambda: mw.save_as(path))
    log["saved_file"] = sha(path)
    w.fixed_check.Checked = False
    w.fixed = {}
    w.magnify_slider.Value = 0
    h.idle()
    return log


def anchor_double(h, beam, n=6):
    """A double click exactly on the number of a section (its anchor), and Back."""
    w = h.window
    view(h, n, True, "anchor_before.png")
    tile = w.last["tiles"][1]
    picks = double(h, int(tile["anchor"][0]), int(tile["anchor"][1]))
    log = {"picks": picks, "state": detail_state(h, tile, n, beam)}
    w.on_back()
    h.idle()
    return log


def times(h, steps=30, n=12):
    """Medians of coarse and fine frames during turning, ms, in Mecway."""
    view(h, n, True, "times.png")
    mark = SPY.mark()
    ok = 0
    for i in range(steps):
        if drag(h, "left", 10 if i % 2 == 0 else -10, 0)["one_coarse_one_fine"]:
            ok += 1
    frames = SPY.since(mark)
    med = h.gui._median
    out = {"steps": steps, "one_coarse_one_fine": ok}
    for kind, sel in (("coarse", True), ("fine", False)):
        rows = [f for f in frames if f["coarse"] == sel]
        worker = [sum(float((f["worker"] or {}).get(k) or 0.0) for k in ("compute", "map", "paint"))
                  for f in rows]
        out[kind] = {"count": len(rows), "round_trip_ms": med([f["ms"] for f in rows]),
                     "round_trip_p95": h.gui._p95([f["ms"] for f in rows]),
                     "worker_ms": med(worker)}
    return out


def burst(h):
    """Five moves without waiting: the older requests are replaced, the last counts."""
    w = h.window
    c = w.canvas
    size = c.ClientSize
    x0, y0 = size.Width // 2, size.Height // 2
    replaced = w.link.slot.replaced
    mark = SPY.mark()
    c.OnMouseDown(mouse(h, "left", 1, x0, y0))
    for i in range(1, 6):
        c.OnMouseMove(mouse(h, "left", 0, x0 + 12 * i, y0))
    asked = dict(w.camera)
    h.idle()
    c.OnMouseUp(mouse(h, "left", 1, x0 + 60, y0))
    h.idle()
    frames = SPY.since(mark)
    return {"frames": len(frames), "replaced": w.link.slot.replaced - replaced,
            "check": same_angle(w.last["camera"]["azimuth"], asked["azimuth"]) and
            w.camera == w.last["camera"] and len(frames) < 10}


def resize(h):
    w = h.window
    D = h.D
    form = w.form
    camera = dict(w.camera)
    mark = SPY.mark()
    form.ClientSize = D.Size(1000, 700)
    h.idle()
    frames = SPY.since(mark)
    out = {"frames": frames, "camera": w.last.get("camera"),
           "size": [w.canvas.ClientSize.Width, w.canvas.ClientSize.Height],
           "dib": list(w.canvas.dib[1:]) if w.canvas.dib else None}
    out["check"] = (same_angle(w.last["camera"]["azimuth"], camera["azimuth"]) and
                    abs(w.last["camera"]["elevation"] - camera["elevation"]) < 1e-9 and
                    out["dib"] == out["size"] and any(f["coarse"] for f in frames))
    form.ClientSize = D.Size(CANVAS[0], CANVAS[1])
    h.idle()
    return out


def reset(h):
    w = h.window
    w.contour_check.Checked = False
    w.fixed_check.Checked = False
    w.fixed = {}
    w.magnify_slider.Value = 0
    w.measure_list.SelectedIndex = 0
    h.idle()


def view3d_body(h):
    global SPY
    w = h.window
    SPY = Spy(w)
    log = RESULT["view3d"] = {"settings_at_start": {
        "sheet_view": h.bu.settings().get("sheet_view"),
        "contours": h.bu.settings().get("contours")}}
    log["start_ok"] = h.start()
    w.radio_2d.Checked = True
    reset(h)
    info = json.loads(open(JOB.get("info3d") or JOB["info"]).read())
    beams = log["beams"] = {}
    for i, (selection, key) in enumerate(JOB["beams"]):
        beam = info["beams"][key]
        row = beams[selection] = {}
        mark = SPY.mark()
        row["select_ok"] = h.select(selection)
        first = [f for f in SPY.since(mark) if f["gen"] == w.gen]
        row["switch"] = {"in_3d": i > 0, "first_camera_sent":
                         first[0]["camera_sent"] if first else None,
                         "first_three": first[0]["three"] if first else None,
                         "old_gen_frames": len([f for f in SPY.since(mark) if f["gen"] != w.gen]),
                         "camera_is_new": i == 0 or not same_camera(
                             (w.last or {}).get("camera"), log.get("left_camera"))}
        reset(h)
        row["chain"] = dict(w.chain or {})
        row["warnings"] = w.warn_box.Text
        row["reversed_in_model"] = info.get("reversed")
        save()
        row["views"] = {}
        PREFIX[0] = key + u"_"
        for n, three, name in ((6, False, "6_2d"), (6, True, "6_3d"), (12, True, "12_3d"),
                               (12, False, "12_2d"), (12, True, "12_3d_again")):
            row["views"][name] = step(name, view, h, n, three, name + ".png")
            save()
        if key == "kinked":
            row["views"]["4_3d"] = step("4 on the kinks", view, h, 4, True, "4_3d.png")
        default = dict(w.camera or {})
        twice = row["views"]["12_3d"], row["views"]["12_3d_again"]
        row["camera_after_2d"] = same_camera(twice[0]["camera"], twice[1]["camera"])
        row["turn"] = step("turn", drag, h, "left", 40, 16)
        row["move_right"] = step("move right", drag, h, "right", 30, -10)
        row["move_middle"] = step("move middle", drag, h, "middle", 0, 20)
        row["wheel"] = step("wheel", wheel, h, False)
        row["wheel_posted"] = step("wheel posted", wheel, h, True)
        row["burst"] = step("burst", burst, h)
        row["turned_png"] = canvas_png(h, "turned.png")
        # Fit: the default view again, without a camera in the request. Its zoom fits
        # the sections of now; the camera of the first 3D frame was kept through the
        # changes of n and fits the magnification of then.
        mark = SPY.mark()
        w.on_fit()
        h.idle()
        fit = dict(w.camera or {})
        row["fit"] = {"camera": fit, "default": default, "frames": SPY.since(mark),
                      "check": bool(SPY.since(mark)) and
                      not any(f["camera_sent"] for f in SPY.since(mark)) and
                      same_angle(fit["azimuth"], default["azimuth"]) and
                      fit["elevation"] == default["elevation"] and
                      list(fit["pan"]) == [0.0, 0.0] and fit["target"] == default["target"]}
        w.hover_label.Text = u""
        row["hover"] = step("hover", hover_on, h, 2)
        save()
        row["detail_2d"] = step("detail 2d", detail_2d, h, beam)
        save()
        row["detail_3d"] = step("detail 3d", detail_3d, h, beam)
        save()
        row["anchor"] = step("anchor double click", anchor_double, h, beam)
        if i == 0:
            row["resize"] = step("resize", resize, h)
            row["times"] = step("times", times, h)
        # left turned in 3D: the next beam must come with its own camera
        view(h, 6, True, "last.png")
        drag(h, "left", 50, 0)
        log["left_camera"] = dict(w.camera or {})
        save()


def view3d_case():
    RESULT["version"] = str(mw.version())
    if JOB.get("solve", True):
        mw.solve()
    h = Offscreen()
    h.bu.save_setting = lambda key, value: None         # not into the real settings
    h.run(view3d_body)


CASES = {"numbers": numbers_case, "diff": diff_case, "shells": shells_case, "gap": gap_case, "gap_open": gap_open_case, "setvar": setvar_case, "setvar_open": setvar_open_case, "aster": aster_case, "config": config_case, "buckling": sets_case, "selections": selections_case,
         "marker_modal": marker_solve_case, "api": api_case, "api_modal": api_case, "api_ccx": api_case,
         "api_steps_ccx": api_case, "internal": internal_case, "ccx": ccx_case,
         "modal": sets_case, "steps": sets_case, "marker": marker_case,
         "marker_solve": marker_solve_case, "marker_solve_ccx": marker_solve_case,
         "marker_stale": marker_stale_case, "stale_open": crash_open_case, "crash_save": crash_save_case,
         "crash_open": crash_open_case, "big": big_case,
         "view3d": view3d_case}

try:
    RESULT["started"] = time.time()
    sys.path.insert(0, os.path.join(JOB["repo"], "Scripts"))
    step(JOB["case"], CASES[JOB["case"]])
except Exception:
    RESULT["errors"].append(traceback.format_exc())
save()
