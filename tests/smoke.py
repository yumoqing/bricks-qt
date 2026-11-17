"""冒烟验收：对 bricks 仓库 examples/ 下的 .ui 逐个 check + 无头渲染截图。

用法：
  python tests/smoke.py --examples D:/work/repos/bricks/examples --out out --dist D:/work/repos/bricks/dist
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSize          # noqa: E402
from bricks_qt import app as A            # noqa: E402
from bricks_qt.widgets import REGISTRY, WEB_TYPES   # noqa: E402

TARGETS = [
    "hbox.ui", "vbox.ui", "form.ui", "miniform.ui", "accordion.ui", "dynamicaccordion.ui",
    "tab.ui", "toolbar.ui", "tree.ui", "editabletree.ui", "datagrid.ui", "table.ui",
    "tabular.ui", "button.ui", "image.ui", "message.ui", "modal.ui", "checkbox.ui",
    "scroll.ui", "text.ui", "conform.ui", "keytext.ui", "iconbar.ui", "text.ui",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--dist", default="")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    roots = [a.dist] if a.dist else []
    if a.dist:
        roots += [os.path.join(a.dist, "imgs"), os.path.join(a.dist, "3parties")]

    rows, fails = [], 0
    for name in TARGETS:
        path = os.path.join(a.examples, name)
        if not os.path.isfile(path):
            rows.append((name, "-", "-", "-", "MISSING"))
            continue
        try:
            r = A.inspect(path, roots)
            types = sorted(set(r["widgets"]))
            web = [t for t in types if t in WEB_TYPES]
            unknown = [t for t in types if t not in REGISTRY and t not in WEB_TYPES]
            png = os.path.join(a.out, name.replace(".ui", ".png"))
            rr = A.render(path, png, QSize(1100, 760), roots)
            rows.append((name, r["root"], len(r["widgets"]), "%dx%d" % tuple(rr["size"]),
                         "ok" if rr["ok"] else "RENDER_FAIL",
                         ",".join(web) or "-", ",".join(unknown) or "-"))
        except Exception as e:                     # noqa: BLE001
            fails += 1
            rows.append((name, "-", "-", "-", "EXC: %s" % str(e)[:60], "-", "-"))

    print("%-16s %-12s %5s %10s %-10s %-24s %s" % ("file", "root", "wid", "png", "result", "web(占位)", "unknown"))
    for r in rows:
        print("%-16s %-12s %5s %10s %-10s %-24s %s" % r)
    print("\n已适配类型 %d，WebEngine 待接 %d" % (len(REGISTRY), len(WEB_TYPES)))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())