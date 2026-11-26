"""Terminal：bricks 的原生终端控件（pyte 屏幕 + QPainter 自绘）。

对应 bricks 的 `Terminal` 类型，和 web 侧的 `Wterm`（xterm.js）同一个位置：

- 文本/颜色：pyte 解析 ANSI（16 色 / 256 色 / 真彩）、粗体、反显
- 交互：拖选、双击选词、Ctrl+C/Ctrl+Shift+C 复制、右键菜单、Shift+PgUp/PgDn 回滚
- 回滚：右侧滚动条 + 滚轮，上翻时**不会被新输出打断**（视图锚在用户看的那几行上），
  拖选到边缘自动滚屏 → 复制可以跨屏，不必先滚再选
- 中文：`WA_InputMethodEnabled` + inputMethodEvent（候选窗定位靠 inputMethodQuery）
- 性能：读入走队列 + 16ms 合帧，整屏重绘 <1ms

宿主用法（两种选一）：

    t = Terminal(font_size=12)
    t.set_sender(lambda b: my_channel.write(b))      # 往外发
    t.feed(b"...")                                   # 往里喂（可从任意线程）

    # 或者挂一个满足 recv(n)/sendall(bytes)/resize_pty(w,h) 契约的通道
    t.attach_channel(chan)
"""

from __future__ import annotations

import collections
import queue
import threading
from typing import Optional

import pyte
from wcwidth import wcwidth
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QKeyEvent, QPainter, QPaintEvent,
                           QResizeEvent, QGuiApplication)
from PySide6.QtWidgets import QApplication, QScrollBar, QWidget

MONO_FONTS = {
    "nt": ["Consolas", "Cascadia Mono", "Courier New"],
    "darwin": ["Menlo", "SF Mono", "Monaco", "Courier New"],
    "posix": ["DejaVu Sans Mono", "Ubuntu Mono", "Liberation Mono", "Noto Sans Mono", "monospace"],
}
DEFAULT_FG = QColor("#e6e6e6")
DEFAULT_BG = QColor("#1e1e1e")
CURSOR_BG = QColor("#00c896")

# pyte 的 16 色 → 接近 Windows Terminal 的配色
ANSI16 = ["#0c0c0c", "#c50f1f", "#13a10e", "#c19c00", "#0037da", "#881798", "#3a96dd", "#cccccc",
          "#767676", "#e74856", "#16c60c", "#f9f1a5", "#3b78ff", "#b4009e", "#61d6d6", "#f2f2f2"]

# pyte 的颜色名 → xterm 配色（暗/亮两套）
NAMED = {"black": "#0c0c0c", "red": "#c50f1f", "green": "#13a10e", "brown": "#c19c00",
         "blue": "#0037da", "magenta": "#881798", "cyan": "#3a96dd", "white": "#cccccc"}
NAMED_BRIGHT = {"black": "#767676", "red": "#e74856", "green": "#16c60c", "brown": "#f9f1a5",
                "blue": "#3b78ff", "magenta": "#b4009e", "cyan": "#61d6d6", "white": "#f2f2f2"}
HEXCHARS = set("0123456789abcdef")


def xterm256(n: int) -> str:
    """xterm 256 色下标 → #rrggbb。"""
    n = int(n) & 0xFF
    if n < 16:
        return (ANSI16 + ["#0c0c0c", "#e74856", "#16c60c", "#f9f1a5", "#3b78ff",
                          "#b4009e", "#61d6d6", "#f2f2f2"])[n]
    if n < 232:
        n -= 16
        lv = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (lv[n // 36], lv[(n // 6) % 6], lv[n % 6])
    g = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (g, g, g)


_COLOR_CACHE: dict = {}


def color_of(val, default: QColor, bold: bool = False) -> QColor:
    """把 pyte 的 cell.fg / cell.bg 翻成 QColor；任何认不出的值都退回默认色。

    结果按 (颜色值, 默认色, 粗体) 缓存：整屏绘制每帧要翻几万个格子，
    每次 new 一个 QColor 能把一帧从 10ms 拖到 20ms 以上。
    """
    key = None
    if isinstance(val, (str, int, tuple, type(None))):
        key = (val, default.name(), bool(bold))
    if key is not None:
        hit = _COLOR_CACHE.get(key)
        if hit is not None:
            return hit
    c = _build_color(val, default, bold)
    if key is not None and len(_COLOR_CACHE) < 8192:
        _COLOR_CACHE[key] = c
    return c


def _build_color(val, default: QColor, bold: bool = False) -> QColor:
    try:
        if val is None:
            return default
        if isinstance(val, bool):
            return default
        if isinstance(val, int):
            return QColor(xterm256(val))
        if isinstance(val, (tuple, list)) and len(val) == 3:
            return QColor(int(val[0]) & 255, int(val[1]) & 255, int(val[2]) & 255)
        if not isinstance(val, str):
            return default
        v = val.strip().lower()
        if not v or v.startswith("default"):
            return default
        if v in NAMED:
            return QColor(NAMED_BRIGHT.get(v) if bold else NAMED[v])
        if len(v) == 6 and set(v) <= HEXCHARS:
            return QColor("#" + v)
        return default
    except Exception:                       # noqa: BLE001
        return default


KEYS = {
    Qt.Key_Return: b"\r", Qt.Key_Enter: b"\r", Qt.Key_Backspace: b"\x7f", Qt.Key_Tab: b"\t",
    Qt.Key_Escape: b"\x1b", Qt.Key_Up: b"\x1b[A", Qt.Key_Down: b"\x1b[B",
    Qt.Key_Right: b"\x1b[C", Qt.Key_Left: b"\x1b[D",
    Qt.Key_Home: b"\x1b[H", Qt.Key_End: b"\x1b[F",
    Qt.Key_PageUp: b"\x1b[5~", Qt.Key_PageDown: b"\x1b[6~",
    Qt.Key_Insert: b"\x1b[2~", Qt.Key_Delete: b"\x1b[3~",
}
FKEYS = [Qt.Key_F1, Qt.Key_F2, Qt.Key_F3, Qt.Key_F4, Qt.Key_F5, Qt.Key_F6, Qt.Key_F7, Qt.Key_F8,
         Qt.Key_F9, Qt.Key_F10, Qt.Key_F11, Qt.Key_F12]

#: Ctrl+键 → 控制字节（与 xterm 口径一致）。
#: 为什么不能只靠 QKeyEvent.text()：text() 会不会带出控制字符**按平台不同**
#: （X11 上 Ctrl+S 的 text 是 "\x13"，Windows/macOS 上常常是空串）。只走 text 分支的话，
#: Ctrl+S(XOFF 暂停输出)/Ctrl+Q(XON 恢复)/Ctrl+Z(挂起) 在这些平台上按下去毫无反应——
#: 就是"快捷键与终端不符"的那个 bug。这里按 key() 显式映射，任何平台都发得出去。
_CTRL = {
    Qt.Key_Space: 0x00, Qt.Key_At: 0x00, Qt.Key_2: 0x00,
    Qt.Key_3: 0x1B, Qt.Key_4: 0x1C, Qt.Key_5: 0x1D, Qt.Key_6: 0x1E, Qt.Key_7: 0x1F,
    Qt.Key_8: 0x7F,
    Qt.Key_BracketLeft: 0x1B, Qt.Key_Backslash: 0x1C, Qt.Key_BracketRight: 0x1D,
    Qt.Key_AsciiCircum: 0x1E, Qt.Key_Underscore: 0x1F,
}


def ctrl_byte(key: int) -> Optional[int]:
    """Ctrl+<键> 对应的控制字节；不是 Ctrl+字母/符号的键返回 None。"""
    if Qt.Key_A <= key <= Qt.Key_Z:
        return key - Qt.Key_A + 1                 # Ctrl+A=0x01 … Ctrl+Z=0x1A
    return _CTRL.get(key)


def char_cells(ch: str) -> int:
    """一个字符在终端里占几格：中文/全角 2 格，其余 1 格（与 pyte 的排布口径一致）。"""
    try:
        w = wcwidth(ch[0]) if ch else 1
    except Exception:                       # noqa: BLE001
        w = 1
    return 2 if (w or 0) > 1 else 1


def pick_mono_family() -> str:
    """按平台挑一个装了的等宽字体（Consolas 在 macOS/Linux 上通常没有）。"""
    import sys
    from PySide6.QtGui import QFontDatabase
    key = "nt" if sys.platform.startswith("win") else ("darwin" if sys.platform == "darwin" else "posix")
    fams = set(QFontDatabase.families())
    for f in MONO_FONTS.get(key, []):
        if f in fams:
            return f
    return MONO_FONTS["posix"][-1]


class ScrollbackScreen(pyte.Screen):
    """pyte.Screen + 纯追加式的回滚缓冲。

    为什么不用 pyte 自带的 `HistoryScreen`：它的 `before_event` 会在**任何**事件
    （linefeed / draw / …）之前执行 `while history.position < history.size: next_page()`，
    也就是远端一有新输出就把视图强行拉回底部——用户上翻着的历史瞬间被顶掉，
    这是「终端一有输出就取消上翻」的根因（xterm / Windows Terminal 都不是这个行为）。

    这里改成把滚出屏幕顶部的行追加进 `scrollback`，屏幕缓冲区永远只有当前页；
    「往上看几行」由 Terminal 自己的 `_scroll` 偏移控制，输出只往缓冲尾部追加，
    绝不改动用户的浏览位置。行按 cell dict 的副本存，颜色/粗体/反显都保住。
    """

    def __init__(self, columns: int = 80, lines: int = 24, history: int = 5000):
        self.scrollback: "collections.deque[dict]" = collections.deque(maxlen=history)
        super().__init__(columns, lines)

    def index(self) -> None:
        """光标在底边时，超时上顶的那行先存进回滚缓冲，再交给 pyte 滚屏。"""
        top, bottom = self.margins or pyte.screens.Margins(0, self.lines - 1)
        if self.cursor.y == bottom:
            self.scrollback.append(dict(self.buffer.get(top, {})))
        super().index()

    def erase_in_display(self, how: int = 0, *args, **kwargs) -> None:      # noqa: D401
        """`ESC[3J`（清屏并清历史）要把回滚缓冲一起清掉，否则 clear 之后还能翻出旧内容。"""
        if how == 3:
            self.scrollback.clear()
        super().erase_in_display(how, *args, **kwargs)


class Terminal(QWidget):
    """一个 pyte 屏幕 + 自绘；字体大小变化只影响显示，col/row 会自动重算。"""

    connected_changed = Signal(bool)
    title_changed = Signal(str)

    def __init__(self, parent=None, font_family: str = "", font_size: int = 12):
        super().__init__(parent)
        if not font_family or (font_family == "Consolas" and not __import__("sys").platform.startswith("win")):
            font_family = pick_mono_family()
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._font_family = font_family
        self._font_size = font_size
        self._screen = ScrollbackScreen(100, 30, history=5000)   # 带 5000 行回滚历史
        self._stream = pyte.ByteStream(self._screen)
        self._chan = None
        self._sender = None            # 宿主注入的发送函数（可以不走 channel）
        self.on_input = None           # 用户输入回调；不设就走 sender/channel
        self._inbox: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._preedit = ""
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cell_w = 8.0
        self._cell_h = 16.0
        self._pad_x = 10.0             # 文字不要贴着边框
        self._pad_y = 8.0
        self._font = QFont(font_family, font_size)
        self._font.setStyleHint(QFont.Monospace)
        self._refresh_metrics()
        self._blink = True
        self._pen = self._font_used = None      # 绘制缓存：同色同字体不再 setPen/setFont
        self._scroll = 0               # 向上回看的行数（0 = 跟随底部，不随输出跳动）
        self._bar_w = 10               # 右侧滚动条占的宽度（cols 里一直预留，出现时不回流）
        self._sel_a = None             # 选择锚点 (绝对行号, col)
        self._sel_b = None             # 选择终点 (绝对行号, col)
        self._selecting = False
        self._drag_pos = None          # 最近一次拖选的鼠标位置（边缘自动滚屏用）
        self.setMouseTracking(True)

        # 右侧滚动条：QWidget 自绘的终端没有原生滚动条，只能自己挂一个。
        # 为什么不用 QAbstractScrollArea：Terminal 是 bricks 的一个 widgettype，
        # 换成 scroll area 会改掉 .ui 里的类型名和宿主用法；挂个子控件影响面最小。
        self._vbar = QScrollBar(Qt.Vertical, self)
        self._vbar.setStyleSheet(
            "QScrollBar:vertical{background:#1e1e1e;width:%dpx;margin:0;border:0;}"
            "QScrollBar::handle:vertical{background:#4a4a4a;min-height:24px;border-radius:4px;}"
            "QScrollBar::handle:vertical:hover{background:#6a6a6a;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;border:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
            % self._bar_w)
        self._vbar.setToolTip("上下拖动回看历史（滚轮 / Shift+PgUp 也可）")
        self._vbar.valueChanged.connect(self._on_bar_moved)
        self._vbar.hide()
        self._syncing_bar = False

        self._auto = QTimer(self)              # 拖选到边缘时自动滚屏
        self._auto.setInterval(60)
        self._auto.timeout.connect(self._auto_scroll)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_blink)
        self._timer.start(600)
        # 60fps 批量消费：一次 tick 把所有排队的输出一口气喂进 pyte，只重绘一次。
        # 原来每块数据都从工作线程里 singleShot 一次 update()，输出一多就堆成几千个定时器 → 明显卡顿。
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._drain)
        self._tick.start(16)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)   # 中文输入法要这个

    # ---------------- 字体 ----------------
    def sizeHint(self):                          # noqa: N802
        return QSize(720, 420)

    def minimumSizeHint(self):                   # noqa: N802
        return QSize(240, 120)

    def font_size(self) -> int:
        return self._font_size

    def set_font_family(self, family: str) -> None:
        if not family or family == self._font_family:
            return
        self._font_family = family
        self._font = QFont(family, self._font_size)
        self._font.setStyleHint(QFont.Monospace)
        self._refresh_metrics()
        self.update()

    def set_font_size(self, size: int) -> None:
        size = max(6, min(40, int(size)))
        if size == self._font_size:
            return
        self._font_size = size
        self._font = QFont(self._font_family, size)
        self._font.setStyleHint(QFont.Monospace)
        self._refresh_metrics()
        self._apply_size_to_chan()
        self.update()

    def _refresh_metrics(self) -> None:
        fm = QFontMetricsF(self._font)
        self._cell_w = max(4.0, fm.horizontalAdvance("M"))
        self._cell_h = max(6.0, fm.height())
        # 滚动条的宽度一直预留：否则它一出现就少一列、文字整体回流一次
        bar = getattr(self, "_bar_w", 10)
        aw = max(0.0, self.width() - 2 * self._pad_x - bar)
        ah = max(0.0, self.height() - 2 * self._pad_y)
        cols = max(20, int(aw / self._cell_w)) if self.width() else 100
        rows = max(5, int(ah / self._cell_h)) if self.height() else 30
        self._resize_screen(rows, cols)

    # ---- 颜色 → SGR（缩屏重放时要保住颜色，不能只存文本）----
    def _rgb(self, val, bold: bool = False):
        if val is None:
            return None
        if isinstance(val, str) and val.lower().startswith("default"):
            return None
        c = color_of(val, DEFAULT_FG, bold)
        return (c.red(), c.green(), c.blue())

    def _row_to_ansi(self, row: dict, cols: int) -> str:
        parts = []
        last = None
        for x in range(cols):
            cell = row.get(x)
            if cell is None:
                parts.append(" ")
                continue
            if cell.data == "":
                # 宽字符（中文）的第二格占位：左边那格重放时已经吃掉 2 列，
                # 这里再补一个空格会让整行右移一格（缩窗口后中文行越缩越歪）
                continue
            fg, bg = cell.fg, cell.bg
            if getattr(cell, "reverse", False):
                fg, bg = bg, fg
            spec = (bool(getattr(cell, "bold", False)), self._rgb(fg, bool(getattr(cell, "bold", False))),
                    self._rgb(bg))
            if spec != last:
                codes = ["0"]
                if spec[0]:
                    codes.append("1")
                if spec[1]:
                    codes.append("38;2;%d;%d;%d" % spec[1])
                if spec[2]:
                    codes.append("48;2;%d;%d;%d" % spec[2])
                parts.append("\x1b[" + ";".join(codes) + "m")
                last = spec
            parts.append(cell.data or " ")
        parts.append("\x1b[0m")
        return "".join(parts)

    def _resize_screen(self, rows: int, cols: int) -> None:
        """pyte 自己 resize 会把行序搞乱、属性丢掉，所以先按 SGR 编码存下来再重放。

        早期版本只存 `display`（纯文本），结果一拉伸窗口，颜色/粗体/反显全没了。"""
        if (rows, cols) == (self._screen.lines, self._screen.columns):
            return
        old_rows, old_cols = self._screen.lines, self._screen.columns
        saved = [self._row_to_ansi(self._screen.buffer.get(r, {}), old_cols)
                 for r in range(old_rows)]
        # 去掉尾部空行
        while saved and not saved[-1].replace("\x1b[0m", "").strip():
            saved.pop()
        self._screen.resize(rows, cols)
        self._screen.reset()
        keep = saved[-rows:] if rows < len(saved) else saved
        if keep:
            self._stream.feed(("\r\n".join(keep) + "\r\n").encode("utf-8"))
        self._sync_bar()

    def _apply_size_to_chan(self) -> None:
        if self._chan is not None:
            try:
                self._chan.resize_pty(width=self._screen.columns, height=self._screen.lines)
            except Exception:                       # noqa: BLE001
                pass

    # ---------------- 通道 ----------------
    def attach_channel(self, chan) -> None:
        self._chan = chan
        self._stop.clear()
        while True:                                  # 换通道时丢掉旧残留
            try:
                self._inbox.get_nowait()
            except queue.Empty:
                break
        self._reader = threading.Thread(target=self._read_loop, args=(chan,), daemon=True)
        self._reader.start()

    def _read_loop(self, chan) -> None:
        """工作线程：只把数据塞进队列，绝不碰 pyte 屏幕（避免与绘制线程抢状态）。"""
        while not self._stop.is_set():
            try:
                data = chan.recv(65536)
            except Exception:                       # noqa: BLE001
                break
            if not data:
                break
            self._inbox.put(data)
        self._inbox.put(None)                       # 结束标记

    def _feed_stream(self, data: bytes) -> None:
        """喂数据进 pyte，并保持"用户上翻时视图不动"。

        输出把行顶进回滚缓冲时，`_scroll` 跟着加上同样的行数：屏幕内容还是那几行，
        不会被新输出顶走（这是「一有输出就取消上翻」的直接修法）。
        """
        sb = self._screen.scrollback
        before = len(sb)
        self._stream.feed(data)
        grew = len(sb) - before
        if grew:
            if self._scroll:
                self._scroll = min(len(sb), self._scroll + grew)
            self._sync_bar()

    def _drain(self) -> None:
        """GUI 线程：把排队输出一次性喂进去，只重绘一次。"""
        fed = 0
        closed = False
        while fed < 512 * 1024:
            try:
                data = self._inbox.get_nowait()
            except queue.Empty:
                break
            if data is None:
                closed = True
                continue
            try:
                self._feed_stream(data)
                fed += len(data)
            except Exception:                       # noqa: BLE001
                pass
        if fed:
            self.update()
        if closed:
            self.connected_changed.emit(False)

    def detach(self) -> None:
        self._stop.set()
        if self._chan is not None:
            try:
                self._chan.close()
            except Exception:                       # noqa: BLE001
                pass
            self._chan = None

    def feed(self, data: bytes) -> None:
        """宿主把远端数据喂进来。主线程直接喂（立即上屏），其他线程走队列等 16ms 合帧。"""
        if not data:
            return
        if threading.current_thread() is threading.main_thread():
            try:
                self._feed_stream(data)
            except Exception:                    # noqa: BLE001
                return
            self.update()
        else:
            self._inbox.put(data)

    def feed_eof(self) -> None:
        self._inbox.put(None)

    def set_sender(self, fn) -> None:
        """宿主注入发送函数：fn(bytes)。设了就不需要 channel。"""
        self._sender = fn

    def send(self, data: bytes) -> None:
        # 用户在往上翻历史时敲键盘 → 像 xterm 一样直接回到底部，否则输入的东西看不见
        if self._scroll:
            self.scroll_to_bottom()
        if self.on_input is not None:
            self.on_input(data)
            return
        if self._sender is not None:
            self._sender(data)
            return
        if self._chan is None:
            return
        try:
            fn = getattr(self._chan, "send_nowait", None)
            if fn is not None:
                fn(data)                            # 不阻塞 GUI 线程
            else:
                self._chan.sendall(data)
        except Exception:                           # noqa: BLE001
            pass

    # ---------------- 事件 ----------------
    def _on_blink(self) -> None:
        self._blink = not self._blink
        if self._chan is not None:
            self.update()

    def resizeEvent(self, e: QResizeEvent) -> None:      # noqa: N802
        super().resizeEvent(e)
        self._refresh_metrics()
        self._apply_size_to_chan()
        self._sync_bar()
        self.update()

    # ---------------- 历史回滚 ----------------
    # 行号口径：**绝对行号**。0 = 回滚缓冲里最老的一行，len(scrollback) 起是当前屏第 0 行。
    # 视图偏移 `_scroll` 表示"从底部往上看了多少行"，0 就是跟随输出。
    # 选区也用绝对行号存，所以滚动时选区跟着内容走，不会漂到别的行上。
    def _top_abs(self) -> int:
        """当前屏顶行对应的绝对行号。"""
        return max(0, len(self._screen.scrollback) - self._scroll)

    def _total_lines(self) -> int:
        return len(self._screen.scrollback) + self._screen.lines

    def _line_cells(self, abs_line: int) -> dict:
        """绝对行号 → 该行的 cell dict（超出范围给空行）。"""
        sb = self._screen.scrollback
        if 0 <= abs_line < len(sb):
            return sb[abs_line]
        return self._screen.buffer.get(abs_line - len(sb), {})

    def _visible_row(self, y: int) -> dict:
        """屏幕第 y 行（按当前回看偏移）显示的内容。"""
        return self._line_cells(self._top_abs() + y)

    def _row_text(self, cells: dict) -> str:
        cols = self._screen.columns
        return "".join((cells.get(x).data if cells.get(x) else " ") for x in range(cols)).rstrip()

    def scroll_lines(self, n: int) -> None:
        """滚动 `n` 行：正数往上（看更早的内容），负数往下。"""
        if not n:
            return
        total = len(self._screen.scrollback)
        new = max(0, min(total, self._scroll + int(n)))
        if new == self._scroll:
            return
        self._scroll = new
        self._sync_bar()
        self.update()

    def scroll_to_bottom(self) -> None:
        self.scroll_lines(-10 ** 9)

    def scroll_history(self, pages: int) -> None:
        """按屏翻：兼容旧调用（Shift+PgUp/PgDn、右键「回到底部」都走这里）。"""
        step = max(1, self._screen.lines - 1)
        self.scroll_lines(int(pages) * step)

    def _sync_bar(self) -> None:
        """滚动条位置/可见性与 `_scroll` 对齐（条在底部 = 跟随输出）。"""
        if not hasattr(self, "_vbar"):
            return
        sb = len(self._screen.scrollback)
        self._scroll = max(0, min(sb, self._scroll))
        bar = self._vbar
        self._syncing_bar = True
        try:
            bar.setRange(0, sb)
            bar.setPageStep(max(1, self._screen.lines))
            bar.setSingleStep(1)
            bar.setValue(sb - self._scroll)
        finally:
            self._syncing_bar = False
        bar.setGeometry(self.width() - self._bar_w, 0, self._bar_w, self.height())
        want = sb > 0
        if bar.isVisible() != want:
            bar.setVisible(want)

    def _on_bar_moved(self, value: int) -> None:
        if self._syncing_bar:
            return
        self._scroll = max(0, len(self._screen.scrollback) - int(value))
        self.update()

    def _auto_scroll(self) -> None:
        """拖选拖到上下边缘时持续滚屏：选区能一路延伸到屏幕外（复制整段历史）。"""
        pos = self._drag_pos
        if not self._selecting or pos is None:
            self._auto.stop()
            return
        y = pos.y()
        if y < self._pad_y * 2:
            self.scroll_lines(2)
        elif y > self.height() - self._pad_y * 2:
            self.scroll_lines(-2)
        else:
            self._auto.stop()
            return
        self._sel_b = self._cell_at(pos)
        self.update()

    def wheelEvent(self, e) -> None:             # noqa: N802
        dy = e.angleDelta().y()
        if not dy:
            return
        # 普通滚轮 3 行（xterm 口径），Shift+滚轮整屏
        step = max(1, self._screen.lines - 1) if (e.modifiers() & Qt.ShiftModifier) else 3
        self.scroll_lines(step if dy > 0 else -step)
        e.accept()

    # ---------------- 输入法（中文） ----------------
    def inputMethodQuery(self, query):                    # noqa: N802
        cx, cy = self._screen.cursor.x, self._screen.cursor.y
        if query == Qt.ImCursorRectangle:
            return QRect(int(self._pad_x + cx * self._cell_w),
                         int(self._pad_y + cy * self._cell_h),
                         int(self._cell_w), int(self._cell_h))
        if query == Qt.ImCursorPosition:
            return cx
        if query == Qt.ImAnchorPosition:
            return cx
        if query == Qt.ImSurroundingText:
            return "\n".join(self._screen.display)
        if query == Qt.ImCurrentSelection:
            return self.selection_text()
        if query == Qt.ImEnabled:
            return True
        return None

    def inputMethodEvent(self, e) -> None:                 # noqa: N802
        commit = e.commitString()
        if commit:
            self.send(commit.encode("utf-8"))
        self._preedit = e.preeditString() or ""
        self.update()
        e.accept()

    def keyPressEvent(self, e: QKeyEvent) -> None:       # noqa: N802
        mods, key = e.modifiers(), e.key()
        if key == Qt.Key_C and (mods & Qt.ControlModifier):
            if self.copy_selection():                     # 有选中 → 复制
                return
            if mods & Qt.ShiftModifier:                   # 没选中时 Ctrl+Shift+C 复制整屏
                QGuiApplication.clipboard().setText(self.screen_text())
                return
        if key == Qt.Key_A and (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier):
            self.select_all()
            return
        if key == Qt.Key_Insert and (mods & Qt.ControlModifier):
            self.copy_selection()
            return
        if key == Qt.Key_Insert and (mods & Qt.ShiftModifier):
            self.paste()
            return
        if mods & Qt.ControlModifier and mods & Qt.ShiftModifier:
            if key == Qt.Key_C:
                return
            if key == Qt.Key_V:                          # Ctrl+Shift+V 粘贴
                self.send(QApplication.clipboard().text().encode("utf-8"))
                return
        if key in (Qt.Key_PageUp, Qt.Key_PageDown) and (mods & Qt.ShiftModifier):
            self.scroll_history(1 if key == Qt.Key_PageUp else -1)
            return
        if key == Qt.Key_End and (mods & Qt.ShiftModifier) and self._scroll:
            self.scroll_to_bottom()
            return
        # Ctrl+键：按 key() 显式映射成控制字节，不依赖平台是否给出 text()（见 ctrl_byte）。
        # 放在上面几个终端自己的 Ctrl 组合（Ctrl+C 复制、Ctrl+Shift+C/V、Ctrl+Insert）之后，
        # 也不碰 Ctrl+Alt（Windows 的 AltGr 会伪装成 Ctrl+Alt，抢了就打不出 @ € 等字符）。
        if (mods & Qt.ControlModifier) and not (mods & Qt.AltModifier) \
                and not (mods & Qt.MetaModifier):
            b = ctrl_byte(key)
            if b is not None:
                self.send(bytes([b]))
                return
        if key in KEYS:
            self.send(KEYS[key])
            return
        if key in FKEYS:
            n = FKEYS.index(key) + 1
            self.send((b"\x1b[%d~" % (11 + n)) if n > 4 else (b"\x1bO" + bytes([80 + n])))
            return
        text = e.text()
        if text:
            if mods & Qt.ControlModifier and len(text) == 1:
                c = text.upper()
                if "A" <= c <= "Z":
                    self.send(bytes([ord(c) - 64]))
                    return
            self.send(text.encode("utf-8"))

    # ---------------- 选择 / 复制 ----------------
    def _cell_at(self, pos) -> tuple:
        """屏幕坐标 → (绝对行号, col)。绝对行号让选区在滚动后仍然贴在原来那段内容上。"""
        col = int((pos.x() - self._pad_x) // self._cell_w)
        row = int((pos.y() - self._pad_y) // self._cell_h)
        row = max(0, min(self._screen.lines - 1, row))
        col = max(0, min(self._screen.columns - 1, col))
        abs_line = max(0, min(self._total_lines() - 1, self._top_abs() + row))
        return (abs_line, col)

    def _ordered(self):
        if self._sel_a is None or self._sel_b is None:
            return None
        a, b = self._sel_a, self._sel_b
        return (a, b) if (a[0], a[1]) <= (b[0], b[1]) else (b, a)

    def has_selection(self) -> bool:
        """单点点击不算选择。"""
        rng = self._ordered()
        return bool(rng and rng[0] != rng[1])

    def clear_selection(self) -> None:
        self._sel_a = self._sel_b = None
        self.update()

    def select_all(self) -> None:
        # 全选覆盖整个回滚缓冲 + 当前屏：复制历史上千行时不用先拖选
        self._sel_a = (0, 0)
        self._sel_b = (self._total_lines() - 1, self._screen.columns - 1)
        self.update()

    def selection_text(self) -> str:
        rng = self._ordered()
        if not rng or not self.has_selection():
            return ""
        (r1, c1), (r2, c2) = rng
        out = []
        for r in range(r1, r2 + 1):
            line = self._line_cells(r)
            lo = c1 if r == r1 else 0
            hi = c2 if r == r2 else self._screen.columns - 1
            out.append("".join((line.get(x).data if line.get(x) else " ")
                               for x in range(lo, hi + 1)).rstrip())
        while out and not out[-1]:
            out.pop()
        return "\n".join(out)

    def copy_selection(self) -> bool:
        txt = self.selection_text()
        if not txt:
            return False
        QGuiApplication.clipboard().setText(txt)
        return True

    def paste(self) -> None:
        self.send(QGuiApplication.clipboard().text().encode("utf-8"))

    def focusInEvent(self, e) -> None:                     # noqa: N802
        self.update()
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:                    # noqa: N802
        if self._preedit:
            self._preedit = ""
            self.update()
        super().focusOutEvent(e)

    def showEvent(self, e) -> None:                        # noqa: N802
        super().showEvent(e)
        self.setFocus(Qt.OtherFocusReason)                 # 切过来就能直接敲

    def mousePressEvent(self, e) -> None:                  # noqa: N802
        self.setFocus(Qt.MouseFocusReason)                 # 关键：点击即取焦点
        if e.button() == Qt.LeftButton:
            self._sel_a = self._cell_at(e.position())
            self._sel_b = self._sel_a
            self._selecting = True
            self.update()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:                   # noqa: N802
        if not self._selecting and (e.buttons() & Qt.LeftButton) and self._sel_a is not None:
            self._selecting = True
        if self._selecting:
            self._drag_pos = e.position()
            self._sel_b = self._cell_at(e.position())
            # 拖到上下边缘就开始持续滚屏：不用松手、先滚再选，一次拖到底
            y = e.position().y()
            if y < self._pad_y * 2 or y > self.height() - self._pad_y * 2:
                if not self._auto.isActive():
                    self._auto.start()
            elif self._auto.isActive():
                self._auto.stop()
            self.update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:                # noqa: N802
        if e.button() == Qt.LeftButton and self._selecting:
            self._selecting = False
            self._drag_pos = None
            self._auto.stop()
            if not self.has_selection():
                self.clear_selection()
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:            # noqa: N802
        """双击选中光标下的单词；点在空白处则选整行文字。"""
        if e.button() == Qt.LeftButton:
            row, col = self._cell_at(e.position())
            line = self._line_cells(row)
            text = "".join((line.get(x).data if line.get(x) else " ")
                           for x in range(self._screen.columns))
            lo = len(text) - len(text.lstrip())
            hi = len(text.rstrip()) - 1
            if hi < lo:                                    # 空行
                self._sel_a, self._sel_b = (row, 0), (row, 0)
            elif lo <= col <= hi and text[col] != " ":
                s = col
                while s > lo and text[s - 1] != " ":
                    s -= 1
                t2 = col
                while t2 < hi and text[t2 + 1] != " ":
                    t2 += 1
                self._sel_a, self._sel_b = (row, s), (row, t2)
            else:
                self._sel_a, self._sel_b = (row, lo), (row, hi)
            self.update()
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e) -> None:                 # noqa: N802
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        a_copy = m.addAction("复制")
        a_copy.setEnabled(self.has_selection())
        a_copy.triggered.connect(self.copy_selection)
        m.addAction("粘贴", self.paste)
        m.addSeparator()
        m.addAction("全选", self.select_all)
        m.addAction("回到底部", self.scroll_to_bottom)
        m.exec(e.globalPos())

    # ---------------- 绘制 ----------------
    def screen_text(self) -> str:
        """当前可见的这一屏文字（上翻回看时就是正在看的那屏，不含看不见的行）。"""
        return "\n".join(self._row_text(self._visible_row(y))
                         for y in range(self._screen.lines))

    def selected_text(self) -> str:
        """有选区返回选区，否则返回整屏（兼容旧调用）。"""
        return self.selection_text() or self.screen_text()

    # ---------------- 格子绘制（中文占 2 格，位置只由格号决定） ----------------
    @staticmethod
    def _cell_style(cell) -> tuple:
        """cell → (前景色, 背景色, 粗体)；认不出的颜色退回默认色而不是抛异常。"""
        try:
            bold = bool(getattr(cell, "bold", False))
            fg = color_of(cell.fg, DEFAULT_FG, bold)
            bg = color_of(cell.bg, DEFAULT_BG)
            if getattr(cell, "reverse", False):
                fg, bg = bg if bg != DEFAULT_BG else DEFAULT_FG, fg
            return fg, bg, bold
        except Exception:                        # noqa: BLE001
            return DEFAULT_FG, DEFAULT_BG, False

    def _paint_cells(self, p, pieces, start: int, y: int, fg, bg, bold: bool, fonts,
                     plain: bool = False) -> None:
        """画一段同色字符：`pieces` 是 [(文字, 占几格)]，起点是第 `start` 格。

        关键：位置只由 (start, 格数) 决定，绝不让字形的自然 advance 把后面的字带偏。
        pyte 里中文占 2 格（第二格是 data="" 的占位），而 Consolas 回退出来的中文字形
        advance 小于 2 格（12pt 实测 16px vs 17.59px）；早先整段 drawText 按自然排版画，
        一行中文下来会逐字左移，而光标/选区是按格算的 → 光标看着"偏右"，vi 里最明显。

        `plain=True` 表示整段都是单格 ASCII：宽度必然等于字数 × 格宽，可以省掉量字体的调用。
        """
        cw, chh = self._cell_w, self._cell_h
        x0 = self._pad_x + start * cw
        y0 = self._pad_y + y * chh
        total = sum(w for _, w in pieces)
        if bg is not None and bg != DEFAULT_BG:
            p.fillRect(QRectF(x0, y0, total * cw, chh), bg)
        font, fm = fonts[1 if bold else 0]
        if self._pen != fg:
            p.setPen(fg)
            self._pen = fg
        if self._font_used != font:
            p.setFont(font)
            self._font_used = font
        base = y0 + chh * 0.78
        offs = []
        o = 0
        for _txt, w in pieces:
            offs.append(o)
            o += w
        i = 0
        while i < len(pieces):
            w = pieces[i][1]
            j = i
            s = ""
            while j < len(pieces) and pieces[j][1] == w:
                s += pieces[j][0]
                j += 1
            # 连续同宽字符合成一段画：等宽 ASCII 的 advance 正好 1 格，中文/全角整段缩放到 2 格
            if plain:
                p.drawText(QPointF(x0 + offs[i] * cw, base), s)
            else:
                self._draw_fitted(p, fm, s, x0 + offs[i] * cw, base, (j - i) * w * cw)
            i = j

    def _draw_fitted(self, p, fm, text: str, x: float, base: float, want: float) -> None:
        """把 text 画进 [x, x+want)：字形 advance 与格宽对不上时横向缩放到正好占满。

        只做温和缩放（0.75~1.35）；差距太大说明不是等宽字体，退化成逐字锚格子，
        至少保证误差不跨字累计。
        """
        adv = fm.horizontalAdvance(text)
        if adv <= 0 or want <= 0 or abs(adv - want) <= 0.5:
            p.drawText(QPointF(x, base), text)
            return
        k = want / adv
        if 0.75 <= k <= 1.35:
            p.save()
            p.translate(x, 0.0)
            p.scale(k, 1.0)
            p.drawText(QPointF(0.0, base), text)
            p.restore()
            return
        k = 0
        for ch in text:
            p.drawText(QPointF(x + k * self._cell_w, base), ch)
            k += char_cells(ch)

    def paintEvent(self, e: QPaintEvent) -> None:        # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), DEFAULT_BG)
        p.setFont(self._font)
        cols, rows = self._screen.columns, self._screen.lines
        px, py = self._pad_x, self._pad_y
        font_plain = QFont(self._font)
        font_bold = QFont(self._font)
        font_bold.setBold(True)
        fonts = ((font_plain, QFontMetricsF(font_plain)), (font_bold, QFontMetricsF(font_bold)))
        visible = min(rows, int((self.height() - 2 * py) / self._cell_h) + 1)
        self._pen = self._font_used = None          # 同色同字体的段不用反复 setPen/setFont
        for y in range(max(0, visible)):
            line = self._visible_row(y)             # 按回看偏移取行：可能来自回滚缓冲
            x = 0
            while x < cols:
                cell = line.get(x)
                if cell is None or cell.data == "":
                    # None = 空位；"" = 宽字符（中文）占的第二格，字已在它左边那格画过
                    x += 1
                    continue
                # 颜色/粗体/反显：只在一段开头解析一次（color_of 有缓存）
                fg, bg, bold = self._cell_style(cell)
                raw = (cell.fg, cell.bg, bool(getattr(cell, "bold", False)),
                       bool(getattr(cell, "reverse", False)))
                start = x
                pieces = []
                plain = True                        # 整段都是单格 ASCII 时宽度必然 = 字数 × 格宽
                while x < cols:
                    c = line.get(x)
                    if c is None or c.data == "":
                        break
                    if (c.fg, c.bg, bool(getattr(c, "bold", False)),
                            bool(getattr(c, "reverse", False))) != raw:
                        break
                    # 宽字符的判据用 pyte 自己的排布：下一格是 data="" 的占位就是 2 格
                    nxt = line.get(x + 1)
                    w = 2 if (nxt is not None and nxt.data == "") else 1
                    if w == 2 or (not c.data.isascii()):
                        plain = False
                    pieces.append((c.data, w))
                    x += w
                self._paint_cells(p, pieces, start, y, fg, bg, bold, fonts, plain)
        p.setFont(self._font)

        # 选区高亮（绝对行号 → 屏幕行号，只有落在可见范围内才画）
        rng = self._ordered()
        if rng and self.has_selection():
            (r1, c1), (r2, c2) = rng
            top = self._top_abs()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(58, 110, 165, 210))
            for r in range(max(r1, top), min(r2, top + rows - 1) + 1):
                y = r - top
                lo = c1 if r == r1 else 0
                hi = c2 if r == r2 else cols - 1
                p.drawRect(QRectF(px + lo * self._cell_w, py + y * self._cell_h,
                                  (hi - lo + 1) * self._cell_w, self._cell_h))
            p.setBrush(Qt.NoBrush)

        # 回滚提示：让用户知道现在看的是历史，以及怎么回去
        if self._scroll:
            msg = "↑ 已回看 %d 行 · End/滚到条底回到底部" % self._scroll
            w = min(self.width() - 8, 8.5 * len(msg))
            box = QRectF(self.width() - w - 8, 6, w, self._cell_h + 4)
            p.fillRect(box, QColor(0, 0, 0, 180))
            p.setPen(QColor("#f9f1a5"))
            p.drawText(box, Qt.AlignCenter, msg)
        # 输入法未上屏的串（拼音/候选）就画在光标处
        if self._preedit:
            cx, cy = self._screen.cursor.x, self._screen.cursor.y
            pcs = [(ch, char_cells(ch)) for ch in self._preedit]
            adv = sum(w for _, w in pcs) * self._cell_w            # 别用 len()：中文占 2 格
            box = QRectF(px + cx * self._cell_w, py + cy * self._cell_h, adv, self._cell_h)
            p.fillRect(box, QColor("#3a3a3a"))
            self._paint_cells(p, pcs, cx, cy, QColor("#f9f1a5"), None, False, fonts)
            p.setPen(QColor("#f9f1a5"))
            p.drawLine(QPointF(box.left(), box.bottom() - 1), QPointF(box.right(), box.bottom() - 1))

        # 光标（上翻看历史时不画：光标属于当前屏，画在历史里只会让人以为跑到底了）
        if self._blink and not self._scroll and not getattr(self._screen.cursor, "hidden", False):
            cx, cy = self._screen.cursor.x, self._screen.cursor.y
            if not (0 <= cx < cols and 0 <= cy < rows):
                p.end()
                return
            span = 1
            cur_cell = self._screen.buffer.get(cy, {}).get(cx)
            if cur_cell is not None and char_cells(cur_cell.data) == 2:
                span = 2                       # vi 里光标压在中文字上时，块要盖住 2 格
            p.fillRect(QRectF(px + cx * self._cell_w, py + cy * self._cell_h,
                              span * self._cell_w, self._cell_h), CURSOR_BG)
        p.end()