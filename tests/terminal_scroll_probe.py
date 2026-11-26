"""回滚/滚动条/跨屏选择 离线探针（offscreen，无需 SSH）。

覆盖 4 件事：
1. 上翻之后来新输出，视图不动（旧实现会被 HistoryScreen.before_event 拉回底部）
2. 滚动条存在、范围/值跟 `_scroll` 一致
3. 选区用绝对行号：跨屏拖选（含往上滚）能取到屏幕外的内容
4. 全选包含整个回滚缓冲

用法：QT_QPA_PLATFORM=offscreen python tests/terminal_scroll_probe.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, QSize, Qt                     # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from bricks_qt.terminal import Terminal                          # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    t = Terminal(font_size=10)
    t.resize(QSize(600, 300))
    t.show()
    app.processEvents()
    rows = t._screen.lines
    for i in range(200):
        t.feed(("line-%03d\r\n" % i).encode())
    app.processEvents()
    t._sync_bar()

    # 1) 上翻后新输出不许把视图拉回底部
    t.scroll_history(3)
    app.processEvents()
    before = t.screen_text()
    check("line-199" not in before, "上翻 3 屏后看不到最新行")
    check(t._scroll > 0, "上翻后 _scroll > 0（%d）" % t._scroll)
    for i in range(200, 210):
        t.feed(("line-%03d\r\n" % i).encode())
    app.processEvents()
    after = t.screen_text()
    check(after == before, "新输出后视图内容不变（被打断=FAIL）")
    check(t._scroll > 0, "新输出后仍在回看位置（_scroll=%d）" % t._scroll)

    # 2) 滚动条
    bar = t._vbar
    check(bar.isVisible(), "滚动条可见")
    check(bar.maximum() == len(t._screen.scrollback), "滚动条 max = 回滚行数")
    check(bar.value() == bar.maximum() - t._scroll, "滚动条位置与 _scroll 对齐")
    t.scroll_to_bottom()
    app.processEvents()
    check(bar.value() == bar.maximum(), "回到底部 = 滚动条到底")
    check("line-209" in t.screen_text(), "回到底部看得到最新行")

    # 3) 跨屏拖选：从底部往上拖到顶边（触发边缘自动滚屏），选区要包含屏幕外的行
    cell_h = t._cell_h
    last_row_y = int(t._pad_y + (rows - 2) * cell_h + 1)
    QTest.mousePress(t, Qt.LeftButton, Qt.NoModifier, QPoint(30, last_row_y))
    t._drqa = None
    # 模拟向上拖到顶边：mouseMoveEvent 会启动 _auto，手动跑几拍让它滚起来
    ev_pos = QPoint(30, 1)
    t._drag_pos = ev_pos
    t._sel_b = t._cell_at(ev_pos)
    t._selecting = True
    for _ in range(12):
        t._auto_scroll()
    txt = t.selection_text()
    QTest.mouseRelease(t, Qt.LeftButton, Qt.NoModifier, QPoint(30, last_row_y))
    check(txt.count("\n") > rows, "拖选跨屏（%d 行 > 一屏 %d 行）" % (txt.count("\n") + 1, rows))
    check("line-208" in txt, "跨屏选区包含靠底部的行（屏幕外/边缘的行也进选区）")
    check("ine-1" in txt, "跨屏选区一路往上取到更早的行")

    # 4) 全选覆盖回滚缓冲
    t.select_all()
    everything = t.selection_text()
    check("line-000" in everything, "全选含最早的 line-000")
    check("line-209" in everything, "全选含最新的 line-209")
    check(len(everything.splitlines()) >= 210, "全选行数 ≥ 210（%d）"
          % len(everything.splitlines()))

    # 5) 敲键盘回到底部（xterm 语义）
    t.scroll_history(5)
    app.processEvents()
    check(t._scroll > 0, "先上翻")
    t.send(b"x")
    check(t._scroll == 0, "输入后自动回到底部")

    t.close()
    print()
    if FAIL:
        print("失败 %d 项：%s" % (len(FAIL), FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())