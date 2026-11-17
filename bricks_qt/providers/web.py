"""WebEngine 后端：用 bricks 自带的 JS 库（echarts / model-viewer / marked / codemirror）
在 Chromium 里渲染，保真度和浏览器完全一致。需要 `pip install 'bricks-qt[webengine]'`。
"""

from __future__ import annotations

import json
import os

from .base import Provider, register
from .common import (CHART_PAGE, base_url, chart_kind, chart_points, chart_title,
                     file_url, find_asset, read_text, wrap_html)

REQUIRES = "PySide6.QtWebEngineWidgets"


def _view(ctx, html: str):
    from PySide6.QtCore import QUrl
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    v = QWebEngineView()
    st = v.settings()
    st.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
    st.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
    v.setHtml(html, QUrl(base_url(ctx)))
    return v


def _chart_html(node, ctx) -> str:
    echarts = find_asset(ctx, r"^echarts(\.min)?\.js$")
    if not echarts:
        return ""
    kind = chart_kind(node)
    pts = chart_points(node)
    title = chart_title(node)
    if kind == "pie":
        pie_opt = node.opt("pie_options", default={}) or {}
        series = {
            "type": "pie",
            "radius": pie_opt.get("radius", ["40%", "70%"]),
            "data": [{"name": n, "value": v} for n, v in pts],
        }
        option = {"backgroundColor": "#fff", "title": {"text": title},
                  "tooltip": {"trigger": "item"},
                  "legend": {"bottom": 0}, "series": [series]}
    else:
        option = {
            "backgroundColor": "#fff",
            "title": {"text": title},
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 50, "right": 24, "top": 56, "bottom": 40},
            "xAxis": {"type": "category", "data": [n for n, _ in pts]},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar" if kind == "bar" else kind,
                        "data": [v for _, v in pts], "smooth": kind == "line"}],
        }
    return CHART_PAGE % {"echarts": file_url(echarts),
                         "option": json.dumps(option, ensure_ascii=False)}


def web_chart(node, ctx):
    html = _chart_html(node, ctx)
    return _view(ctx, html) if html else None


def web_markdown(node, ctx):
    src = node.opt("md_url", "url", "src", "path", default="")
    text = read_text(ctx, src)
    if not text:
        text = str(node.opt("md", "text", "content", default=""))
    if not text:
        return None
    marked = find_asset(ctx, r"^marked(\.min)?\.js$")
    if marked:
        html = wrap_html(ctx, "<div id='md'></div>", extra_head=(
            "<script src='%s'></script><script>window.addEventListener('DOMContentLoaded',"
            "function(){document.getElementById('md').innerHTML=marked.parse(%s);});</script>"
            % (file_url(marked), json.dumps(text, ensure_ascii=False))))
    else:
        import html as _h
        html = wrap_html(ctx, "<pre>%s</pre>" % _h.escape(text))
    return _view(ctx, html)


def web_html(node, ctx):
    body = str(node.opt("html", "content", "text", default=""))
    url = str(node.opt("url", "src", default=""))
    if url and not body:
        return _view(ctx, wrap_html(ctx, "<iframe src='%s' style='border:0;width:100%%;height:96vh'>"
                                   "</iframe>" % url))
    return _view(ctx, wrap_html(ctx, body)) if body else None


def web_scene3d(node, ctx):
    src = str(node.opt("url", "src", "model", "path", default=""))
    full = ctx.asset(src) or src
    if not full:
        return None
    mv = find_asset(ctx, r"^model-viewer(\.min)?\.js$")
    if not mv:
        return None
    html = """<!doctype html><html><head><meta charset="utf-8">
<script type="module" src="%s"></script>
<style>html,body{margin:0;height:100%%;background:#f5f6f8}</style></head>
<body><model-viewer src="%s" camera-controls auto-rotate shadow-intensity="1"
 style="width:100%%;height:100%%"></model-viewer></body></html>""" % (
        file_url(mv), file_url(full) if os.path.isfile(full) else full)
    return _view(ctx, html)


def web_code(node, ctx):
    cm = find_asset(ctx, r"^codemirror\.js$")
    if not cm:
        return None
    src = node.opt("url", "src", "path", default="")
    code = read_text(ctx, src) or str(node.opt("text", "value", "code", default=""))
    mode = str(node.opt("mode", "language", default="python"))
    html = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="%s"><script src="%s"></script>
<script src="%s"></script>
<style>html,body{margin:0;height:100%%}.CodeMirror{height:100%%;font-size:13px}</style></head>
<body><textarea id="c"></textarea>
<script>var cm=CodeMirror.fromTextArea(document.getElementById('c'),
{mode:'%s',lineNumbers:true,readOnly:%s});cm.setValue(%s);</script></body></html>""" % (
        file_url(find_asset(ctx, r"^codemirror\.css$") or ""), file_url(cm),
        file_url(find_asset(ctx, r"^codemirror-python\.js$") or ""),
        mode, "true" if node.opt("readonly") else "false",
        json.dumps(code, ensure_ascii=False))
    return _view(ctx, html)


register(Provider("webengine-chart", "chart", 60, web_chart, REQUIRES, "webengine",
                  note="echarts，与浏览器一致"))
register(Provider("webengine-markdown", "markdown", 50, web_markdown, REQUIRES, "webengine",
                  note="marked.js"))
register(Provider("webengine-html", "html", 60, web_html, REQUIRES, "webengine"))
register(Provider("webengine-3d", "scene3d", 60, web_scene3d, REQUIRES, "webengine",
                  note="model-viewer，支持 glTF/GLB"))
register(Provider("webengine-code", "code", 60, web_code, REQUIRES, "webengine",
                  note="CodeMirror"))