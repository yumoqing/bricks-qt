"""文档预览后端：PDF（pypdfium2，BSD/Apache）、Excel（openpyxl，MIT）、Word（mammoth，BSD）。"""

from __future__ import annotations

import os

from .base import Provider, register


def _path(node, ctx) -> str:
    return ctx.asset(str(node.opt("url", "src", "path", default=""))) or ""


def pdf_view(node, ctx):
    p = _path(node, ctx)
    if not p or not p.lower().endswith(".pdf"):
        return None
    import pypdfium2 as pdfium
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

    import numpy as np                      # 只借它的 to_numpy，不引入 Pillow

    doc = pdfium.PdfDocument(p)
    scale = float(node.opt("scale", default=2.0))     # 2x ≈ 144dpi
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(4, 4, 4, 4)
    limit = int(node.opt("max_pages", default=20))
    for i in range(min(len(doc), limit)):
        arr = np.asarray(doc[i].render(scale=scale).to_numpy())
        h, w, ch = arr.shape
        fmt = QImage.Format_RGB888 if ch == 3 else QImage.Format_RGBA8888
        qimg = QImage(arr.tobytes(), w, h, ch * w, fmt).copy()   # copy：arr 出了循环就没了
        lab = QLabel()
        lab.setPixmap(QPixmap.fromImage(qimg))
        lay.addWidget(lab)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner)
    return sa


def excel_view(node, ctx):
    p = _path(node, ctx)
    if not p or not p.lower().endswith((".xlsx", ".xlsm")):
        return None
    from openpyxl import load_workbook
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QTabWidget

    wb = load_workbook(p, data_only=True, read_only=True)
    tabs = QTabWidget()
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))[:int(node.opt("max_rows", default=500))]
        if not rows:
            continue
        cols = max(len(r) for r in rows)
        t = QTableWidget(len(rows), cols)
        t.setHorizontalHeaderLabels([str(c) if c else "" for c in rows[0]])
        for r, row in enumerate(rows[1:], start=1):
            for c in range(cols):
                v = row[c] if c < len(row) else ""
                t.setItem(r, c, QTableWidgetItem("" if v is None else str(v)))
        t.verticalHeader().setVisible(False)
        tabs.addTab(t, ws.title)
    wb.close()
    return tabs if tabs.count() else None


def docx_view(node, ctx):
    p = _path(node, ctx)
    if not p or not p.lower().endswith(".docx"):
        return None
    import mammoth
    from PySide6.QtWidgets import QTextBrowser

    with open(p, "rb") as fh:
        result = mammoth.convert_to_html(fh)
    tb = QTextBrowser()
    tb.document().setDefaultStyleSheet(
        "body{font-family:'Microsoft YaHei UI';font-size:14px;line-height:1.7}"
        "table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:4px 8px}"
        "img{max-width:100%}")
    tb.setHtml(result.value)
    return tb


register(Provider("pypdfium2", "docview", 60, pdf_view, "pypdfium2", "pdf",
                  note="PDF 逐页渲染成图，BSD/Apache，可商用"))
register(Provider("openpyxl", "docview", 60, excel_view, "openpyxl", "excel",
                  note="Excel 按 sheet 出表格"))
register(Provider("mammoth", "docview", 55, docx_view, "mammoth", "docx",
                  note="Word → HTML，图片内嵌"))