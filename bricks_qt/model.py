"""bricks .ui 模型层：容错解析（bricks 的 .ui 不是严格 JSON）+ 尺寸换算。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

try:
    import json5
except ImportError:  # 兜底：没有 json5 时用正则清洗，能覆盖 90% 的 .ui
    json5 = None


@dataclass
class Node:
    widgettype: str
    options: Dict[str, Any] = field(default_factory=dict)
    subwidgets: List["Node"] = field(default_factory=list)
    binds: List[Dict[str, Any]] = field(default_factory=list)
    id: str = ""
    source: str = ""          # 来自哪个 .ui 文件（urlwidget 嵌套时用）

    def opt(self, *names, default=None):
        for n in names:
            if n in self.options and self.options[n] not in (None, ""):
                return self.options[n]
        return default


def _cleanup(text: str) -> str:
    """把 JS 风格的 .ui 文本洗成严格 JSON：单引号、注释、尾逗号。"""
    t = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    t = re.sub(r"(?m)//.*$", "", t)
    # 单引号字符串 -> 双引号（只处理成对的，内容里的双引号转义）
    def _sq(m):
        inner = m.group(1).replace('\\"', '"').replace('"', '\\"')
        return '"%s"' % inner
    t = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", _sq, t)
    t = re.sub(r",(\s*[}\]])", r"\1", t)      # 尾逗号
    return t


def loads(text: str) -> Any:
    text = text.lstrip("\ufeff \t\r\n")
    if json5 is not None:
        try:
            return json5.loads(text)
        except Exception:
            pass
    try:
        return json.loads(text)
    except Exception:
        return json.loads(_cleanup(text))


def parse_node(d: Any, source: str = "") -> Node:
    if not isinstance(d, dict):
        raise ValueError("widget 节点必须是对象，得到 %r" % type(d).__name__)
    children = d.get("subwidgets") or d.get("widgets") or d.get("children") or []
    return Node(
        widgettype=str(d.get("widgettype") or d.get("uitype") or "Widget"),
        options=d.get("options") or {},
        subwidgets=[parse_node(c, source) for c in children if isinstance(c, dict)],
        binds=d.get("binds") or [],
        id=str(d.get("id") or ""),
        source=source,
    )


def load_file(path: str) -> Node:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse_node(loads(fh.read()), source=os.path.abspath(path))


# ── 尺寸换算（bricks 里是 "700px" / "auto" / "100%" / 28）──
def to_px(value, base: int = 0):
    """返回像素整数；auto/百分比/空 -> None（交给布局自适应）。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    if s in ("auto", "none"):
        return None
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*px$", s)
    if m:
        return int(float(m.group(1)))
    if s.endswith("%"):
        try:
            pct = float(s[:-1])
        except ValueError:
            return None
        return int(base * pct / 100.0) if base else None
    try:
        return int(float(s))
    except ValueError:
        return None


def is_expand(value) -> bool:
    s = str(value or "").strip()
    return s.endswith("%") or s == "100%"