"""主题：默认字体（中文不能变豆腐块）、Fusion 风格、以及 bricks 主题变量的最小映射。"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

CJK_FONTS = [
    "Microsoft YaHei UI", "Microsoft YaHei",      # Windows
    "PingFang SC", "Hiragino Sans GB",            # macOS
    "Noto Sans CJK SC", "Source Han Sans SC",     # Linux（Debian/Ubuntu 常见）
    "WenQuanYi Micro Hei", "SimHei", "SimSun",
]


def pick_cjk_family() -> str:
    fams = set(QFontDatabase.families())
    for f in CJK_FONTS:
        if f in fams:
            return f
    return ""


def apply(app: QApplication | None = None, size: int = 10) -> dict:
    """给 QApplication 装上中文字体 + Fusion 风格，返回实际生效的字体。"""
    app = app or QApplication.instance()
    if app is None:
        return {}
    app.setStyle("Fusion")
    fam = pick_cjk_family()
    if fam:
        app.setFont(QFont(fam, size))
    fb = QFontDatabase.families()
    return {"style": "Fusion", "font": fam or "(默认)", "families": len(fb)}
