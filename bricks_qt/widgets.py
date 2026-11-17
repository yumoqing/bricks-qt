"""widget 注册表与 Qt 构建器（P0：覆盖 bricks 高频控件，其余给占位并记录缺口）。"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
    QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QTabWidget, QFormLayout, QComboBox, QSpinBox, QCheckBox, QTextEdit, QDialog,
    QToolButton, QFrame, QSizePolicy, QMenuBar, QMenu, QGroupBox, QToolBox, QDateEdit,
    QMessageBox, QProgressBar,
)

from .model import Node, to_px, is_expand, load_file, parse_node

REGISTRY: Dict[str, Callable[[Node, "Ctx"], QWidget]] = {}

PRIMARY_BUTTON_QSS = (
    "QPushButton{background:#1677ff;color:#fff;border:0;border-radius:4px;"
    "padding:6px 18px;font-weight:600} QPushButton:hover{background:#4096ff}"
    " QPushButton:pressed{background:#0958d9}"
)

# 依赖 JS 生态、P0 只给占位（后续接 QWebEngineView）
WEB_TYPES = {
    "ChartBar", "ChartLine", "ChartPie", "ChartRadar", "ChartScatter", "ChartKLine",
    "ChartHeatmap", "ChartMap", "MarkdownViewer", "MdWidget", "CodeEditor", "Scene3D",
    "GlbViewer", "DOCXviewer", "PDFviewer", "EXCELviewer", "Html", "Iframe", "Wterm",
    "WebSocket", "Camera", "ASRClient", "AudioRecorder", "VideoPlayer", "Video",
    "WebASR", "WebTTS", "IconbarPage", "QAFrame", "ResourceBrowser", "ApiDoc",
}


def register(*names: str):
    def deco(fn):
        for n in names:
            REGISTRY[n] = fn
        return fn
    return deco


class Ctx:
    """渲染上下文：日志、资源解析、事件分发、窗口引用。"""

    def __init__(self, base_dir: str = "", asset_roots: Optional[List[str]] = None,
                 navigator: Optional[Callable[[str], None]] = None):
        self.base_dir = base_dir or os.getcwd()
        self.asset_roots = list(asset_roots or [])
        self.navigator = navigator
        self.warnings: List[str] = []
        self.events: List[dict] = []
        self.window = None
        self.deferred: List[Callable[[], None]] = []
        self.depth = 0            # urlwidget 嵌套深度（防环）

    def fork(self, base_dir: str) -> "Ctx":
        """换一个基准目录建子上下文，告警/事件/窗口共享。"""
        c = Ctx(base_dir=base_dir, asset_roots=self.asset_roots, navigator=self.navigator)
        c.warnings = self.warnings
        c.events = self.events
        c.deferred = self.deferred
        c.window = self.window
        c.depth = self.depth + 1
        return c

    # ---- 日志 / 告警 ----
    def warn(self, msg: str):
        self.warnings.append(msg)

    # ---- 资源 ----
    def asset(self, path: str) -> str:
        p = str(path or "").strip()
        if not p:
            return ""
        if os.path.isabs(p) and os.path.isfile(p):
            return p
        cands = [os.path.join(self.base_dir, p.lstrip("/\\"))]
        for r in self.asset_roots:
            cands.append(os.path.join(r, p.lstrip("/\\")))
        for c in cands:
            if os.path.isfile(c):
                return c
        return ""

    # ---- 事件 ----
    def dispatch(self, node: Node, event: str, payload: Any = None):
        hit = False
        for b in node.binds or []:
            if (b.get("event") or "") != event:
                continue
            hit = True
            kind = (b.get("actiontype") or "script").lower()
            rec = {"widget": node.widgettype, "event": event, "actiontype": kind,
                   "wid": b.get("wid"), "target": b.get("target")}
            if kind == "script":
                rec["script"] = b.get("script")
            elif kind in ("urlwidget", "newwindow", "link", "popupwindow"):
                rec["url"] = b.get("url")
                if kind == "urlwidget" and self.navigator and b.get("url"):
                    self.navigator(b["url"])
            elif kind == "submit":
                rec["url"] = b.get("url")
            self.events.append(rec)
            print("[bricks-qt] 事件 %s.%s -> %s %s" % (node.widgettype, event, kind,
                                                       (rec.get("script") or rec.get("url") or "")[:80]))
        if not hit:
            print("[bricks-qt] 事件 %s.%s（无 binds）" % (node.widgettype, event))
        return hit


def _apply_size(w: QWidget, node: Node):
    """bricks 的 width/height：px 固定、100%/百分号扩展、auto 交给 sizeHint。"""
    width, height = node.opt("width"), node.opt("height")
    if is_expand(width):
        w.setSizePolicy(QSizePolicy.Expanding, w.sizePolicy().verticalPolicy())
    else:
        px = to_px(width)
        if px:
            w.setMinimumWidth(px)
            w.setMaximumWidth(px)
    if is_expand(height):
        w.setSizePolicy(w.sizePolicy().horizontalPolicy(), QSizePolicy.Expanding)
    else:
        px = to_px(height)
        if px:
            w.setMinimumHeight(px)
            w.setMaximumHeight(px)


def _label_text(node: Node) -> str:
    return str(node.opt("text", "otext", "label", "title", "value", default=""))


def _box(node: Node, ctx: Ctx, horizontal: bool) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w) if horizontal else QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    for child in node.subwidgets:
        cw = build(child, ctx)
        if cw is not None:
            stretch = 1 if (is_expand(child.opt("height")) if not horizontal
                            else is_expand(child.opt("width"))) else 0
            lay.addWidget(cw, stretch)
    _apply_size(w, node)
    return w


@register("VBox")
def b_vbox(node, ctx):
    return _box(node, ctx, horizontal=False)


@register("HBox", "Toolbar")
def b_hbox(node, ctx):
    if node.widgettype == "Toolbar":
        return _toolbar(node, ctx)
    return _box(node, ctx, horizontal=True)


@register("FVBox", "FVbox")
def b_fvbox(node, ctx):
    return _box(node, ctx, horizontal=False)


@register("FHBox", "FHbox")
def b_fhbox(node, ctx):
    return _box(node, ctx, horizontal=True)


@register("Cols")
def b_cols(node, ctx):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for child in node.subwidgets:
        lay.addWidget(build(child, ctx), 1)
    _apply_size(w, node)
    return w


@register("Filler", "VScrollPanel", "ScrollPanel")
def b_filler(node, ctx):
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, 0, 0)
    for child in node.subwidgets:
        lay.addWidget(build(child, ctx))
    lay.addStretch(1)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner)
    sa.setFrameShape(QFrame.NoFrame)
    _apply_size(sa, node)
    return sa


@register("HFiller", "HScrollPanel")
def b_hfiller(node, ctx):
    inner = QWidget()
    lay = QHBoxLayout(inner)
    lay.setContentsMargins(0, 0, 0, 0)
    for child in node.subwidgets:
        lay.addWidget(build(child, ctx))
    lay.addStretch(1)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner)
    sa.setFrameShape(QFrame.NoFrame)
    _apply_size(sa, node)
    return sa


@register("Splitter")
def b_splitter(node, ctx):
    horizontal = bool(node.opt("horizontal", "orient", default=True))
    sp = QSplitter(Qt.Horizontal if horizontal else Qt.Vertical)
    for child in node.subwidgets:
        sp.addWidget(build(child, ctx))
    _apply_size(sp, node)
    return sp


@register("Text")
def b_text(node, ctx):
    lab = QLabel(_label_text(node))
    lab.setWordWrap(bool(node.opt("wordwrap", "wrap", default=False)))
    color = node.opt("color", "font_color")
    if color:
        lab.setStyleSheet("color:%s" % color)
    _apply_size(lab, node)
    return lab


def _title(size: int, bold=True):
    def builder(node, ctx):
        lab = QLabel(_label_text(node))
        f = QFont()
        f.setPointSize(size)
        f.setBold(bold)
        lab.setFont(f)
        lab.setWordWrap(True)
        _apply_size(lab, node)
        return lab
    return builder


for _i, _size in enumerate((22, 19, 17, 15, 14, 13), start=1):
    REGISTRY["Title%d" % _i] = _title(_size)


@register("Button")
def b_button(node, ctx):
    btn = QPushButton(str(node.opt("label", "text", "title", default="按钮")))
    if node.opt("icon"):
        p = ctx.asset(node.opt("icon"))
        if p:
            btn.setIcon(QPixmap(p))
    if node.opt("enabled") is False:
        btn.setEnabled(False)
    btn.clicked.connect(lambda: ctx.dispatch(node, "click"))
    _apply_size(btn, node)
    return btn


@register("Icon", "BlankIcon", "MultipleStateIcon", "StatedIcon")
def b_icon(node, ctx):
    path = ctx.asset(node.opt("icon", "url", "src", "image", default=""))
    lab = QLabel()
    if path:
        pix = QPixmap(path)
        size = to_px(node.opt("width", "icon_size"), 0) or 0
        if size:
            pix = pix.scaled(QSize(size, size), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        lab.setPixmap(pix)
    else:
        lab.setText(str(node.opt("label", default="⟨icon⟩")))
        lab.setStyleSheet("color:#999")
    _apply_size(lab, node)
    return lab


@register("Image", "Svg", "MultipleStateImage")
def b_image(node, ctx):
    path = ctx.asset(node.opt("url", "src", "image", "path", default=""))
    lab = QLabel()
    lab.setAlignment(Qt.AlignCenter)
    if path:
        pix = QPixmap(path)
        w = to_px(node.opt("width"), 0) or pix.width()
        if w and pix.width() > w:
            pix = pix.scaledToWidth(w, Qt.SmoothTransformation)
        lab.setPixmap(pix)
    else:
        lab.setText("[image] %s" % node.opt("url", "src", "image", "path", default=""))
        lab.setStyleSheet("color:#999;border:1px dashed #ccc;padding:8px")
    _apply_size(lab, node)
    return lab


@register("Input", "KeyinText")
def b_input(node, ctx):
    led = QLineEdit(str(node.opt("value", "text", default="")))
    led.setPlaceholderText(str(node.opt("placeholder", "label", "title", default="")))
    if str(node.opt("type", "uitype", default="")).lower() == "password":
        led.setEchoMode(QLineEdit.Password)
    led.textChanged.connect(lambda t: ctx.dispatch(node, "change", t))
    _apply_size(led, node)
    return led


@register("SearchBar")
def b_searchbar(node, ctx):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    led = QLineEdit()
    led.setPlaceholderText(str(node.opt("placeholder", "label", default="搜索")))
    btn = QPushButton(str(node.opt("button_text", default="搜索")))
    btn.clicked.connect(lambda: ctx.dispatch(node, "search", led.text()))
    lay.addWidget(led, 1)
    lay.addWidget(btn)
    _apply_size(w, node)
    return w


def _password_line(f):
    e = QLineEdit()
    e.setEchoMode(QLineEdit.Password)
    return e


def _spin(f):
    sb = QSpinBox()
    sb.setRange(int(f.get("min", 0)), int(f.get("max", 100)))
    sb.setSingleStep(int(f.get("step", 1)))
    if f.get("value") not in (None, ""):
        try:
            sb.setValue(int(float(f["value"])))
        except (TypeError, ValueError):
            pass
    return sb


def _date_edit(f):
    from PySide6.QtCore import QDate
    de = QDateEdit()
    de.setCalendarPopup(True)                 # 带日历弹窗
    de.setDisplayFormat(str(f.get("format", "yyyy-MM-dd")))
    d = QDate.fromString(str(f.get("value", "")), "yyyy-MM-dd")
    de.setDate(d if d.isValid() else QDate.currentDate())
    return de


_FIELD_WIDGETS = {
    "str": lambda f: QLineEdit(str(f.get("value", ""))),
    "text": lambda f: QLineEdit(str(f.get("value", ""))),
    "email": lambda f: QLineEdit(str(f.get("value", ""))),
    "tel": lambda f: QLineEdit(str(f.get("value", ""))),
    "url": lambda f: QLineEdit(str(f.get("value", ""))),
    "password": _password_line,
    "number": _spin,
    "int": _spin,
    "float": _spin,
    "date": _date_edit,
    "textarea": lambda f: QTextEdit(str(f.get("value", ""))),
    "checkbox": lambda f: QCheckBox(""),      # 标签已在左侧行标题，别重复
    "bool": lambda f: QCheckBox(""),
    "file": lambda f: QLineEdit(str(f.get("value", ""))),
}


@register("Form")
def b_form(node, ctx):
    box = QGroupBox(str(node.opt("title", default="")) or "")
    outer = QVBoxLayout(box)
    if node.opt("description"):
        desc = QLabel(str(node.opt("description")))
        desc.setStyleSheet("color:#666")
        desc.setWordWrap(True)
        outer.addWidget(desc)
    form = QFormLayout()
    fields = node.opt("fields", "items", default=[]) or []
    for f in fields:
        if not isinstance(f, dict):
            continue
        uitype = str(f.get("uitype", f.get("type", "str"))).lower()
        label = str(f.get("label", f.get("name", "")))
        if uitype in ("select", "choice", "combo", "dropdown"):
            cb = QComboBox()
            for opt in (f.get("options") or f.get("values") or []):
                cb.addItem(str(opt.get("label", opt)) if isinstance(opt, dict) else str(opt))
            widget = cb
        else:
            widget = _FIELD_WIDGETS.get(uitype, _FIELD_WIDGETS["str"])(f)
        if f.get("required") and isinstance(widget, QLineEdit):
            widget.setPlaceholderText("* 必填")
        form.addRow(label + (" *" if f.get("required") else ""), widget)
    outer.addLayout(form)
    btn = QPushButton(str(node.opt("submit_text", default="提交")))
    btn.setStyleSheet(PRIMARY_BUTTON_QSS)
    btn.clicked.connect(lambda: ctx.dispatch(node, "submit"))
    outer.addWidget(btn, 0, Qt.AlignRight)
    _apply_size(box, node)
    return box


@register("DataGrid", "DataViewer", "DataRow", "Tabular")
def b_datagrid(node, ctx):
    cols: List[str] = []
    for f in (node.opt("fields", "columns", default=[]) or []):
        if isinstance(f, dict):
            cols.append(str(f.get("label", f.get("name", ""))))
        else:
            cols.append(str(f))
    rows = node.opt("data", "rows", "values", default=[]) or []
    tbl = QTableWidget(len(rows), max(len(cols), 1))
    tbl.setHorizontalHeaderLabels(cols or ["（无列定义）"])
    for r, row in enumerate(rows):
        vals = row if isinstance(row, (list, tuple)) else [row]
        for c, v in enumerate(vals[:tbl.columnCount()]):
            tbl.setItem(r, c, QTableWidgetItem(str(v)))
    tbl.verticalHeader().setVisible(False)
    tbl.setAlternatingRowColors(True)
    from PySide6.QtWidgets import QHeaderView
    tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    tbl.horizontalHeader().setStretchLastSection(True)
    tbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    tbl.setTextElideMode(Qt.ElideRight)
    tbl.itemSelectionChanged.connect(lambda: ctx.dispatch(node, "select"))
    _apply_size(tbl, node)
    return tbl


@register("Tree", "EditableTree")
def b_tree(node, ctx):
    tree = QTreeWidget()
    heads = node.opt("columns", "fields", default=[]) or []
    if heads:
        tree.setHeaderLabels([str(h.get("label", h.get("name", ""))) if isinstance(h, dict) else str(h)
                              for h in heads])
    else:
        tree.setHeaderLabels([str(node.opt("label", "title", default="名称"))])

    text_key = str(node.opt("textField", "labelField", default="text"))
    child_key = str(node.opt("childrenField", default="children"))

    def add(parent, items):
        for it in items or []:
            if isinstance(it, dict):
                label = (it.get(text_key) or it.get("label") or it.get("text")
                         or it.get("name") or "")
                nodeitem = QTreeWidgetItem([str(label)])
                (parent.addChild(nodeitem) if parent else tree.addTopLevelItem(nodeitem))
                add(nodeitem, it.get(child_key) or it.get("nodes") or it.get("children")
                    or it.get("subwidgets"))
            else:
                (parent.addChild(QTreeWidgetItem([str(it)])) if parent
                 else tree.addTopLevelItem(QTreeWidgetItem([str(it)])))

    data = node.opt("data", "nodes", "items", "tree", default=[]) or []
    add(None, data)
    tree.expandAll()
    tree.itemClicked.connect(lambda i, c: ctx.dispatch(node, "node_selected", i.text(0)))
    _apply_size(tree, node)
    return tree


@register("TabPanel", "TabBar")
def b_tab(node, ctx):
    tabs = QTabWidget()
    items = node.opt("items", "tabs", default=[]) or []
    if items:
        for it in items:
            if not isinstance(it, dict):
                continue
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(6, 6, 6, 6)
            content = it.get("content") or it.get("subwidget")
            if isinstance(content, dict):
                from .model import parse_node
                lay.addWidget(build(parse_node(content, node.source), ctx))
            for sub in it.get("subwidgets") or []:
                lay.addWidget(build(sub, ctx) if isinstance(sub, Node) else build(parse_node(sub, node.source), ctx))
            lay.addStretch(1)
            tabs.addTab(page, str(it.get("label", it.get("title", "页"))))
    else:
        for i, sub in enumerate(node.subwidgets):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(6, 6, 6, 6)
            lay.addWidget(build(sub, ctx))
            lay.addStretch(1)
            tabs.addTab(page, "页 %d" % (i + 1))
    tabs.currentChanged.connect(lambda i: ctx.dispatch(node, "change", i))
    _apply_size(tabs, node)
    return tabs


def _toolbar(node, ctx):
    bar = QWidget()
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(4, 2, 4, 2)
    lay.setSpacing(4)
    items = node.opt("items", "buttons", default=[]) or []
    if items:
        for it in items:
            if not isinstance(it, dict):
                continue
            b = QToolButton()
            b.setText(str(it.get("label", it.get("text", ""))))
            p = ctx.asset(it.get("icon", ""))
            if p:
                b.setIcon(QPixmap(p))
            b.clicked.connect(lambda _=False, item=it: ctx.events.append(
                {"widget": "Toolbar", "event": "click", "actiontype": "item", "label": item.get("label")}))
            lay.addWidget(b)
    for sub in node.subwidgets:
        lay.addWidget(build(sub, ctx))
    lay.addStretch(1)
    par = QFrame()
    par.setLayout(lay)
    par.setFrameShape(QFrame.StyledPanel)
    _apply_size(par, node)
    return par


@register("Accordion", "DynamicAccordion")
def b_accordion(node, ctx):
    from .model import parse_node
    box = QToolBox()
    for it in (node.opt("items", default=[]) or []):
        if not isinstance(it, dict):
            continue
        page = QWidget()
        lay = QVBoxLayout(page)
        content = it.get("content")
        if isinstance(content, dict):
            lay.addWidget(build(parse_node(content, node.source), ctx))
        lay.addStretch(1)
        box.addItem(page, str(it.get("label", it.get("name", "项"))))
    _apply_size(box, node)
    return box


@register("Paging")
def b_paging(node, ctx):
    total = int(node.opt("total", "count", default=0) or 0)
    size = int(node.opt("page_size", default=20) or 20)
    pages = max(1, (total + size - 1) // size)
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    prev = QPushButton("上一页")
    nxt = QPushButton("下一页")
    lab = QLabel("第 1 / %d 页（共 %d 条）" % (pages, total))
    prev.setEnabled(pages > 1)
    nxt.setEnabled(pages > 1)
    prev.clicked.connect(lambda: ctx.dispatch(node, "prev"))
    nxt.clicked.connect(lambda: ctx.dispatch(node, "next"))
    lay.addWidget(prev); lay.addWidget(lab); lay.addWidget(nxt); lay.addStretch(1)
    _apply_size(w, node)
    return w


@register("Message", "Tooltip")
def b_message(node, ctx):
    lab = QLabel(_label_text(node))
    kind = str(node.opt("type", "kind", default="info")).lower()
    color = {"info": "#e8f2ff", "warning": "#fff7e6", "error": "#ffecec", "success": "#eafbea"}.get(kind, "#f5f5f5")
    border = {"info": "#b6d4fe", "warning": "#ffd591", "error": "#ffa39e", "success": "#b7eb8f"}.get(kind, "#ddd")
    lab.setStyleSheet("background:%s;border:1px solid %s;padding:8px;border-radius:4px" % (color, border))
    lab.setWordWrap(True)
    _apply_size(lab, node)
    return lab


@register("Modal", "Popup", "PopupWindow", "NewWindow")
def b_modal(node, ctx):
    btn = QPushButton("打开 %s" % (node.opt("label", "title", default=node.widgettype)))
    dlg = QDialog(ctx.window)
    dlg.setWindowTitle(str(node.opt("title", "label", default=node.widgettype)))
    lay = QVBoxLayout(dlg)
    for sub in node.subwidgets:
        lay.addWidget(build(sub, ctx))
    close = QPushButton("关闭")
    close.clicked.connect(dlg.accept)
    lay.addWidget(close, 0, Qt.AlignRight)
    w = to_px(node.opt("width")); h = to_px(node.opt("height"))
    if w and h:
        dlg.resize(w, h)
    btn.clicked.connect(dlg.exec)
    if node.opt("auto_open"):
        ctx.deferred.append(dlg.open)
    return btn


@register("Menu")
def b_menu(node, ctx):
    """有 QMainWindow 就进菜单栏，否则退化成一行按钮。"""
    items = node.opt("items", default=[]) or []

    def fill(menu, entries):
        for it in entries or []:
            if not isinstance(it, dict):
                menu.addAction(str(it))
                continue
            sub = it.get("items") or it.get("submenu")
            if sub:
                fill(menu.addMenu(str(it.get("label", it.get("text", "")))), sub)
            else:
                act = menu.addAction(str(it.get("label", it.get("text", ""))))
                act.triggered.connect(lambda _=False, x=it: ctx.events.append(
                    {"widget": "Menu", "event": "click", "actiontype": "item", "label": x.get("label")}))

    mb = ctx.window.menuBar() if (ctx.window is not None and hasattr(ctx.window, "menuBar")) else None
    if mb is not None:
        for it in items:
            if isinstance(it, dict):
                fill(mb.addMenu(str(it.get("label", it.get("text", "")))), it.get("items") or it.get("submenu"))
        spacer = QWidget()
        spacer.setFixedHeight(0)
        return spacer
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for it in items:
        b = QPushButton(str(it.get("label", it.get("text", ""))) if isinstance(it, dict) else str(it))
        lay.addWidget(b)
    lay.addStretch(1)
    _apply_size(w, node)
    return w


# ── urlwidget：把一个 .ui/.json 当子控件嵌进来（bricks 里最常用的组装方式）──
_URL_EXPR = re.compile(r"entire_url\(\s*['\"]([^'\"]+)['\"]")


@register("urlwidget", "UrlWidget", "URLWidget")
def b_urlwidget(node, ctx):
    raw = str(node.opt("url", "src", default="")).strip()
    m = _URL_EXPR.search(raw)
    path = (m.group(1) if m else raw).strip()
    low = path.lower()

    def placeholder(msg):
        w = QLabel("<%s>（P0 未接入，P2 用 WebEngine 承载）" % msg)
        w.setAlignment(Qt.AlignCenter)
        w.setWordWrap(True)
        w.setStyleSheet("color:#888;border:1px dashed #bbb;background:#fafafa;padding:10px")
        _apply_size(w, node)
        return w

    if ctx.depth >= 6:
        return placeholder("urlwidget 嵌套过深：%s" % path)
    if low.endswith((".tmpl", ".html")):
        ctx.warn("urlwidget 指向 HTML 模板（需 WebEngine）：%s" % path)
        return placeholder("HTML 模板 %s" % path)
    if low.endswith(".bcrud"):
        ctx.warn("urlwidget 指向 CRUD 端点（需后端+WebEngine）：%s" % path)
        return placeholder("CRUD %s" % path)

    rel = path.lstrip("/")
    for pre in ("bricks/", "wwwroot/"):
        if rel.startswith(pre):
            rel = rel[len(pre):]
    full = ctx.asset(rel)
    if not full:
        ctx.warn("urlwidget 找不到文件：%s" % path)
        return placeholder("找不到 %s" % path)
    try:
        sub = load_file(full)
    except Exception as e:                     # noqa: BLE001
        ctx.warn("urlwidget 解析失败 %s：%s" % (path, str(e)[:60]))
        return placeholder("解析失败 %s" % path)
    if not sub.widgettype or sub.widgettype in ("Widget", "None"):
        ctx.warn("urlwidget 目标不是 widget：%s" % path)
        return placeholder("%s（非 widget 数据）" % path)
    return build(sub, ctx.fork(os.path.dirname(full)))


@register("UiCheckBox", "CheckBox", "Switch", "Toggle")
def b_checkbox(node, ctx):
    cb = QCheckBox(str(node.opt("label", "text", "title", default="")))
    val = node.opt("value", "checked", "selected", default=False)
    cb.setChecked(bool(val) if not isinstance(val, str) else val.lower() in ("1", "true", "yes", "on"))
    cb.toggled.connect(lambda v: ctx.dispatch(node, "change", v))
    _apply_size(cb, node)
    return cb


@register("Conform", "Confirm", "MiniForm")
def b_conform(node, ctx):
    box = QGroupBox(str(node.opt("title", "label", default="")) or "")
    lay = QVBoxLayout(box)
    for sub in node.subwidgets:
        lay.addWidget(build(sub, ctx))
    fields = node.opt("fields", default=[]) or []
    if fields:
        form = QFormLayout()
        for f in fields:
            if not isinstance(f, dict):
                continue
            uitype = str(f.get("uitype", f.get("type", "str"))).lower()
            widget = _FIELD_WIDGETS.get(uitype, _FIELD_WIDGETS["str"])(f)
            form.addRow(str(f.get("label", f.get("name", ""))), widget)
        lay.addLayout(form)
    btn = QPushButton(str(node.opt("submit_text", default="确定")))
    btn.clicked.connect(lambda: ctx.dispatch(node, "submit"))
    lay.addWidget(btn, 0, Qt.AlignRight)
    _apply_size(box, node)
    return box


@register("IconBar", "IconTextBar", "Floaticonbar", "Floaticontextbar", "IconBarText")
def b_iconbar(node, ctx):
    bar = QWidget()
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(2, 2, 2, 2)
    lay.setSpacing(2)
    show_text = "text" in node.widgettype.lower() or node.opt("show_text")
    for it in (node.opt("items", "buttons", default=[]) or []):
        if not isinstance(it, dict):
            continue
        b = QToolButton()
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon if show_text else Qt.ToolButtonIconOnly)
        if show_text:
            b.setText(str(it.get("label", it.get("text", ""))))
        b.setToolTip(str(it.get("tooltip", it.get("label", ""))))
        p = ctx.asset(it.get("icon", ""))
        if p:
            b.setIcon(QPixmap(p))
        b.clicked.connect(lambda _=False, item=it: ctx.events.append(
            {"widget": node.widgettype, "event": "click", "actiontype": "item",
             "label": item.get("label")}))
        lay.addWidget(b)
    for sub in node.subwidgets:
        lay.addWidget(build(sub, ctx))
    lay.addStretch(1)
    _apply_size(bar, node)
    return bar


@register("Running", "Progress", "Loading")
def b_running(node, ctx):
    pb = QProgressBar()
    pb.setRange(0, 0) if not node.opt("value") else pb.setValue(int(node.opt("value")))
    pb.setTextVisible(bool(node.opt("show_text", default=True)))
    if node.opt("label", "text"):
        pb.setFormat(str(node.opt("label", "text")))
    _apply_size(pb, node)
    return pb


def _placeholder(node: Node, ctx: Ctx) -> QWidget:
    label = node.widgettype
    w = QLabel("⟨%s⟩%s" % (label, " 需要 WebEngine 渲染" if label in WEB_TYPES else " 尚未适配"))
    w.setAlignment(Qt.AlignCenter)
    w.setStyleSheet("color:#888;border:1px dashed #bbb;background:#fafafa;padding:10px")
    _apply_size(w, node)
    ctx.warn("未适配的 widget：%s（%s）" % (label, "JS 生态，需 WebEngine" if label in WEB_TYPES else "待实现"))
    return w


def build(node: Node, ctx: Ctx) -> Optional[QWidget]:
    fn = REGISTRY.get(node.widgettype)
    if fn is None:
        return _placeholder(node, ctx)
    return fn(node, ctx)