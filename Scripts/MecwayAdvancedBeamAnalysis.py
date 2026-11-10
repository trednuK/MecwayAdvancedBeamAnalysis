# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# MecwayAdvancedBeamAnalysis.py
# Entry point: the stress at every point of a beam section, along a beam.
# Register it in Mecway under Tools > Scripts and run it on a solved model.
#
# mw and solution exist only in this script's namespace; they go to
# beam_gui.run_window, and from there to beam_mecway, the only module that
# calls them.
#
# Modules:
#   beam_utils    settings, language, log, Python search, protocol, warnings
#   beam_gui      the non-modal window on its own thread, the link to the worker
#   beam_mecway   selections, result sets, section forces
#   beam_worker   the calculation (CPython 3 with numpy and scipy, a
#                 prerequisite, started as a process), with beam_render,
#                 beam_model, section_profiles, section_stress
#
# Compatible with IronPython 2.7.9.

import os
import sys
import traceback

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
except NameError:
    pass

from beam_utils import log, tr
import beam_gui


def main():
    token = beam_gui.claim_instance()
    if token is None:
        # Second start while the window is open: the user clicked the script
        # in Mecway's menu, so bring it to the front.
        if not beam_gui.activate_existing():
            mw.message(beam_gui.instance_busy_text())
        return
    try:
        try:
            result = solution
        except NameError:
            result = None
        # like mw, Vector exists only in this script's namespace
        beam_gui.run_window(mw, result, Vector)
    except Exception:
        log("ERROR: {0}".format(traceback.format_exc()))
        mw.message(tr(u"MecwayAdvancedBeamAnalysis gestopt door een fout:\n{0}",
                      u"MecwayAdvancedBeamAnalysis stopped by an error:\n{0}")
                   .format(traceback.format_exc()))
    finally:
        beam_gui.release_instance(token)


main()
