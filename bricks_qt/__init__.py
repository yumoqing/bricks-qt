"""bricks .ui 的 Qt(Qt6/PySide6) 原生渲染器。

一句话：读 bricks 的 .ui JSON（widgettype/options/subwidgets/binds），
用 Qt 原生控件搭出同样的界面；JS 生态重的控件走可插拔 provider（WebEngine 兜底）。

宿主扩展三个入口：
  ctx.on(name, fn)      注册动作：.ui 里 binds.actiontype=script / script=<name> 会调到它
  ctx.get("id")         按 .ui 里的 widget id 取到建好的控件
  register(type)(fn)    把自己的控件类型加进注册表（fn(node, ctx) -> QWidget）

模块：
  model       容错解析（.ui 不是严格 JSON）+ 尺寸换算
  widgets     widgettype → QWidget 注册表 + Ctx（事件/动作/引用）
  terminal    原生终端（pyte + 自绘），类型名 Terminal
  remotefiles 原生远端文件面板（拖拽上传/下载），类型名 RemoteFiles
  app         装进 QMainWindow；无头渲染截图
  cli         show / shot / check / types / caps
"""

from .model import Node, load_file, parse_node          # noqa: F401
from .widgets import Ctx, REGISTRY, build, register      # noqa: F401

__version__ = "0.2.0"
__all__ = ["__version__", "Node", "load_file", "parse_node", "Ctx", "REGISTRY", "build", "register"]
