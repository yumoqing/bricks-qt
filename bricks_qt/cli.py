"""bricks-qt 命令行：渲染/检查 bricks 的 .ui（P0 原型）。

  bricks-qt show  <file.ui> [--size 1200x800] [--asset DIR ...]
  bricks-qt shot  <file.ui> --out out.png [--size WxH]
  bricks-qt check <file.ui> [--json]
  bricks-qt types
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__


def _size(s: str):
    from PySide6.QtCore import QSize
    w, _, h = str(s).lower().partition("x")
    return QSize(int(w), int(h or w))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bricks-qt", description="bricks .ui 的 Qt 原生渲染器（P0）")
    ap.add_argument("--version", action="version", version="bricks-qt %s" % __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("show", help="打开 .ui（GUI）")
    p.add_argument("file")
    p.add_argument("--size", default="1200x800")
    p.add_argument("--asset", action="append", default=[])

    p = sub.add_parser("shot", help="无头渲染成 PNG")
    p.add_argument("file")
    p.add_argument("--out", required=True)
    p.add_argument("--size", default="1200x800")
    p.add_argument("--asset", action="append", default=[])

    p = sub.add_parser("check", help="解析并构建（无头），报告未适配控件")
    p.add_argument("file")
    p.add_argument("--asset", action="append", default=[])
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true", help="有未适配控件就以非 0 退出（CI 门禁）")

    sub.add_parser("types", help="列出已适配的 widget 类型")

    p = sub.add_parser("caps", help="列出能力后端（图表/3D/文档/Markdown/代码/媒体）及可用性")
    p.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    from . import app as A

    if a.cmd == "show":
        return A.show(a.file, a.asset, _size(a.size))
    if a.cmd == "shot":
        r = A.render(a.file, a.out, _size(a.size), a.asset)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["ok"] else 1
    if a.cmd == "check":
        r = A.inspect(a.file, a.asset)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print("root=%s widgets=%d" % (r["root"], len(r["widgets"])))
            print("类型：%s" % ", ".join(sorted(set(r["widgets"]))))
            if r["warnings"]:
                print("未适配/告警 %d 条：" % len(r["warnings"]))
                for w in r["warnings"]:
                    print("  -", w)
        return 1 if (a.strict and r["warnings"]) else 0
    if a.cmd == "caps":
        from .providers import report
        rows = report()
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
            return 0
        for r in rows:
            print("%-10s 用 %-18s %s" % (r["capability"], r["chosen"] or "(无可用)",
                                         ("缺依赖 → " + r["hint"]) if not r["chosen"] else ""))
            for pv in r["providers"]:
                mark = "ok  " if pv["ready"] else "缺  "
                print("    %s%-18s p=%-3s %s%s" % (mark, pv["name"], pv["priority"],
                                                  pv["why"], ("  · " + pv["note"]) if pv["note"] else ""))
        return 0

    if a.cmd == "types":
        from .widgets import REGISTRY, WEB_TYPES
        print("已适配 %d 类：" % len(REGISTRY))
        print("  " + " ".join(sorted(REGISTRY)))
        print("待接 WebEngine %d 类：" % len(WEB_TYPES))
        print("  " + " ".join(sorted(WEB_TYPES)))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())