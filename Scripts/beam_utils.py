# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# beam_utils.py
# What the panel (IronPython 2.7.9) and the worker (CPython 3) share:
#   settings()/save_setting()   settings in the work folder
#   language()/tr(nl, en)       Dutch or English user text
#   log(text), work_dir()       log file, work folder (WorkDir next to Scripts)
#   find_python()               a python.exe with numpy and scipy
#   FORCES, MEASURES, RAMP      names of forces and measures, the colour ramp
#   pack_request ... Reply      the pipe protocol between panel and worker
#   RequestSlot                 which request goes next: control commands in
#                               order, of the frames only the newest
#   scan_head(path)             analysis, solver, configurations from a .liml
#   UpAxis(paths).z_top()       Mecway's up axis (its option) for the 3D view
#   warning_text(code, data)    user text of a warning code
#   fmt_*                       numbers for the window
#
# Settings (WorkDir/beam_settings.json, all optional):
#   "language": "nl" or "en"; default the Windows display language
#   "python":   python.exe with numpy and scipy; default searched, see
#               find_python (Code-Aster's Python first; never pip in it)
#   "python_found": the Python the search found last
#   "contours": the check box Contour lines

import io
import json
import os
import re
import string
import struct
import tempfile
import threading
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL = u"MecwayAdvancedBeamAnalysis"
# Version of the tool (window title, log, CHANGELOG.md), as in MecwayAsterBridge
VERSION = u"V2026.2"
LOG_NAME = "beam_log.txt"
WORKER_LOG = "beam_worker_log.txt"
SETTINGS_NAME = "beam_settings.json"
_DEFAULTS = {"language": "", "python": "", "python_found": "", "contours": False}
# Oldest scipy with cKDTree.query_ball_point(return_length=...), which
# section_mesh needs.
MIN_SCIPY = (1, 3)
# -E: ignore PYTHONPATH and PYTHONHOME, keep the user's site-packages.
PYTHON_FLAGS = "-E"
_PROBE = ("import sys, numpy, scipy, scipy.optimize, scipy.sparse, "
          "scipy.spatial; sys.stdout.write('BEAMPY %d %s %s\\n' % ("
          "sys.version_info[0], numpy.__version__, scipy.__version__))")
_CACHE = {}

# Section forces in the order of the window and their Mecway names (research
# note par. 7, E2).
FORCES = ("N", "Vv", "Vw", "T", "Mv", "Mw")
MECWAY_VARS = {"N": "tensileforce", "Vv": "sheary", "Vw": "shearz",
               "T": "torsion", "Mv": "bendingmomv", "Mw": "bendingmomw"}
# (key of section_stress.measures, Dutch, English)
MEASURES = (("vonmises", u"Von Mises", u"Von Mises"),
            ("sigma", u"σ langs de balk", u"σ along the beam"),
            ("tau", u"τ totaal", u"τ total"),
            ("tau_v", u"τ langs V", u"τ along V"),
            ("tau_w", u"τ langs W", u"τ along W"),
            ("tresca", u"Tresca", u"Tresca"),
            ("principal1", u"Hoofdspanning 1", u"Principal stress 1"),
            ("principal2", u"Hoofdspanning 2", u"Principal stress 2"))
# What "Write to Mecway results" writes: (key of section_stress.measures, node variable)
RESULT_VARS = (("vonmises", "sectionvonmises"), ("sigma", "sectionsigma"), ("tau", "sectiontau"))
# Temporary marker of "Show position in Mecway": component and material of this
# series, Mecway adds (2), (3) to a name in use.
MARKER_PREFIX = u"Balkpositie_tijdelijk"
_MARKER_NAME = re.compile(u"^" + re.escape(MARKER_PREFIX) + r"(\(\d+\))?$")
# Parts of the colour scale between the ticks of the legend; the contour lines lie
# on those ticks, so legend and lines cannot differ.
LEGEND_STEPS = 5
# Colour ramp: (position 0..1, red, green, blue); the worker makes its look-up
# table from it and the panel the legend, so the two cannot differ.
RAMP = ((0.0, 0, 0, 200), (0.2, 0, 120, 255), (0.4, 0, 220, 200),
        (0.6, 120, 230, 40), (0.8, 255, 200, 0), (1.0, 220, 20, 20))


def is_marker_name(name):
    return bool(name) and _MARKER_NAME.match(name) is not None


def work_dir():
    """The work folder (created): WorkDir next to the Scripts folder, the temp
    folder when that cannot be made."""
    folder = os.path.join(os.path.dirname(SCRIPTS_DIR), "WorkDir")
    if not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except OSError:
            folder = tempfile.gettempdir()
    return folder


def settings():
    """The settings, defaults filled in (read once per module load)."""
    if "settings" not in _CACHE:
        values = dict(_DEFAULTS)
        try:
            with io.open(os.path.join(work_dir(), SETTINGS_NAME),
                         encoding="utf-8") as f:
                values.update(json.loads(f.read()))
        except (IOError, OSError, ValueError):
            pass
        _CACHE["settings"] = values
    return _CACHE["settings"]


def save_setting(key, value):
    """Store one setting; a failure (read-only folder) is only logged."""
    values = settings()
    values[key] = value
    try:
        with io.open(os.path.join(work_dir(), SETTINGS_NAME), "w",
                     encoding="utf-8") as f:
            f.write(u"{0}".format(json.dumps(values, indent=1,
                                             ensure_ascii=False)))
    except (IOError, OSError) as e:
        log("WARNING: settings not saved: {0}".format(e))


def _windows_language():
    try:
        from System.Globalization import CultureInfo
        name = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName
        return "nl" if name == "nl" else "en"
    except Exception:
        return "en"


def language():
    """ "nl" or "en": the setting, else the Windows display language."""
    lang = settings().get("language")
    return lang if lang in ("nl", "en") else _windows_language()


def tr(nl, en):
    """Text in the current language (Dutch first, then English)."""
    return nl if language() == "nl" else en


def log(text):
    """Append a line with a time stamp to the log; never raises."""
    try:
        with io.open(os.path.join(work_dir(), LOG_NAME), "a",
                     encoding="utf-8") as f:
            f.write(u"[{0}] {1}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"),
                                          text))
    except Exception:
        pass


# ==========================================
# THE PYTHON WITH NUMPY AND SCIPY (as MecwaySectionProps)
# ==========================================
def _version_key(name):
    return [int(p) for p in re.findall(r"\d+", name)]


def _aster_pythons():
    """Pythons of the Code-Aster installations under %LOCALAPPDATA%\\code_aster,
    newest first (e.g. v2025\\17.4\\Python311\\python.exe)."""
    root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "code_aster")
    found = []
    try:
        releases = os.listdir(root)
    except OSError:
        return []
    for rel in releases:
        rel_dir = os.path.join(root, rel)
        if not os.path.isdir(rel_dir):
            continue
        for sub in os.listdir(rel_dir):
            sub_dir = os.path.join(rel_dir, sub)
            if sub.lower().startswith("python"):
                found.append(([_version_key(rel), [-1]],
                              os.path.join(sub_dir, "python.exe")))
            elif os.path.isdir(sub_dir):
                for py in os.listdir(sub_dir):
                    if py.lower().startswith("python"):
                        found.append(([_version_key(rel), _version_key(sub)],
                                      os.path.join(sub_dir, py, "python.exe")))
    found = [f for f in found if os.path.isfile(f[1])]
    return [f[1] for f in sorted(found, key=lambda f: f[0], reverse=True)]


def _plain_pythons():
    """Other Pythons of this PC: the PATH (not the Microsoft Store stub in
    WindowsApps) and the default install folders, newest first."""
    found = []
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip('" ')
        exe = os.path.join(folder, "python.exe")
        if (os.path.isabs(folder) and "windowsapps" not in exe.lower() and
                os.path.isfile(exe)):
            found.append(exe)
    tops = []
    for name in ("LOCALAPPDATA", "ProgramFiles"):
        top = os.environ.get(name, "")
        if top:
            tops.append(os.path.join(top, "Programs", "Python"))
            tops.append(top)
    for top in tops:
        try:
            names = os.listdir(top)
        except OSError:
            continue
        for name in names:
            exe = os.path.join(top, name, "python.exe")
            if name.lower().startswith("python") and os.path.isfile(exe):
                found.append(exe)
    return sorted(found, key=lambda p: _version_key(
        os.path.basename(os.path.dirname(p))), reverse=True)


def _run_quiet(exe, args, timeout=60):
    """(exit code, stdout) of a program, without a window; (None, "") when it
    cannot be started or does not end in time."""
    try:
        import clr
    except ImportError:
        clr = None
    try:
        if clr is None:
            import subprocess
            proc = subprocess.Popen([exe] + list(args),
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL,
                                    creationflags=0x08000000)
            try:
                out = proc.communicate(timeout=timeout)[0]
            except subprocess.TimeoutExpired:
                proc.kill()
                return None, ""
            return proc.returncode, out.decode("utf-8", "replace")
        clr.AddReference("System")
        from System.Diagnostics import Process, ProcessStartInfo
        info = ProcessStartInfo(exe)
        info.Arguments = u" ".join(u'"{0}"'.format(a.replace('"', '\\"'))
                                   for a in args)
        info.UseShellExecute = False
        info.CreateNoWindow = True
        info.RedirectStandardOutput = True
        proc = Process.Start(info)
        # read before waiting: a full pipe would stop the child
        out = proc.StandardOutput.ReadToEnd()
        if not proc.WaitForExit(int(timeout * 1000)):
            proc.Kill()
            return None, ""
        return proc.ExitCode, out
    except Exception as e:
        log("WARNING: could not run {0}: {1}".format(exe, e))
        return None, ""


def check_python(path):
    """(ok, reason) of a python.exe, with the flags the worker runs with."""
    code, out = _run_quiet(path, [PYTHON_FLAGS, "-c", _PROBE])
    if code is None:
        return False, "no_run"
    words = [w for line in out.splitlines() if line.startswith("BEAMPY")
             for w in line.split()]
    if code != 0 or len(words) != 4:
        return False, "no_packages"
    if words[1] != "3" or tuple(_version_key(words[3])[:2]) < MIN_SCIPY:
        return False, "old"
    return True, "numpy {0}, scipy {1}".format(words[2], words[3])


def expand_path(path):
    if not path:
        return ""
    return os.path.expanduser(os.path.expandvars(u"{0}".format(path).strip()))


def find_python(refresh=False):
    """The Python for the worker: {"path": python.exe or None, "pip": a Python
    that runs but lacks the packages or None, "own": the path from the
    settings or ""}. Order: the setting "python" (nothing else when it is set),
    the Python found last, Code-Aster, the other Pythons of this PC. A Python
    of Code-Aster is never offered for pip."""
    values = settings()
    own = expand_path(values.get("python"))
    if own:
        ok, why = check_python(own) if os.path.isfile(own) else (False, "")
        if ok:
            return {"path": own, "pip": None, "own": own}
        mine = why == "no_packages" and own not in _aster_pythons()
        return {"path": None, "pip": own if mine else None, "own": own}
    known = expand_path(values.get("python_found"))
    if known and os.path.isfile(known) and not refresh:
        return {"path": known, "pip": None, "own": ""}
    aster = _aster_pythons()
    pip = None
    seen = set()
    for path in aster + _plain_pythons():
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        ok, why = check_python(path)
        if ok:
            log("INFO: Python {0} ({1}).".format(path, why))
            save_setting("python_found", path)
            return {"path": path, "pip": None, "own": ""}
        if pip is None and why != "no_run" and path not in aster:
            pip = path
    return {"path": None, "pip": pip, "own": ""}


def forget_python():
    """Search again next time (the Python found last no longer works)."""
    save_setting("python_found", "")


def pip_command(python):
    return u'"{0}" -m pip install numpy scipy'.format(python)


# ==========================================
# PROTOCOL BETWEEN PANEL AND WORKER
# ==========================================
# Stream of records over stdin and stdout of the worker, binary.
# Request: REQUEST (seq, n), then n bytes of UTF-8 JSON.
# Reply:   REPLY (magic, seq, json bytes, width, height, pixel bytes), then the
#          JSON, then the pixels (BGRA, top row first).
# The worker sends one reply per request, plus one with seq 0 at its start
# ("hello"). It stops when stdin ends.
REQUEST = struct.Struct("<II")
REPLY = struct.Struct("<IIIIII")
MAGIC = 0x4D414231
# Sanity limits on a header read from the pipe, so that garbage cannot ask for
# gigabytes.
MAX_JSON = 64 * 1024 * 1024
MAX_SIDE = 16384


def _text(obj):
    """JSON text; ensure_ascii=False, because IronPython fails on non-ASCII
    with True (paths with accents)."""
    text = json.dumps(obj, ensure_ascii=False)
    return text if isinstance(text, type(u"")) else text.decode("utf-8")


def pack_request(seq, obj):
    body = _text(obj).encode("utf-8")
    return REQUEST.pack(seq, len(body)) + body


def pack_reply_head(seq, obj, width=0, height=0):
    """Header and JSON of a reply; the pixels follow."""
    body = _text(obj).encode("utf-8")
    return (REPLY.pack(MAGIC, seq, len(body), width, height,
                       width * height * 4) + body)


def valid_reply_head(magic, n_json, width, height, n_pixels):
    return (magic == MAGIC and n_json <= MAX_JSON and width <= MAX_SIDE and
            height <= MAX_SIDE and n_pixels == width * height * 4)


class RequestSlot(object):
    """What the link thread sends next; every call is thread safe.

    Control commands (open, forces) go in order and are never dropped. Of the
    frames only the newest waits: a frame request is a complete state of the
    window, so an older one may be replaced. After a coarse frame the same
    state follows as a fine frame, unless something newer came. A probe (hover)
    is sent last: only when nothing else waits.

    A new control command of the kind "open" drops the waiting frame and
    probe: they belong to the beam before it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._control = []
        self._frame = None
        self._refine = None
        self._probe = None
        self.replaced = 0

    def put_control(self, request):
        with self._lock:
            if request.get("cmd") == "open":
                self._frame = self._refine = self._probe = None
            self._control.append(request)

    def put_frame(self, request):
        with self._lock:
            if self._frame is not None:
                self.replaced += 1
            self._frame = request
            self._refine = None

    def put_probe(self, request):
        with self._lock:
            self._probe = request

    def take(self):
        """The next request, or None when nothing waits."""
        with self._lock:
            if self._control:
                return self._control.pop(0)
            if self._frame is not None:
                request, self._frame = self._frame, None
                if request.get("coarse"):
                    fine = dict(request)
                    fine["coarse"] = False
                    self._refine = fine
                return request
            if self._refine is not None:
                request, self._refine = self._refine, None
                return request
            request, self._probe = self._probe, None
            return request

    def empty(self):
        with self._lock:
            return not (self._control or self._frame or self._refine or
                        self._probe)


# ==========================================
# MECWAY'S UP AXIS
# ==========================================
# Mecway keeps its up-axis option (Tools > Options > General) as the .NET user
# setting ViewZTop, True for Z up, in its user.config; its own default stands in the
# <userSettings> of mecway.exe.config (measured on Mecway 35: False, so Y up). The
# paths come from beam_mecway.config_paths(). The setting name is no contract of
# Mecway: without it the 3D view takes Y up and the log says so once.
_SETTING = re.compile(r'<setting\b[^>]*\bname\s*=\s*"ViewZTop"[^>]*?(/>|>(.*?)</setting>)',
                      re.S)
_VALUE = re.compile(r'<value\s*>(.*?)</value\s*>|<value\s*/>', re.S)


def parse_z_top(text):
    """True or False from the first ViewZTop setting of a .config text, None when
    there is none or its value is no True or False."""
    if not text:
        return None
    m = _SETTING.search(text)
    if m is None or m.group(2) is None:
        return None
    v = _VALUE.search(m.group(2))
    word = (v.group(1) or u"").strip().lower() if v is not None else u""
    return {u"true": True, u"false": False}.get(word)


def _read_config(path):
    """The text of a .config file (BOM or not), None when there is no such file;
    raises IOError when it exists but cannot be read."""
    if not path or not os.path.isfile(path):
        return None
    with io.open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


class UpAxis(object):
    """Mecway's up axis for the default 3D camera. paths: (user.config, default
    .config), either may be None. z_top() reads the files (a few kB) and is called
    only when the default camera is needed; known() gives the last answer without
    reading. A file that exists but cannot be read keeps the last good answer;
    with no answer at all it is False (Y up, Mecway's default)."""

    def __init__(self, paths=(None, None), say=None):
        self.paths = tuple(paths) if paths else (None, None)
        self.value = None
        self.said = None
        self.say = say or log

    def known(self):
        return bool(self.value)

    def z_top(self):
        found = None
        for path in self.paths:
            try:
                found = parse_z_top(_read_config(path))
            except Exception:
                # exists but cannot be read now (Mecway writing it): the last
                # good answer, not the default behind it
                found = self.value
            if found is not None:
                break
        note = (u"INFO: Mecway's up axis: {0}".format(u"Z" if found else u"Y")
                if found is not None else
                u"INFO: Mecway's up-axis setting not found; Y up assumed")
        if note != self.said:
            self.said = note
            self.say(note)
        # what known() gives for a kept camera: the axis the default used last
        self.value = bool(found)
        return self.value


# ==========================================
# THE HEAD OF A SAVED MODEL
# ==========================================
_SELECTION = re.compile(r'<(element|face|node)selection\b([^>]*)>')
_CONFIGURATION = re.compile(r'<configuration\b[^>]*\bname="([^"]*)"')
_ELSET = re.compile(r'<elset\b[^>]*\bname="([^"]*)"')
_ANALYSIS = re.compile(r'<analysis\b([^>]*)>')
_ATTRIBUTE = re.compile(r'(\w+)="([^"]*)"')
_FIELD = re.compile(r'<fieldvariable\b[^>]*\bname="([^"]*)"')
# The field variables Mecway needs to store section forces (research note, E6), as
# Mecway itself writes them in a saved .liml (measured)
FORCE_FIELDS = (u"tensile force", u"shear force", u"torsion moment", u"bending moment 2")
_ENTITY = re.compile(r"&(#[0-9]+|#x[0-9a-fA-F]+|amp|lt|gt|quot|apos);")
_NAMED = {"amp": u"&", "lt": u"<", "gt": u">", "quot": u'"', "apos": u"'"}


def _unescape(text):
    """An attribute value as the XML parser gives it: the names in the head must be
    the names that beam_model reads and that Mecway knows."""
    def one(match):
        key = match.group(1)
        if key.startswith("#x"):
            return unichr_(int(key[2:], 16))
        if key.startswith("#"):
            return unichr_(int(key[1:]))
        return _NAMED[key]
    return _ENTITY.sub(one, text)


try:
    unichr_ = unichr
except NameError:
    unichr_ = chr


_ENTITY = re.compile(r"&(#x[0-9a-fA-F]+|#[0-9]+|amp|lt|gt|quot|apos);")
_NAMED = {"amp": u"&", "lt": u"<", "gt": u">", "quot": u'"', "apos": u"'"}
try:
    _char = unichr      # IronPython 2.7
except NameError:
    _char = chr


def _unescape(text):
    """An attribute value as the API gives the name: the XML entities resolved. How
    Mecway escapes names is NOT verified; resolving is harmless either way."""
    def one(match):
        key = match.group(1)
        try:
            if key.startswith("#x"):
                return _char(int(key[2:], 16))
            if key.startswith("#"):
                return _char(int(key[1:]))
        except (ValueError, OverflowError):
            return match.group(0)       # no character: left as it stands
        return _NAMED[key]
    return _ENTITY.sub(one, text)


def scan_head(path):
    """What the API cannot tell, from the saved file up to the first <solution>:
    {"analysis", "solver", "configurations", "node_selections", "element_selections",
    "fields"}, and (node_selections: every selection the list of beams leaves out;
    element_selections: the names of every element and face selection in the file,
    direct or not, None when the file cannot be read; the API gives ints for node and
    element selections alike, measured)
    "aster": the solution came from Code-Aster, see _from_bridge; "shells": component
    names only the solution has, the shells of the bridge's "Section in results". Runs
    in the worker, line by line, so the size of the file does not bother the panel."""
    out = {"analysis": None, "solver": "internal", "configurations": [],
           "node_selections": [], "element_selections": None, "fields": [],
           "aster": False, "shells": []}
    elsets, chosen, direct = set(), set(), []
    if not path:
        return out
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            out["element_selections"] = []
            for line in f:
                if "<solution" in line:
                    out["aster"], copy = _from_bridge(f)
                    out["shells"] = sorted(n for n in copy - elsets if not is_marker_name(n))
                    break
                if "<" not in line:
                    continue
                out["fields"].extend(_FIELD.findall(line))
                elsets.update(_ELSET.findall(line))
                match = _ANALYSIS.search(line)
                if match and out["analysis"] is None:
                    attrs = dict(_ATTRIBUTE.findall(match.group(1)))
                    out["analysis"] = attrs.get("type")
                    out["solver"] = attrs.get("solver") or "internal"
                for name in _CONFIGURATION.findall(line):
                    name = _unescape(name)
                    if name not in out["configurations"]:
                        out["configurations"].append(name)
                for kind, text in _SELECTION.findall(line):
                    attrs = dict(_ATTRIBUTE.findall(text))
                    name = attrs.get("name")
                    if name is None:
                        continue
                    name = _unescape(name)
                    if kind == "node":
                        out["node_selections"].append(name)
                        continue
                    out["element_selections"].append(name)
                    if attrs.get("direct") == "true":
                        direct.append(name)
                    else:
                        chosen.add(name)
        # direct="true" taken as made with a load or constraint, not in Mecway's tree,
        # so no choice the user knows: NOT verified on a file saved from the program.
        # So only when the name does not also stand as an ordinary selection.
        out["node_selections"].extend(n for n in direct if n not in chosen)
    except (IOError, OSError) as e:
        out["element_selections"] = None
        log("WARNING: could not read {0}: {1}".format(path, e))
    return out


def _from_bridge(lines):
    """(bridge, component names of the solution's copy of the model). bridge: True
    when the results after the <solution> just read are those of MecwayAsterBridge:
    its fragment has empty <nodes /> and <values /> in every node
    variable (bridge 6efd62a); the results of Mecway 35 have them not (measured). NOT
    verified on Mecway 34 and older: the bridge's own format note says Mecway 33 wrote
    these tags, so there a solution of its own could read as Code-Aster's. The analysis
    line says nothing: it stays that of the model. Mecway 35 drops the tags when the
    user saves the opened result, so a saved one reads as Mecway's own (measured). With
    configurations the first solution in the file decides for all."""
    inside = False
    names = set()
    for line in lines:
        if not inside:
            inside = "<results" in line
            names.update(_ELSET.findall(line))
            if "</solution" in line:
                return False, names
        elif "</nodevariable>" in line:
            return False, names
        elif "<nodes />" in line:
            return True, names
    return False, names


# ==========================================
# NUMBERS IN THE WINDOW
# ==========================================
def _num(value, digits=4):
    """A number with `digits` significant digits, no locale, no exponent for
    ordinary sizes."""
    if value is None:
        return u"–"
    v = float(value)
    if v == 0.0:
        return u"0"
    a = abs(v)
    if a >= 1e7 or a < 1e-4:
        return u"{0:.{1}e}".format(v, digits - 1).replace(u"e+0", u"e").replace(
            u"e-0", u"e-")
    import math
    dec = max(0, digits - 1 - int(math.floor(math.log10(a))))
    return u"{0:.{1}f}".format(v, dec)


def fmt_stress(pa):
    """Stress in MPa from Pa."""
    return u"{0} MPa".format(_num(None if pa is None else pa / 1e6))


def fmt_force(n):
    return u"{0} N".format(_num(n))


def fmt_moment(nm):
    return u"{0} N·m".format(_num(nm))


def fmt_length(m):
    return u"{0} mm".format(_num(None if m is None else m * 1e3))


def parse_number(text):
    """A float from a text box, with a decimal point or comma; None when it is
    no number."""
    try:
        return float(u"{0}".format(text).strip().replace(u",", u"."))
    except ValueError:
        return None


# ==========================================
# WARNINGS: codes with data, text only here
# ==========================================
# Codes of the worker, of beam_model and of section_profiles.
_WARNINGS = {
    # the window and the worker
    "no_file": (u"Het model is niet opgeslagen. Sla het op in Mecway: ori\u00ebntatie "
                u"en doorsnede staan alleen in het bestand.",
                u"The model is not saved. Save it in Mecway: orientation and "
                u"section exist only in the file."),
    "file_differs": (u"Het opgeslagen bestand wijkt af van het model in Mecway "
                     u"({what}). Sla het model op en kies Opnieuw lezen.",
                     u"The saved file differs from the model in Mecway ({what}). "
                     u"Save the model and choose Read again."),
    # an int selection whose name the saved file does not hold as element or face
    # selection: made or renamed after the last save (selection_unreadable: the file
    # could not be read)
    "selection_unsaved": (u"Selectie {name} staat nog niet in het opgeslagen bestand. "
                          u"Via de API is een knoopselectie niet van een elementselectie te "
                          u"onderscheiden. Sla het model op en kies Opnieuw lezen.",
                          u"Selection {name} is not in the saved file yet. Through the API "
                          u"a node selection cannot be told from an element selection. "
                          u"Save the model and choose Read again."),
    "selection_unreadable": (u"Selectie {name}: het opgeslagen bestand is niet te lezen, "
                             u"dus is niet te zeggen of dit een knoop- of een "
                             u"elementselectie is. Sla het model op en kies Opnieuw lezen.",
                             u"Selection {name}: the saved file cannot be read, so it "
                             u"cannot be told whether this is a node or an element "
                             u"selection. Save the model and choose Read again."),
    "route_node": (u"Geen elementwaarden van deze solver: snedekrachten uit de "
                   u"knoopwaarden, sprongen in een gedeelde knoop zijn uitgemiddeld.",
                   u"No element values from this solver: section forces from the "
                   u"node values, jumps at a shared node are averaged."),
    "route_kinematics": (u"Geen snedekrachten opgeslagen: alleen \u03c3, uit de "
                         u"verplaatsingen en rotaties. Voeg de veldvariabelen tensile "
                         u"force, shear force, torsion moment en bending moment 2 toe "
                         u"aan het model en los opnieuw op voor \u03c4 en von Mises.",
                         u"No section forces stored: \u03c3 only, from the "
                         u"displacements and rotations. Add the field variables "
                         u"tensile force, shear force, torsion moment and bending "
                         u"moment 2 to the model and solve again for \u03c4 and von "
                         u"Mises."),
    "route_kinematics_result": (u"Dit resultaat bevat geen snedekrachten, ook al staan de "
                                u"veldvariabelen in het model (gemeten: Mecway bewaart ze niet "
                                u"bij modes). Alleen \u03c3, uit de verplaatsingen en rotaties.",
                                u"This result holds no section forces, although the field "
                                u"variables are in the model (measured: Mecway does not store "
                                u"them for modes). \u03c3 only, from the displacements and "
                                u"rotations."),
    "no_route": (u"Deze oplossing heeft geen snedekrachten en geen rotaties voor "
                 u"deze balk.",
                 u"This solution has no section forces and no rotations for this "
                 u"beam."),
    "separate_parts": (u"Doorsnede uit losse delen: geen schuifvelden, alleen \u03c3.",
                       u"Section of separate parts: no shear fields, \u03c3 only."),
    "props_differ": (u"Doorsnede {name}: vorm en eigenschappen in het model verschillen "
                     u"tot {percent} %. \u03c3 rekent met het model, \u03c4 met de vorm.",
                     u"Section {name}: shape and properties in the model differ by up "
                     u"to {percent} %. \u03c3 uses the model, \u03c4 the shape."),
    "eccentric_shear_centre": (
        u"Excentrisch schuifcentrum: Mecway's balk kent geen koppeling tussen buiging "
        u"en wringing, dus in een statisch onbepaalde constructie kunnen T en V zelf "
        u"afwijken.",
        u"Eccentric shear centre: Mecway's beam has no coupling between bending and "
        u"torsion, so in a statically indeterminate structure T and V themselves can "
        u"deviate."),
    "aster_offset_used": (u"Doorsnede {name}: EY, EZ uit #ASTER-SECTIE wijken {mm} af van "
                          u"het schuifcentrum van de vorm. Voor deze Code-Aster-oplossing telt "
                          u"#ASTER-SECTIE.",
                          u"Section {name}: EY, EZ of #ASTER-SECTIE differ {mm} from the shear "
                          u"centre of the shape. For this Code-Aster solution #ASTER-SECTIE "
                          u"counts."),
    "aster_offset_unused": (u"Doorsnede {name}: EY, EZ uit #ASTER-SECTIE wijken {mm} af van "
                            u"het schuifcentrum van de vorm. Deze oplossing komt niet herkenbaar "
                            u"van Code-Aster, dus telt de vorm.",
                            u"Section {name}: EY, EZ of #ASTER-SECTIE differ {mm} from the "
                            u"shear centre of the shape. This solution is not recognised as "
                            u"Code-Aster's, so the shape counts."),
    "warping_zone": (u"Doorsnede {name}: bij dit profiel klopt de spanning pas op ongeveer "
                     u"{mm} (4/k) van een inklemming of krachtinleiding.",
                     u"Section {name}: with this profile the stress holds only from about "
                     u"{mm} (4/k) away from a clamp or load introduction."),
    "mode_scaled": (u"Mode-oplossing: de grootte is willekeurig geschaald, alleen de "
                    u"verdeling zegt iets.",
                    u"Mode solution: the size is scaled arbitrarily, only the "
                    u"distribution means something."),
    "corner_peak": (u"Scherpe inwendige hoek: de piek daar groeit mee met het mesh en is "
                    u"geen uitkomst. Het hoofdgetal is het maximum buiten de hoeken.",
                    u"Sharp re-entrant corner: the peak there grows with the mesh and "
                    u"is not a result. The main number is the maximum outside the "
                    u"corners."),
    "configuration_active": (u"Configuratie {name} in Mecway actief gezet: Mecway's actieve "
                             u"configuratie is niet uit te lezen, dus kiest de tool hem zelf.",
                             u"Configuration {name} made active in Mecway: Mecway's active "
                             u"configuration cannot be read, so the tool chooses it itself."),
    "worker_died": (u"Het rekenproces is gestopt.", u"The calculation process stopped."),
    "import_failed": (u"De Python mist iets: {what}", u"The Python lacks something: {what}"),
    "internal": (u"Interne fout in het rekenproces, zie {log}.",
                 u"Internal error in the calculation process, see {log}."),
    "marker_on": (u"De positie staat als tijdelijk element {name} in het model, alleen om "
                  u"te kijken en alleen zichtbaar in het modelbeeld. Zet de optie uit voor "
                  u"het oplossen: een modale analyse geeft er geen oplossing mee (gemeten).",
                  u"The position is in the model as temporary element {name}, for viewing "
                  u"only and visible in the model view only. Turn the option off before "
                  u"solving: a modal analysis gives no solution with it (measured)."),
    "marker_left": (u"Een tijdelijk element van een vorige keer is verwijderd.",
                    u"A temporary element from an earlier time was removed."),
    "marker_stuck": (u"Een tijdelijk element ({name}) kon niet worden verwijderd. Verwijder "
                     u"het in Mecway.",
                     u"A temporary element ({name}) could not be removed. Delete it in "
                     u"Mecway."),
    "not_open": (u"Nog geen balk geopend.", u"No beam opened yet."),
    "unknown_command": (u"Onbekende opdracht aan het rekenproces.",
                        u"Unknown command to the calculation process."),
    # the chain
    "empty": (u"Geen elementen gekozen.", u"No elements chosen."),
    "unknown_element": (u"Element {eid} staat niet in het opgeslagen bestand.",
                        u"Element {eid} is not in the saved file."),
    "not_a_beam": (u"Element {eid} is geen balk met doorsnede.",
                   u"Element {eid} is no beam with a section."),
    "missing_node": (u"Element {eid}: knoop {node} ontbreekt in het bestand.",
                     u"Element {eid}: node {node} is missing from the file."),
    "zero_length": (u"Element {eid} heeft lengte nul.", u"Element {eid} has zero length."),
    "branch": (u"De keuze vertakt in knoop {node}: kies \u00e9\u00e9n doorlopende lijn.",
               u"The choice branches at node {node}: choose one continuous line."),
    "gap": (u"De elementen vormen geen doorlopende lijn.",
            u"The elements do not form a continuous line."),
    "closed_loop": (u"De keuze is een gesloten lus: kies een lijn met twee einden.",
                    u"The choice is a closed loop: choose a line with two ends."),
    "fold_back": (u"De keten vouwt terug bij element {eid}.",
                  u"The chain folds back at element {eid}."),
    "orientation_configuration": (u"Element {eid}: de oriëntatie is in een configuratie "
                                  u"onderdrukt en welke configuratie is opgelost is niet "
                                  u"bekend, dus de V-as ook niet. Kies Opnieuw lezen.",
                                  u"Element {eid}: the orientation is suppressed in a "
                                  u"configuration and which configuration was solved is not "
                                  u"known, so neither is the V axis. Choose Read again."),
    "orientation_formula": (u"Element {eid}: ori\u00ebntatie met een formule kan de tool "
                            u"niet lezen.",
                            u"Element {eid}: the tool cannot read an orientation with a "
                            u"formula."),
    "outside_chain": (u"Plaats buiten de keten.", u"Position outside the chain."),
    "no_section_forces": (u"Element {eid} mist {variable}.",
                          u"Element {eid} lacks {variable}."),
    "no_young": (u"Element {eid}: het materiaal heeft geen elasticiteitsmodulus.",
                 u"Element {eid}: the material has no Young's modulus."),
    "no_motion": (u"Element {eid} mist verplaatsing of rotatie.",
                  u"Element {eid} lacks displacement or rotation."),
    "kink": (u"Knik van {degrees}\u00b0 bij {mm} (element {eid}): elke doorsnede rekent "
             u"in het eigen element.",
             u"Kink of {degrees}\u00b0 at {mm} (element {eid}): each section is "
             u"calculated in its own element."),
    "section_twist": (u"Doorsnede draait {degrees}\u00b0 bij {mm} (element {eid}).",
                      u"Section turns {degrees}\u00b0 at {mm} (element {eid})."),
    "section_change": (u"Andere doorsnede vanaf {mm}: {material}.",
                       u"Other section from {mm}: {material}."),
    "section_mirrored": (u"Bij {mm} keert de knoopvolgorde om (element {eid}): dezelfde "
                         u"doorsnede, gespiegeld getekend.",
                         u"At {mm} the node order turns round (element {eid}): the same "
                         u"section, drawn mirrored."),
    "frame_tie": (u"Element {eid}: de tekenrichting van de doorsnede is een keuze, want "
                  u"beide kanten zijn even dichtbij.",
                  u"Element {eid}: the drawing side of the section is a choice, both "
                  u"sides being equally near."),
    "curved_element": (u"Element {eid} is gekromd: gerekend als rechte balk.",
                       u"Element {eid} is curved: calculated as a straight beam."),
    "load_on_mid_node": (u"Puntlast op een middenknoop ({selection}): het verloop binnen "
                         u"dat element klopt niet.",
                         u"Point load on a mid node ({selection}): the course within "
                         u"that element is not right."),
    "ccx_line2": (u"CCX met line2-elementen: het moment is gemeten 14 % te laag. Gebruik "
                  u"line3.",
                  u"CCX with line2 elements: the moment was measured 14 % too low. Use "
                  u"line3."),
    "ccx_reversed": (u"CCX met tegengesteld genummerde elementen: knoopwaarden zijn "
                     u"gemiddelden in elementassen, gemeten Vw en Mw 0 op zo'n knoop.",
                     u"CCX with elements numbered in opposite directions: node values "
                     u"are averages in element axes, measured Vw and Mw 0 at such a "
                     u"node."),
    "correction_skipped": (u"Geen correctie voor verdeelde last op {count} element(en) "
                           u"({reason}): het verloop daarbinnen is rechtlijnig.",
                           u"No correction for distributed load on {count} element(s) "
                           u"({reason}): the course within is linear."),
    "load_convention_unknown": (u"Element {eid}: niet te bepalen of de eindwaarden "
                                u"middenwaarden zijn; verdeelde last niet gecorrigeerd.",
                                u"Element {eid}: cannot tell whether end values are "
                                u"midpoint values; distributed load not corrected."),
    "kinematics_only": (u"Alleen N, Mv en Mw, uit de kinematica.",
                        u"Only N, Mv and Mw, from the kinematics."),
    # the model
    "bad_file": (u"Het bestand is niet te lezen: {what}",
                 u"The file cannot be read: {what}"),
    "empty_model": (u"Het bestand bevat geen knopen.", u"The file holds no nodes."),
    "configuration_unknown": (u"{tag} is in een configuratie onderdrukt, en welke configuratie is "
                              u"opgelost is niet bekend: de tool gebruikt het niet ongemerkt.",
                              u"{tag} is suppressed in a configuration, and which configuration "
                              u"was solved is not known: the tool does not use it unnoticed."),
    "configuration_missing": (u"Configuratie {name} staat niet in het opgeslagen bestand. Sla "
                              u"het model op en kies Opnieuw lezen.",
                              u"Configuration {name} is not in the saved file. Save the model "
                              u"and choose Read again."),
    "bad_aster_section": (u"Materiaal {material}: regel #ASTER-SECTIE niet te lezen.",
                          u"Material {material}: line #ASTER-SECTIE cannot be read."),
    "aster_section_stress": (u"Materiaal {material}: #ASTER-SECTIE levert geen "
                             u"spanningen.",
                             u"Material {material}: #ASTER-SECTIE gives no stresses."),
    "aster_section_stale": (u"Materiaal {material}: #ASTER-SECTIE past niet bij het "
                            u"materiaal (A, IY, IZ of JX); EY, EZ tellen niet.",
                            u"Material {material}: #ASTER-SECTIE does not fit the "
                            u"material (A, IY, IZ or JX); EY, EZ do not count."),
    "bad_shape": (u"SECTIE-VORM niet te lezen.", u"SECTIE-VORM cannot be read."),
    "bad_direction": (u"SECTIE-RICHTING niet te lezen: {what}",
                      u"SECTIE-RICHTING cannot be read: {what}"),
    "orientation_conflict": (u"Element {eid} heeft twee ori\u00ebntaties.",
                             u"Element {eid} has two orientations."),
    # the section
    "no_shape": (u"Doorsnede {name}: geen vorm in het opgeslagen model, dus geen beeld. "
                 u"Wijs de doorsnede toe met MecwaySectionProps en sla het model op: de "
                 u"regels staan pas na opslaan in de .liml. Een hernoemd materiaal "
                 u"verliest zijn vorm; wijs die dan opnieuw toe.",
                 u"Section {name}: no shape in the saved model, so no picture. Assign "
                 u"the section with MecwaySectionProps and save the model: the lines are "
                 u"only in the .liml after saving. A renamed material loses its shape; "
                 u"then assign it again."),
    "stale_shape": (u"Doorsnede {name}: de SECTIE-VORM past niet bij het materiaal "
                    u"({what}), dus geen beeld. Wijs de doorsnede opnieuw toe met "
                    u"MecwaySectionProps en sla het model op.",
                    u"Section {name}: the SECTIE-VORM does not fit the material ({what}), "
                    u"so no picture. Assign the section again with MecwaySectionProps "
                    u"and save the model."),
    "section_direction": (u"Materiaal {material}: {count} balk(en) passen niet bij de "
                          u"richting waarvoor de doorsnede is gemaakt ({reason}, "
                          u"{degrees} graden). De balk rekent dan met een gedraaide of "
                          u"gespiegelde doorsnede. Zet de V-as goed, of wijs de "
                          u"doorsnede opnieuw toe met MecwaySectionProps.",
                          u"Material {material}: {count} beam(s) do not fit the direction "
                          u"the section was made for ({reason}, {degrees} degrees). The "
                          u"beam then calculates with a turned or mirrored section. "
                          u"Correct the V axis, or assign the section again with "
                          u"MecwaySectionProps."),
    "shape_inertia": (u"Materiaal {material}: de vorm past niet bij de eigenschappen. "
                      u"Omtrek: Ivv {outline_ivv}, Iww {outline_iww} mm\u2074; materiaal: "
                      u"iyy {iyy}, izz {izz} mm\u2074. De waarden zijn verwisseld of "
                      u"aangepast, of de vorm is te grof. \u03c3 rekent met het materiaal, \u03c4 met de vorm. "
                      u"Wijs de doorsnede opnieuw toe met MecwaySectionProps.",
                      u"Material {material}: the shape does not fit the properties. "
                      u"Outline: Ivv {outline_ivv}, Iww {outline_iww} mm\u2074; material: "
                      u"iyy {iyy}, izz {izz} mm\u2074. The values were swapped or "
                      u"edited, or the shape is too coarse. \u03c3 uses the material, \u03c4 the shape. Assign the "
                      u"section again with MecwaySectionProps."),
    "no_poisson": (u"Doorsnede {name}: het materiaal heeft geen Poisson-getal.",
                   u"Section {name}: the material has no Poisson ratio."),
    "missing_dimension": (u"Doorsnede {name}: maat {what} ontbreekt.",
                          u"Section {name}: dimension {what} is missing."),
    "bad_value": (u"Waarde niet te lezen: {what}", u"Value cannot be read: {what}"),
    "bad_dimensions": (u"Doorsnede {name}: de maten kloppen niet ({what}).",
                       u"Section {name}: the dimensions are not valid ({what})."),
    "unknown_section": (u"Doorsnede {name}: onbekend type {what}.",
                        u"Section {name}: unknown type {what}."),
    "mesh_failed": (u"Doorsnede {name}: geen mesh mogelijk ({what}).",
                    u"Section {name}: no mesh possible ({what})."),
    "warping_failed": (u"Doorsnede {name}: de wringing is niet op te lossen ({what}).",
                       u"Section {name}: the torsion cannot be solved ({what})."),
    "bad_properties": (u"Doorsnede {name}: de eigenschappen kloppen niet ({what}).",
                       u"Section {name}: the properties are not valid ({what})."),
}
_REASONS = (("gravity", u"zwaartekracht", u"gravity"),
            ("load_selection", u"selectie van de last", u"selection of the load"),
            ("load", u"last", u"load"),
            ("flexible_joint", u"flexibele verbinding", u"flexible joint"),
            ("element_offset", u"element-offset", u"element offset"),
            ("analysis", u"andere analyse dan statisch", u"analysis other than static"),
            ("configuration", u"last in een configuratie onderdrukt, opgeloste configuratie "
                              u"onbekend", u"load suppressed in a configuration, solved "
                                           u"configuration unknown"),
            ("turned", u"V-as gedraaid", u"V axis turned"),
            ("mirrored", u"balk tegen de richting in", u"beam against the direction"),
            ("skew", u"niet evenwijdig", u"not parallel"))


class _Blank(dict):
    def __missing__(self, key):
        return u"?"


def _prepare(data):
    """The fields of a warning as text for the window."""
    out = _Blank(data or {})
    if out.get("length") is not None:
        out["mm"] = fmt_length(out["length"])
    if out.get("s") is not None:
        out["mm"] = fmt_length(out["s"])
    if out.get("degrees") is not None:
        # one decimal for every angle: 90.0 and 180.0, not 90.0 and 180
        out["degrees"] = u"{0:.1f}".format(float(out["degrees"]))
    for key in ("outline_ivv", "outline_iww", "iyy", "izz"):
        if out.get(key) is not None:
            out[key] = _num(out[key] * 1.0e12, 4)
    if out.get("elements") is not None:
        out["count"] = len(out["elements"])
    reason = out.get("reason")
    if reason:
        for key, nl, en in _REASONS:
            if reason.startswith(key):
                out["reason"] = tr(nl, en)
                break
    out.setdefault("log", WORKER_LOG)
    out.setdefault("name", MARKER_PREFIX)
    return out


def warning_text(code, data=None):
    """User text of a warning code; an unknown code shows the code and data, so
    a new one is never lost."""
    pair = _WARNINGS.get(code)
    if pair is None:
        return u"{0}: {1}".format(code, data) if data else u"{0}".format(code)
    return string.Formatter().vformat(tr(pair[0], pair[1]), (), _prepare(data))
