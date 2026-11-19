"""中文（宽字符）占 2 格的回归用例。

历史缺陷：终端整段文字按 `drawText` 的自然排版画，而 Consolas 回退出来的中文字形 advance
**小于 2 格**（12pt 实测 16px vs 2×8.797px），一行中文下来逐字左移；光标/选区是按格算的，
于是光标看着"偏右"，vi 里一行中文后面最明显。修法：位置只由格号决定，宽字符横向缩放到正好
占满 2 格（见 `Terminal._paint_cells` / `_draw_fitted`）。

用法：python tests/test_cjk_grid.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt                            # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF   # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402

from bricks_qt.terminal import CURSOR_BG, Terminal, char_cells   # noqa: E402

FAIL = []


def check(cond, msg: str) -> None:
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAIL.append(msg)


class FakeFm:
    """按字符给 advance：ASCII 正好 1 格，中文故意少给（模拟真实回退字体的偏差）。"""

    def __init__(self, cell_w: float, wide: float):
        self.cell_w, self.wide = cell_w, wide

    def horizontalAdvance(self, s: str) -> float:
        return sum(self.wide if char_cells(ch) == 2 else self.cell_w for ch in s)


class FakePainter:
    """只记录绘制调用，用来断言"画在哪个格子"，与字体、平台无关。"""

    def __init__(self):
        self.texts = []          # [(x, 文本)]，缩放过的记实际落点
        self._tx = 0.0
        self._k = 1.0
        self._stack = []
        self.rects = []          # [(x, w)]
        self.font = None
        self.pen = None

    def save(self):
        self._stack.append((self._tx, self._k))

    def restore(self):
        self._tx, self._k = self._stack.pop()

    def translate(self, x, _y):
        self._tx += x

    def scale(self, k, _ky):
        self._k *= k

    def setPen(self, c):
        self.pen = c

    def setFont(self, f):
        self.font = f

    def fillRect(self, rect, _color):
        self.rects.append((round(rect.x(), 2), round(rect.width(), 2)))

    def drawText(self, pt, text):
        self.texts.append((round(self._tx + pt.x() * self._k, 2), text))


def test_char_cells():
    print("char_cells：中文/全角 2 格，ASCII 1 格")
    check(char_cells("中") == 2, "中文 2 格")
    check(char_cells("ａ") == 2, "全角字母 2 格")
    check(char_cells("a") == 1, "ASCII 1 格")
    check(char_cells(" ") == 1, "空格 1 格")


def test_grid_positions(app):
    print("绘制位置只由格号决定（假字体：中文 advance 只有 1.8 格）")
    t = Terminal(font_size=12)
    t._cell_w, t._cell_h, t._pad_x, t._pad_y = 8.0, 16.0, 10.0, 8.0
    fm = FakeFm(8.0, 14.4)                       # 2 格 = 16.0，字形只给 14.4
    font = QFont()
    p = FakePainter()
    pieces = [("a", 1), ("中", 2), ("b", 1)]
    t._paint_cells(p, pieces, 2, 0, Qt.white, Qt.black, False, ((font, fm), (font, fm)))
    want = [(round(10 + 2 * 8.0, 2), "a"), (round(10 + 3 * 8.0, 2), "中"), (round(10 + 5 * 8.0, 2), "b")]
    check(p.texts == want, "三个字分别落在第 2/3/5 格：%r" % (p.texts,))
    check(p.rects == [(round(10 + 2 * 8.0, 2), 32.0)], "背景宽度按格数算（4 格）：%r" % (p.rects,))
    if len(p.texts) >= 2:
        # 中文被横向缩放到正好 2 格：scale = 16.0/14.4 ≈ 1.111
        check(abs(p.texts[1][0] - (10 + 3 * 8.0)) < 0.01, "中文起点仍在格线上（不随字形 advance 漂移）")
    t.deleteLater()


def test_resize_replay(app):
    print("缩窗口重放不丢列（中文第二格是占位，不能补空格）")
    t = Terminal(font_size=12)
    t._screen.reset()
    t.feed("中文ab".encode("utf-8"))
    line = t._screen.buffer[0]
    check(line[0].data == "中" and line[4].data == "a", "原始排布：中(0-1) 文(2-3) a(4)")
    t._resize_screen(t._screen.lines, t._screen.columns - 5)
    line = t._screen.buffer[0]
    got = "".join((line[x].data if line.get(x) else "") for x in range(7))
    check(got.startswith("中文ab"), "重放后仍是 中文ab：%r" % got)
    check(line[4].data == "a" and line[5].data == "b", "a/b 没被挤到后面：a@4 b@5")
    t.deleteLater()


def test_pixels_real_font(app):
    """真字体下量像素：文字右边缘离光标块不能超过 1 格。无字库（offscreen）或无偏差时跳过。"""
    fams = QFontDatabase.families()
    t = Terminal(font_size=12)
    if not fams:
        print("  跳过像素用例（无字库，offscreen 环境）")
        t.deleteLater()
        return
    cell_w = t._cell_w
    mismatch = abs(QFontMetricsF(t._font).horizontalAdvance("中") - 2 * cell_w)
    if mismatch < 0.5:
        print("  跳过像素用例（本机中文字形 advance 正好 2 格）")
        t.deleteLater()
        return
    print("像素用例：中文 advance 比 2 格少 %.2fpx/字" % mismatch)
    t.setAttribute(Qt.WA_DontShowOnScreen, True)
    t.resize(900, 240)
    t.show()
    app.processEvents()
    t._screen.reset()
    t.feed((("中文测试" * 5) + "xy").encode("utf-8"))
    app.processEvents()
    img = t.grab().toImage()
    bg = img.pixelColor(3, 3)
    dpr = t.devicePixelRatioF()
    y0, y1 = int(t._pad_y * dpr), int((t._pad_y + t._cell_h) * dpr)

    def runs(pred):
        cols = [any(pred(img.pixelColor(x, y)) for y in range(y0, y1)) for x in range(img.width())]
        out, s = [], None
        for x, v in enumerate(cols):
            if v and s is None:
                s = x
            if not v and s is not None:
                out.append((s, x - 1))
                s = None
        return out

    text = runs(lambda c: c != bg and c != CURSOR_BG)
    cur = runs(lambda c: c == CURSOR_BG)
    check(bool(text) and bool(cur), "文字与光标都画出来了")
    if text and cur:
        gap = (cur[0][0] - text[-1][1]) / dpr
        check(0 <= gap <= cell_w, "文字右边缘 → 光标：%.1fpx（须在 1 格 %.1fpx 内）" % (gap, cell_w))
    t.close()
    t.deleteLater()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    test_char_cells()
    test_grid_positions(app)
    test_resize_replay(app)
    test_pixels_real_font(app)
    print("失败 %d 项" % len(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())