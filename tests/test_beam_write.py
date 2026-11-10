# -*- coding: utf-8 -*-
# Copyright (C) 2026 trednuK
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Test of the write route of Scripts/beam_mecway.py with a fake solution that behaves
# as measured on Mecway 35: set_variable raises a null reference on a solution of the
# bridge with shells of "Section in results", any other error stays an error. The real
# errors are in tests/test_mecway_e2e.py. Also the head of a file: what the list of
# beams leaves out and which components only the solution has. Runs in CPython and in
# Mecway's IronPython.
#
#   python tests/test_beam_write.py

from __future__ import print_function

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(globals().get("__file__", sys.argv[0])))
sys.path.insert(0, os.path.join(_HERE, "..", "Scripts"))
sys.dont_write_bytecode = True

import beam_mecway as bmw  # noqa: E402
import beam_utils as bu  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (" ({0})".format(detail) if detail != "" else ""))
    if not ok:
        FAILED.append(name)


class FakeType(object):
    Name = "NullReferenceException"


class FakeClr(object):
    def GetType(self):
        return FakeType()


class FakeSolution(object):
    def __init__(self, nodes=3, refuse=False):
        self.nodes = nodes
        self.refuse = refuse
        self.written = {}

    def all_nodes(self):
        return list(range(1, self.nodes + 1))

    def set_variable(self, name, values, **kw):
        if self.refuse:
            error = SystemError("object reference")
            error.clsException = FakeClr()
            raise error
        self.written[name] = list(values)


class FakeMw(object):
    def __init__(self, nodes=3):
        self.count = nodes

    def all_nodes(self):
        return list(range(1, self.count + 1))

    def components(self):
        return []


ACTIVE = {"kind": "active", "number": None, "value": None, "time_step": None, "mode": None,
          "configuration": None}
VALUES = {"sectionvonmises": {"2": 5.0}, "sectionsigma": {"3": 7.0}}

same = FakeSolution()
out = bmw.write_results(FakeMw(), same, VALUES, ACTIVE)
check("same size: both names", out.get("names") == ["sectionsigma", "sectionvonmises"], out)
check("values on their nodes, 0 elsewhere", same.written["sectionvonmises"] == [0.0, 5.0, 0.0],
      same.written)

more = FakeSolution(nodes=9)
out = bmw.write_results(FakeMw(), more, VALUES, ACTIVE)
check("another size: refused with both sizes", out.get("refused") == "size" and
      out["solution"] == 9 and out["model"] == 3, out)
check("another size: nothing written", more.written == {})

null = FakeSolution(refuse=True)
out = bmw.write_results(FakeMw(), null, VALUES, ACTIVE)
check("null reference: refused, with the text", out.get("refused") == "object reference", out)


class SecondRefuses(FakeSolution):
    def set_variable(self, name, values, **kw):
        self.refuse = bool(self.written)
        FakeSolution.set_variable(self, name, values, **kw)


out = bmw.write_results(FakeMw(), SecondRefuses(), VALUES, ACTIVE)
check("refused halfway: says what was written", out.get("written") == ["sectionsigma"] and
      "names" not in out, out)


class OtherSystem(FakeSolution):
    def set_variable(self, name, values, **kw):
        raise SystemError("another system error")


class Other(FakeSolution):
    def set_variable(self, name, values, **kw):
        raise ValueError("other error")


for kind, error in ((OtherSystem, SystemError), (Other, ValueError)):
    try:
        bmw.write_results(FakeMw(), kind(), VALUES, ACTIVE)
        check("{0} is not hidden".format(error.__name__), False)
    except error:
        check("{0} is not hidden".format(error.__name__), True)


# ---------------------------------------------------------------- the head of a file
def head(*lines):
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "head.liml")
    with open(path, "w") as f:
        for line in lines:
            f.write(line + chr(10))
    try:
        return bu.scan_head(path)
    finally:
        os.remove(path)
        os.rmdir(folder)


left_out = sorted(head(
    '<liml version="35">',
    '  <nodeselection name="n1"><node nid="1" /></nodeselection>',
    '  <elementselection name="mine"><element eid="1" /></elementselection>',
    '  <elementselection name="of_load" direct="true"><element eid="1" /></elementselection>',
    '  <faceselection name="faces"><face eid="1" faceid="1" /></faceselection>',
    '  <faceselection name="of_force" direct="true"><face eid="1" faceid="1" /></faceselection>',
    '  <elementselection name="both" direct="true"><element eid="1" /></elementselection>',
    '  <faceselection name="both"><face eid="1" faceid="1" /></faceselection>',
    '</liml>')["node_selections"])
check("left out: node selections and those only made with a load",
      left_out == ["n1", "of_force", "of_load"], left_out)
known = head(
    '<liml version="35">',
    '  <nodeselection name="n1"><node nid="1" /></nodeselection>',
    '  <elementselection name="mine"><element eid="1" /></elementselection>',
    '  <elementselection name="of_load" direct="true"><element eid="1" /></elementselection>',
    '  <faceselection name="faces"><face eid="1" faceid="1" /></faceselection>',
    '  <faceselection name="of_force" direct="true"><face eid="1" faceid="1" /></faceselection>',
    '  <elementselection name="A &amp; B &lt;&#49;&#x32;&gt;"><element eid="1" /></elementselection>',
    '  <nodeselection name="n &amp; m"><node nid="1" /></nodeselection>',
    '  <solution>',
    '  <elementselection name="after"><element eid="1" /></elementselection>',
    '</liml>')
check("element and face selections of the file, direct or not, names unescaped",
      sorted(known["element_selections"]) == [u"A & B <12>", u"faces", u"mine", u"of_force",
                                              u"of_load"], known["element_selections"])
check("node selection names unescaped too", u"n & m" in known["node_selections"],
      known["node_selections"])
check("no file: the selections of the file are unknown",
      bu.scan_head(os.path.join(tempfile.gettempdir(), "no_such_dir_x", "nope.liml"))
      ["element_selections"] is None)
check("no path: unknown as well", bu.scan_head(None)["element_selections"] is None)
odd = head('<liml version="35">',
           '  <elementselection name="x&#99999999999;&#x110000;y"><element eid="1" /></elementselection>',
           '</liml>')
check("a number that is no character stays as it stands",
      odd["element_selections"] == [u"x&#99999999999;&#x110000;y"], odd["element_selections"])

model = ['<liml version="35">', '  <elset name="E_u" material="u">', '  </elset>']
shells = head(*(model + ['  <solution>', '    <elset name="Doorsnede" colorrgb="DADADA">',
                         '    </elset>', '    <elset name="E_u" material="u__line">', '    </elset>',
                         '    <results precision="double">', '    </results>', '  </solution>',
                         '</liml>']))
check("shells: the component only the solution has", shells["shells"] == ["Doorsnede"], shells["shells"])
plain = head(*(model + ['  <solution>', '    <elset name="E_u" material="u">', '    </elset>',
                        '    <results precision="double">', '    </results>', '  </solution>',
                        '</liml>']))
check("no shells: nothing", plain["shells"] == [], plain["shells"])
later = head(*(model + ['  <solution>', '    <elset name="E_u" material="u">', '    </elset>',
                        '    <results precision="double">', '    <elset name="not_a_part">',
                        '    </results>', '  </solution>', '</liml>']))
check("after <results> nothing counts as a component", later["shells"] == [], later["shells"])
marker = head(*(model + ['  <solution>', '    <elset name="{0}">'.format(bu.MARKER_PREFIX),
                         '    </elset>', '    <results precision="double">', '    </results>',
                         '  </solution>', '</liml>']))
check("the marker in a solution is no shells", marker["shells"] == [], marker["shells"])

print("{0} failed".format(len(FAILED)) if FAILED else "All checks OK")
sys.exit(1 if FAILED else 0)
