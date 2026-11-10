# -*- coding: ascii -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# In Mecway's IronPython, without Mecway (tools/run_ipy.ps1): the bridge's own
# splice_results puts a result fragment in the <solution> of a model. Arguments:
# <bridge Scripts> <model.liml> <fragment> <out.liml>. splice_results is IronPython only.
import sys

sys.path.insert(0, sys.argv[1])
import bridge_results_liml as brl  # noqa: E402

brl.splice_results(sys.argv[2], sys.argv[3], sys.argv[4])
print("spliced")
