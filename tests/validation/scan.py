# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# scan.py
# The error measures along the beam, to show where the end effects are gone.
#
#   python scan.py <tag>[:key] <case> ...

import sys

import compare


def main():
    tag, _, key = sys.argv[1].partition(":")
    s = compare.Solid(tag)
    i = s.info
    cases = compare.cases_for(s, key or i["section"])
    for name in sys.argv[2:]:
        print("#", tag, name, "L/h", round(i["length"] / i["h"], 2))
        print("  x/h  to end  e_sig%  e_tau%   e_vm%  rel30t%  syy%  szz%  tyz%")
        for x in sorted(i["x"]):
            r = compare.compare(s, cases[name], x=x)
            print("{0:6.2f} {1:6.2f} {2:7.3f} {3:7.2f} {4:7.2f} {5:7.2f} {6:6.3f} {7:6.3f} {8:6.3f}".format(
                x / i["h"], (i["length"] - x) / i["h"], 100 * r["e_sig"], 100 * r["e_tau"],
                100 * r["e_vm"], 100 * r["rel30_tau"], 100 * r["sv_syy"], 100 * r["sv_szz"],
                100 * r["sv_tyz"]), flush=True)


if __name__ == "__main__":
    main()
