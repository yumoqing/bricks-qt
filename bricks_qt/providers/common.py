"""provider 共用的数据/资源解析小工具。"""

from __future__ import annotations

import os
import re
from typing import Any, List, Optional, Tuple

from ..model import Node

CHART_KIND = {
    "ChartPie": "pie", "ChartBar": "bar", "ChartLine": "line", "ChartScatter": "scatter",
    "ChartRadar": "radar", "ChartKLine": "candlestick", "ChartHeatmap": "heatmap",
    "ChartMap": "map",
}


def chart_kind(node: Node) -> str:
    return CHART_KIND.get(node.widgettype, "bar")


def chart_points(node: Node) -> List[Tuple[str, float]]:
    """把 bricks 各种写法归一成 [(name, value)]：user_data / data / series[0].data。"""
    raw = node.opt("user_data", "data", "values", default=None)
    if raw is None:
        series = node.opt("series", default=None) or []
        if isinstance(series, list) and series and isinstance(series[0], dict):
            raw = series[0].get("data")
    pts: List[Tuple[str, float]] = []
    for it in raw or []:
        if isinstance(it, dict):
            name = it.get("name", it.get("label", it.get("x", "")))
            val = it.get("value", it.get("y", it.get("count", 0)))
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            name, val = it[0], it[1]
        else:
            name, val = str(it), 0
        try:
            pts.append((str(name), float(val)))
        except (TypeError, ValueError):
            pts.append((str(name), 0.0))
    return pts


def chart_title(node: Node) -> str:
    return str(node.opt("title", "label", default=""))


def read_text(ctx, path: str) -> str:
    """按 ctx.base_dir / asset_roots 解析相对路径并读文本（md/html 内容）。"""
    p = str(path or "").strip()
    if not p:
        return ""
    full = ctx.asset(p)
    if not full:
        return ""
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def find_asset(ctx, pattern: str) -> str:
    """在 asset_roots 里找匹配 pattern 的第一个文件（例如 echarts.*\\.js）。"""
    rx = re.compile(pattern, re.I)
    roots = list(getattr(ctx, "asset_roots", []) or [])
    if getattr(ctx, "base_dir", ""):
        roots.append(ctx.base_dir)
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if rx.search(f):
                    return os.path.join(dirpath, f)
            if dirpath.count(os.sep) - str(root).count(os.sep) > 2:
                continue
    return ""


def file_url(path: str) -> str:
    return "file:///" + str(path).replace("\\", "/").lstrip("/")


def base_url(ctx) -> str:
    d = getattr(ctx, "base_dir", "") or os.getcwd()
    return "file:///" + str(d).replace("\\", "/").lstrip("/") + "/"


CHART_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;padding:0;height:100%%;font-family:"Microsoft YaHei UI",sans-serif}</style>
<script src="%(echarts)s"></script></head>
<body><div id="c" style="width:100%%;height:100%%"></div>
<script>
var el = document.getElementById('c');
var ch = echarts.init(el);
ch.setOption(%(option)s);
window.addEventListener('resize', function(){ ch.resize(); });
</script></body></html>"""


def wrap_html(ctx, body: str, extra_css: str = "", extra_head: str = "") -> str:
    css = (
        "html,body{margin:0;padding:14px;font-family:'Microsoft YaHei UI',sans-serif;"
        "font-size:14px;line-height:1.7;color:#222}"
        "h1,h2,h3{color:#111} code{background:#f4f4f4;padding:1px 4px;border-radius:3px}"
        "pre{background:#f6f8fa;padding:10px;border-radius:5px;overflow:auto}"
        "table{border-collapse:collapse} td,th{border:1px solid #ddd;padding:4px 8px}"
        "img{max-width:100%}"
    )
    return ("<!doctype html><html><head><meta charset='utf-8'><style>%s%s</style>%s</head>"
            "<body>%s</body></html>") % (css, extra_css, extra_head, body)