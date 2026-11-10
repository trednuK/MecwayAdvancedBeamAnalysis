# -*- coding: ascii -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# In Mecway's IronPython, without Mecway (tools/run_ipy.ps1): the bridge's "Section in
# results": the swept shells in the solution of a solved model, as its button does.
# Arguments: <bridge Scripts> <model.liml> <work folder>. An own model with a SECTIE-VORM.
# Used by the step "prepare" of run_e2e.py.
import sys

sys.path.insert(0, sys.argv[1])
import bridge_utils as utils  # noqa: E402
utils.CONFIG_BRIDGE["work_dir"] = sys.argv[3]
utils.CONFIG_BRIDGE["active_analysis"] = "lin_static_3d"
utils.set_run_log("view", sys.argv[2])
import bridge_section_view as view_module  # noqa: E402
from bridge_from_liml import extract_aster_data_from_liml  # noqa: E402
from bridge_results_liml import add_section_view  # noqa: E402

model = sys.argv[2]
data = extract_aster_data_from_liml(model)
shapes = view_module.current_shapes(
    view_module.parse_shapes(data["aster_custom"].get("section_shapes", [])), data)
view = view_module.build_view(data, shapes, warn_directions=False)
got = add_section_view(model, view, data, None)
print("section view ok: {0}".format(got["ok"]))
if not got["ok"]:
    sys.exit(1)
