"""可插拔能力后端（图表 / 3D / 文档预览 / Markdown / 代码 / 媒体 / HTML）。

用法：
    from bricks_qt.providers import build_capability, report, install_hint

强制指定后端（环境变量，逗号分隔）：
    set BRICKS_QT_PROVIDERS=chart=qtcharts,markdown=native,docview=pypdfium2
"""

from .base import (CAPABILITY_OF, PROVIDERS, Provider, build_capability, install_hint,
                   pick, providers_for, register, report)

__all__ = ["Provider", "PROVIDERS", "CAPABILITY_OF", "register", "providers_for", "pick",
           "build_capability", "install_hint", "report"]
