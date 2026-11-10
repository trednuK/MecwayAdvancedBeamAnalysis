# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Safe runner for the own models in tests/mecway/models. Hidden run on a private
# desktop, so that not even an error dialog of Mecway or a solver can reach the
# user's screen; gate first, one model at a time, stops at the first problem; kills
# only the PID it started, with its child processes, when a window stays or on the
# timeout.
# It refuses to start while a Mecway is open. The results in tests/mecway/results
# stay on this PC, not in git; with them the tests do not need this runner.
#
#   python tests/mecway/run_mecway.py <model> [<model>! ...]
#
# A name ending in ! also saves the solved model. The Mecway path comes from
# the environment variable MECWAY_EXE when set.

from __future__ import print_function

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
RESULTS = os.path.join(HERE, "results")
PROBE = os.path.join(HERE, "probe.py")
MECWAY = os.environ.get("MECWAY_EXE", r"C:\Program Files\Mecway\mecway.exe")
TIMEOUT = 180.0
WINDOW_LIMIT = 3.0          # s a visible window may stay on the private desktop
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
_user32.CreateDesktopW.restype = ctypes.c_void_p
_user32.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD,
                                   ctypes.c_void_p]
_user32.CloseDesktop.argtypes = [ctypes.c_void_p]
_user32.EnumDesktopWindows.argtypes = [ctypes.c_void_p, _ENUM, wt.LPARAM]
_user32.EnumChildWindows.argtypes = [wt.HWND, _ENUM, wt.LPARAM]
_kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wt.DWORD]
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                     wt.BOOL, wt.DWORD, ctypes.c_void_p, wt.LPCWSTR,
                                     ctypes.c_void_p, ctypes.c_void_p]


def mecway_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq mecway.exe"],
                         capture_output=True).stdout
    return b"mecway.exe" in out.lower()


class _Startup(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("reserved", wt.LPWSTR), ("desktop", wt.LPWSTR),
                ("title", wt.LPWSTR), ("x", wt.DWORD), ("y", wt.DWORD), ("xs", wt.DWORD),
                ("ys", wt.DWORD), ("xc", wt.DWORD), ("yc", wt.DWORD), ("fill", wt.DWORD),
                ("flags", wt.DWORD), ("show", wt.WORD), ("r2", wt.WORD),
                ("r3", ctypes.c_void_p), ("stdin", wt.HANDLE), ("stdout", wt.HANDLE),
                ("stderr", wt.HANDLE)]


class _Process(ctypes.Structure):
    _fields_ = [("process", wt.HANDLE), ("thread", wt.HANDLE), ("pid", wt.DWORD),
                ("tid", wt.DWORD)]


def visible_windows(desktop):
    """Titles of the visible top-level windows on a desktop of this runner; read only."""
    found = []

    def read(hwnd):
        text = ctypes.create_unicode_buffer(200)
        _user32.GetWindowTextW(hwnd, text, 200)
        return text.value

    def visit(hwnd, _):
        if _user32.IsWindowVisible(hwnd):
            parts = [read(hwnd)]

            def child(sub, _):
                parts.append(read(sub))
                return True
            _user32.EnumChildWindows(hwnd, _ENUM(child), 0)
            found.append(" | ".join(p for p in parts if p))
        return True
    _user32.EnumDesktopWindows(desktop, _ENUM(visit), 0)
    return found


def gate(model):
    """Reason not to start, or None."""
    if not os.path.isfile(MECWAY):
        return "no Mecway at the configured path"
    if not os.path.isfile(model) or not os.path.isfile(PROBE):
        return "model or probe missing"
    try:
        model.encode("ascii")
    except UnicodeError:
        return "path is not ASCII"
    try:
        with open(PROBE, encoding="ascii") as f:
            compile(f.read(), PROBE, "exec")
        root = ET.parse(model).getroot()
    except Exception as exc:        # any defect stops the run before Mecway starts
        return "gate: {0}".format(exc)
    fields = [f.get("name") for f in root.findall("fieldvariable")]
    for need in ("tensile force", "shear force", "torsion moment", "bending moment 2"):
        if need not in fields:
            return "field variable missing: " + need
    return None


def run_hidden(model, script, env, cwd, timeout=TIMEOUT, window_limit=WINDOW_LIMIT):
    """Mecway on model with script, on a private desktop, until it ends: "ok",
    "timeout" or "window ...". Kills only the PID it started, with its child
    processes. env: the environment of the run."""
    name = "MecwayAdvancedBeamAnalysis_{0}".format(os.getpid())
    desktop = _user32.CreateDesktopW(name, None, None, 0, 0x10000000, None)
    if not desktop:
        return "no private desktop"
    saved = dict(os.environ)
    os.environ.update(env)
    start_info = _Startup()
    start_info.cb = ctypes.sizeof(_Startup)
    start_info.desktop = name
    start_info.flags = 1            # STARTF_USESHOWWINDOW
    start_info.show = 0
    proc = _Process()
    line = ctypes.create_unicode_buffer(subprocess.list2cmdline(
        [MECWAY, model, "script", script]))
    made = _kernel32.CreateProcessW(MECWAY, line, None, None, False, 0, None, cwd,
                                    ctypes.byref(start_info), ctypes.byref(proc))
    os.environ.clear()
    os.environ.update(saved)
    if not made:
        _user32.CloseDesktop(desktop)
        return "no process"
    start = time.time()
    seen = None
    status = "ok"
    while _kernel32.WaitForSingleObject(proc.process, 0) != 0:
        wins = visible_windows(desktop)
        if not wins:
            seen = None
        elif seen is None:
            seen = time.time()
        if seen is not None and time.time() - seen > window_limit:
            status = "window {0!r}".format(wins)
        if time.time() - start > timeout:
            status = "timeout"
        if status != "ok":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
            break
        time.sleep(0.05)
    _kernel32.WaitForSingleObject(proc.process, 10000)
    _kernel32.CloseHandle(proc.process)
    _kernel32.CloseHandle(proc.thread)
    _user32.CloseDesktop(desktop)
    return status


def run(name, save=None):
    model = os.path.join(MODELS, name + ".liml")
    reason = gate(model)
    if reason:
        return reason
    if mecway_running():
        return "a Mecway is running"
    if not os.path.isdir(RESULTS):
        os.makedirs(RESULTS)
    out = os.path.join(RESULTS, name + ".json")
    if os.path.exists(out):
        os.remove(out)
    env = dict(os.environ, PROBE_OUT=out, PROBE_SAVE=save or "",
               PROBE_INFO=os.path.join(MODELS, name + ".info.json"))
    start = time.time()
    status = run_hidden(model, PROBE, env, MODELS)
    print(name, status, "%.1f s" % (time.time() - start), "result:", os.path.exists(out))
    if status == "ok" and os.path.exists(out):
        with open(out) as f:
            errors = json.load(f).get("errors")
        if errors:
            return "probe: {0}".format(errors[0])
    return None if status == "ok" else status


def main(names):
    for name in names:
        save = None
        if name.endswith("!"):
            name = name[:-1]
            save = os.path.join(MODELS, name + "_saved.liml")
        reason = run(name, save)
        if reason:
            print("STOP:", reason)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
