"""原生控件冒烟：Terminal / RemoteFiles 能在无头环境建起来并工作。

CI 在 windows-latest 与 ubuntu-latest 都跑这个——这就是"bricks 原生可用"的自动化证据。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSize, Qt               # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

from bricks_qt import Ctx, build, load_file        # noqa: E402
from bricks_qt.terminal import Terminal            # noqa: E402
from bricks_qt.remotefiles import RemoteFiles      # noqa: E402

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "examples", "native_terminal.ui")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    node = load_file(UI)
    ctx = Ctx(base_dir=os.path.dirname(UI))
    calls = []
    ctx.on("connect", lambda n, e, p: calls.append("connect"))
    root = build(node, ctx)
    assert root is not None, "native_terminal.ui 渲染为空"

    term = ctx.get("term")
    files = ctx.get("files")
    assert isinstance(term, Terminal), "Terminal 没建出来：%r" % type(term)
    assert isinstance(files, RemoteFiles), "RemoteFiles 没建出来：%r" % type(files)

    # 喂一段带颜色的输出，屏幕缓冲区必须收下
    term.feed(b"(py3) \x1b[01;32mymq@home\x1b[00m:\x1b[01;34m~\x1b[00m$ echo hi\r\nhi\r\n")
    for _ in range(10):
        app.processEvents()
        time.sleep(0.02)
    txt = term.screen_text()
    assert "ymq@home:~$ echo hi" in txt, "终端没收到内容：%r" % txt[:60]

    # 绘制一帧：颜色解析出错会在这里抛
    term.resize(QSize(600, 300))
    app.processEvents()
    img = term.grab().toImage()
    assert not img.isNull(), "终端截图失败"

    # 动作桥：模拟点击 .ui 里的按钮
    ctx.get("btn_connect").click()
    app.processEvents()
    assert calls == ["connect"], "宿主动作没被调用：%r" % calls

    # 未 attach 的 RemoteFiles 必须是禁用态（不能崩）
    assert not files.btn_upload.isEnabled(), "未连接时上传按钮应禁用"
    print("native smoke ok: Terminal + RemoteFiles + 动作桥（%s）" % sys.platform)
    return 0


if __name__ == "__main__":
    sys.exit(main())
