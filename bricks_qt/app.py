"""应用层：把 .ui 装进 QMainWindow；支持无头渲染截图（用于验收）。"""

from __future__ import annotations

import os
import sys
from typing import Callable, List, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from .model import Node, load_file
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


class BricksWindow(QMainWindow):
    def __init__(self, node: Node, ctx: Ctx, title: str = "bricks-qt"):
        super().__init__()
        self.setWindowTitle(title)
        ctx.window = self
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


def render(path: str, out_png: str, size: QSize = QSize(1200, 800),
           asset_roots: Optional[List[str]] = None) -> dict:
    """离屏渲染：用真实平台插件（才有系统字体）+ WA_DontShowOnScreen，抓窗口成 PNG。

    注意：不要用 QT_QPA_PLATFORM=offscreen —— 那个插件的字库是空的，中文全成豆腐块。
    """
    app = _app()
    node, ctx = load(path, asset_roots)
    win = BricksWindow(node, ctx, os.path.basename(path))
    win.resize(size)
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.show()
    app.processEvents()
    for _ in range(3):
        app.processEvents()
    pix: QPixmap = win.grab()
    os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
    ok = pix.save(out_png)
    dpr = pix.devicePixelRatio() or 1.0
    win.close()
    return {"ok": bool(ok), "png": out_png,
            "size": [int(pix.width() / dpr), int(pix.height() / dpr)],
            "pixels": [pix.width(), pix.height()], "dpr": dpr,
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
    walk(node)
    return {"root": node.widgettype, "widgets": types, "warnings": ctx.warnings,
            "built": root is not None}