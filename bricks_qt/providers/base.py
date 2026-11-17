"""可插拔的"能力"后端：图表 / 3D / 文档预览 / 富文本 / 媒体，每个能力可以有多个实现。

设计要点：
  * widgettype → capability（能力名），一个能力可由多个 provider 实现；
  * provider 自己声明所需模块与 pip extra，**懒加载**（import 只在真正用到时发生）；
  * 按 priority 择优，可用环境变量强制指定：`BRICKS_QT_PROVIDERS="chart=qtcharts,markdown=native"`；
  * 某个能力一个 provider 都不可用时，返回 None，由上层画带安装提示的占位框。
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..model import Node


# ── widgettype → 能力 ──
CAPABILITY_OF: Dict[str, str] = {
    "ChartBar": "chart", "ChartLine": "chart", "ChartPie": "chart", "ChartRadar": "chart",
    "ChartScatter": "chart", "ChartKLine": "chart", "ChartHeatmap": "chart", "ChartMap": "chart",
    "Scene3D": "scene3d", "GlbViewer": "scene3d",
    "MarkdownViewer": "markdown", "MdWidget": "markdown",
    "CodeEditor": "code",
    "DOCXviewer": "docview", "PDFviewer": "docview", "EXCELviewer": "docview",
    "Html": "html", "Iframe": "html",
    "Video": "media", "VideoPlayer": "media", "AudioRecorder": "media",
    "Camera": "media", "ASRClient": "media",
}


@dataclass
class Provider:
    name: str
    capability: str
    priority: int
    factory: Callable[[Node, object], object]
    requires: str = ""          # 需要能 import 的模块（逗号分隔，全部满足才算可用）
    extra: str = ""             # pip extra 名，缺依赖时提示用
    note: str = ""              # 额外说明（例如许可证）
    tags: List[str] = field(default_factory=list)

    def available(self) -> Optional[str]:
        """可用返回 None，不可用返回原因。"""
        if not self.requires:
            return None
        for mod in [m.strip() for m in self.requires.split(",") if m.strip()]:
            try:
                if importlib.util.find_spec(mod) is None:
                    return "缺模块 %s" % mod
            except (ImportError, ValueError):
                return "缺模块 %s" % mod
        return None


PROVIDERS: List[Provider] = []


def register(p: Provider) -> Provider:
    PROVIDERS.append(p)
    return p


def _overrides() -> Dict[str, str]:
    raw = os.environ.get("BRICKS_QT_PROVIDERS", "").strip()
    out: Dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def providers_for(capability: str) -> List[Provider]:
    cands = [p for p in PROVIDERS if p.capability == capability]
    forced = _overrides().get(capability)
    if forced:
        cands.sort(key=lambda p: (0 if p.name == forced else 1, -p.priority))
    else:
        cands.sort(key=lambda p: -p.priority)
    return cands


def pick(capability: str) -> Optional[Provider]:
    for p in providers_for(capability):
        if p.available() is None:
            return p
    return None


def build_capability(node: Node, ctx) -> Optional[object]:
    """按能力找 provider 造控件；全不可用返回 None。"""
    cap = CAPABILITY_OF.get(node.widgettype)
    if not cap:
        return None
    last_err = ""
    for p in providers_for(cap):
        why = p.available()
        if why:
            continue
        try:
            w = p.factory(node, ctx)
            if w is not None:
                print("[bricks-qt] %s ← %s" % (node.widgettype, p.name))
                return w
        except Exception as e:                     # noqa: BLE001
            last_err = "%s 失败：%s" % (p.name, str(e)[:80])
            ctx.warn("%s: %s" % (node.widgettype, last_err))
    if last_err:
        ctx.warn("%s 所有可用 provider 都失败" % node.widgettype)
    return None


def install_hint(capability: str) -> str:
    extras = sorted({p.extra for p in PROVIDERS if p.capability == capability and p.extra})
    return ("pip install 'bricks-qt[%s]'" % "|".join(extras)) if extras else ""


def report() -> List[dict]:
    caps = sorted({p.capability for p in PROVIDERS})
    rows = []
    for cap in caps:
        provs = []
        for p in providers_for(cap):
            why = p.available()
            provs.append({"name": p.name, "priority": p.priority, "ready": why is None,
                          "why": why or "", "extra": p.extra, "note": p.note})
        chosen = pick(cap)
        rows.append({"capability": cap, "chosen": chosen.name if chosen else None,
                     "hint": install_hint(cap), "providers": provs})
    return rows


# 注册内置 provider（只登记元数据，模块在 factory 里才 import）
from . import charts as _charts        # noqa: E402,F401
from . import docs as _docs            # noqa: E402,F401
from . import native as _native        # noqa: E402,F401
from . import web as _web              # noqa: E402,F401