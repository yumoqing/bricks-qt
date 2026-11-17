"""应用层：把 .ui 装进 QMainWindow；支持无头渲染截图（用于验收）。"""

from __future__ import annotations

import os
import sys
from typing import Callable, List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from .model import Node, load_file, parse_node
from .widgets import Ctx, build


def _app() -> QApplication:
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication(sys.argv[:1])
        from . import theme
        info = theme.apply(inst)
        if info:
            print("[bricks-qt] 主题 %s，字体 %s（可用字族 %d）" % (
                info["style"], info["font"], info["families"]))
    return inst


def _wait_web_views(win, app, timeout_ms: int = 6000):
    """截图前等所有 QWebEngineView 加载完（Chromium 是独立进程，不等会拍到空白）。"""
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception:                       # noqa: BLE001
        return []
    views = win.findChildren(QWebEngineView)
    if not views:
        return []
    from PySide6.QtCore import QEventLoop, QTimer

    for v in views:
        loop = QEventLoop()
        v.loadFinished.connect(lambda ok=True, _l=loop: _l.quit())
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        app.processEvents()
    return views


class BricksWindow(QMainWindow):
    def __init__(self, node: Node, ctx: Ctx, title: str = "bricks-qt"):
        super().__init__()
        self.setWindowTitle(title)
        ctx.window = self
        self.ctx = ctx                    # 宿主后面还要用（动态加页/取控件）
        root = build(node, ctx)
        if root is None:
            root = QLabel("空 .ui")
        self.setCentralWidget(root)
        for fn in ctx.deferred:
            fn()

    def keyPressEvent(self, e):  # F5 重新加载留给上层
        if e.key() == Qt.Key_F5:
            print("[bricks-qt] F5（重新加载未实现，重启进程即可）")
        super().keyPressEvent(e)


def load(path: str, asset_roots: Optional[List[str]] = None,
         navigator: Optional[Callable[[str], None]] = None) -> tuple:
    node = load_file(path)
    ctx = Ctx(base_dir=os.path.dirname(os.path.abspath(path)),
              asset_roots=asset_roots or [], navigator=navigator)
    return node, ctx


def show(path: str, asset_roots: Optional[List[str]] = None,
         size: Optional[QSize] = None, title: str = "") -> int:
    app = _app()
    node, ctx = load(path, asset_roots)
    win = BricksWindow(node, ctx, title or os.path.basename(path))
    if size:
        win.resize(size)
    else:
        win.resize(1200, 800)
    win.show()
    for w in ctx.warnings:
        print("[bricks-qt][WARN] %s" % w)
    return app.exec()


def _quiet_wait(app, seconds: float):
    """不阻塞事件循环地等一会儿（截图前让 Chromium 出帧）。"""
    import time
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.03)


def _export_canvas(win, app, out_png: str, timeout_ms: int = 5000) -> str:
    """把 WebEngine 里第一块 canvas 导出成 PNG（grab() 拍不到 Chromium 的像素）。"""
    import base64
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception:                       # noqa: BLE001
        return ""
    from PySide6.QtCore import QEventLoop, QTimer

    views = win.findChildren(QWebEngineView)
    if not views:
        return ""
    box = {}
    loop = QEventLoop()
    views[0].page().runJavaScript(
        "(() => { const c = document.querySelector('canvas');"
        " if (!c) return ''; try { return c.toDataURL('image/png'); } catch (e) { return ''; } })()",
        lambda r: (box.update(r=r), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    data = box.get("r") or ""
    if not data.startswith("data:image/png;base64,"):
        return ""
    path = (out_png[:-4] if out_png.lower().endswith(".png") else out_png) + ".web.png"
    try:
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data.split(",", 1)[1]))
    except (OSError, ValueError):
        return ""
    print("[bricks-qt] WebEngine canvas 导出 → %s" % path)
    return path


def _probe_web(win, app, timeout_ms: int = 4000):
    """跑一段 JS 看 WebEngine 里到底有没有渲染出东西（canvas/svg/文本）。"""
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception:                       # noqa: BLE001
        return []
    from PySide6.QtCore import QEventLoop, QTimer

    out = []
    js = """JSON.stringify((() => {
      const cs = [...document.querySelectorAll('canvas')];
      return {canvases: cs.map(c => c.width + 'x' + c.height),
              svg: document.querySelectorAll('svg').length,
              text: (document.body.innerText || '').slice(0, 100)};
    })())"""
    for v in win.findChildren(QWebEngineView):
        box = {}
        loop = QEventLoop()

        def cb(res, _box=box, _loop=loop):
            _box["r"] = res
            _loop.quit()

        v.page().runJavaScript(js, cb)
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        app.processEvents()
        out.append(box.get("r") or "")
    return out


def render(path: str, out_png: str, size: QSize = QSize(1200, 800),
           asset_roots: Optional[List[str]] = None) -> dict:
    """离屏渲染：用真实平台插件（才有系统字体）+ WA_DontShowOnScreen，抓窗口成 PNG。

    注意：不要用 QT_QPA_PLATFORM=offscreen —— 那个插件的字库是空的，中文全成豆腐块。
    """
    # Chromium 的像素拍不到（独立进程），WebEngine 内容一律用 canvas 导出；
    # swiftshader 让无 GPU 环境下 WebGL（3D）也能起来
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--enable-unsafe-swiftshader")
    app = _app()
    node, ctx = load(path, asset_roots)
    win = BricksWindow(node, ctx, os.path.basename(path))
    win.resize(size)
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.show()
    app.processEvents()
    has_web = bool(_wait_web_views(win, app, timeout_ms=8000))
    if has_web:
        # Chromium 是独立进程：loadFinished 之后还要给它时间出帧，否则拍到空白
        _quiet_wait(app, 2.0)
    for _ in range(3):
        app.processEvents()
    pix: QPixmap = win.grab()
    os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
    ok = pix.save(out_png)
    dpr = pix.devicePixelRatio() or 1.0
    web = _probe_web(win, app) if has_web else []
    canvas_png = _export_canvas(win, app, out_png) if has_web else ""
    win.close()
    return {"ok": bool(ok), "png": out_png, "web": web,
            "size": [int(pix.width() / dpr), int(pix.height() / dpr)],
            "pixels": [pix.width(), pix.height()], "dpr": dpr, "canvas_png": canvas_png,
            "warnings": ctx.warnings, "events": ctx.events}


def inspect(path: str, asset_roots: Optional[List[str]] = None) -> dict:
    """只解析 + 构建（不显示），报告未适配控件与告警。"""
    _app()
    node, ctx = load(path, asset_roots)
    root = build(node, ctx)
    types = []

    def walk(n: Node):
        types.append(n.widgettype)
        for c in n.subwidgets:
            walk(c)
        # TabPanel/Accordion 等的页内容在 options.items[].content，也要数进去
        for it in (n.options.get("items") or []):
            if not isinstance(it, dict):
                continue
            c = it.get("content")
            if isinstance(c, dict):
                walk(parse_node(c, n.source))
            for sub in it.get("subwidgets") or []:
                if isinstance(sub, dict):
                    walk(parse_node(sub, n.source))
    walk(node)
    return {"root": node.widgettype, "widgets": types, "warnings": ctx.warnings,
            "built": root is not None}