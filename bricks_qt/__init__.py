"""bricks .ui 的 Qt(Qt6/PySide6) 原生渲染器 — P0 原型。

一句话：读 bricks 的 .ui JSON（widgettype/options/subwidgets/binds），
用 Qt 原生控件搭出同样的界面；JS 生态重的控件先占位（后续接 QWebEngineView）。

模块：
  model    容错解析（.ui 不是严格 JSON：单引号/尾逗号/注释）+ 尺寸换算
  widgets  widgettype → QWidget 注册表（P0 覆盖布局/表格/表单/导航/模态等）
  app      装进 QMainWindow；无头渲染截图用于验收
  cli      命令行：show / shot / check / types
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
