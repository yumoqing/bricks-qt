"""纯原生后端：Markdown（QTextBrowser）、代码（QPlainTextEdit）、媒体（QtMultimedia）。"""

from __future__ import annotations

import re

from .base import Provider, register
from .common import read_text


def md_text(node, ctx):
    """markdown → QTextBrowser，不启 Chromium，打开快。"""
    import markdown as md_mod
    from PySide6.QtWidgets import QTextBrowser

    src = node.opt("md_url", "url", "src", "path", default="")
    text = read_text(ctx, src) or str(node.opt("md", "text", "content", default=""))
    if not text:
        return None
    html = md_mod.markdown(text, extensions=["tables", "fenced_code", "toc"])
    css = ("body{font-family:'Microsoft YaHei UI';font-size:14px;line-height:1.7;color:#222}"
           "code{background:#f4f4f4;padding:1px 4px}pre{background:#f6f8fa;padding:10px}"
           "table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:4px 8px}")
    br = QTextBrowser()
    br.setOpenExternalLinks(True)
    br.document().setDefaultStyleSheet(css)
    br.setHtml(html)
    return br


_KEYWORDS = (
    "def|class|return|if|else|elif|for|while|import|from|as|try|except|finally|with|"
    "lambda|pass|break|continue|global|nonlocal|assert|yield|raise|del|in|is|not|and|or|"
    "None|True|False|self|async|await"
)


class _Highlighter:
    def __new__(cls, document):
        from PySide6.QtCore import QRegularExpression
        from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

        class _H(QSyntaxHighlighter):
            def __init__(self, doc):
                super().__init__(doc)
                kw = QTextCharFormat()
                kw.setForeground(QColor("#0033b3"))
                kw.setFontWeight(QFont.Bold)
                self.kw = QRegularExpression(r"\b(%s)\b" % _KEYWORDS)
                self.fmt_kw = kw
                st = QTextCharFormat()
                st.setForeground(QColor("#067d17"))
                self.fmt_str = st
                self.str = QRegularExpression(r"'[^']*'|\"[^\"]*\"")
                cm = QTextCharFormat()
                cm.setForeground(QColor("#8c8c8c"))
                self.fmt_cm = cm
                self.cm = QRegularExpression(r"#[^\n]*")

            def highlightBlock(self, text):
                for rx, fmt in ((self.kw, self.fmt_kw), (self.str, self.fmt_str), (self.cm, self.fmt_cm)):
                    it = rx.globalMatch(text)
                    while it.hasNext():
                        m = it.next()
                        self.setFormat(m.capturedStart(), m.capturedLength(), fmt)

        h = _H(document)
        return h


def code_native(node, ctx):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QPlainTextEdit

    src = node.opt("url", "src", "path", default="")
    code = read_text(ctx, src) or str(node.opt("text", "value", "code", default=""))
    if not code:
        return None
    ed = QPlainTextEdit(code)
    ed.setFont(QFont("Consolas", 10))
    ed.setReadOnly(bool(node.opt("readonly", default=False)))
    ed.setLineWrapMode(QPlainTextEdit.NoWrap)
    lang = str(node.opt("mode", "language", default="python")).lower()
    if lang.startswith("py"):
        ed._hl = _Highlighter(ed.document())      # 保持引用，别被 GC
    return ed


def media_native(node, ctx):
    """Video / VideoPlayer / AudioRecorder → QtMultimedia（本地/HTTP 都行）。"""
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

    url = str(node.opt("url", "src", "path", default=""))
    if not url:
        return None
    box = QWidget()
    lay = QHBoxLayout(box) if node.widgettype in ("AudioRecorder",) else None
    player = QMediaPlayer(box)
    audio = QAudioOutput(box)
    player.setAudioOutput(audio)

    if node.widgettype in ("Video", "VideoPlayer"):
        from PySide6.QtWidgets import QVBoxLayout
        vw = QVideoWidget(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(vw, 1)
        player.setVideoOutput(vw)
        bar = QWidget(box)
        hb = QHBoxLayout(bar)
        hb.setContentsMargins(0, 0, 0, 0)
        play = QPushButton("播放/暂停")
        play.clicked.connect(lambda: player.pause() if player.playbackState() == QMediaPlayer.PlayingState
                             else player.play())
        hb.addWidget(play)
        hb.addStretch(1)
        lay.addWidget(bar, 0)
    else:
        from PySide6.QtWidgets import QLabel, QVBoxLayout
        lay = QVBoxLayout(box)
        lay.addWidget(QLabel("音频：%s" % url))
        play = QPushButton("播放/暂停")
        play.clicked.connect(lambda: player.pause() if player.playbackState() == QMediaPlayer.PlayingState
                             else player.play())
        lay.addWidget(play)

    player.setSource(QUrl(url) if "://" in url else QUrl.fromLocalFile(ctx.asset(url) or url))
    if node.opt("autoplay"):
        player.play()
    return box


def html_native(node, ctx):
    """没有 WebEngine 时的降级：QTextBrowser 只认简单 HTML/CSS。"""
    from PySide6.QtWidgets import QTextBrowser

    body = str(node.opt("html", "content", "text", default=""))
    if not body:
        return None
    tb = QTextBrowser()
    tb.setHtml(body)
    return tb


register(Provider("native-markdown", "markdown", 60, md_text, "markdown", "markdown",
                  note="QTextBrowser，秒开，不启 Chromium"))
register(Provider("native-media", "media", 60, media_native, "PySide6.QtMultimedia",
                  note="QtMultimedia，本地/HTTP 播放"))
register(Provider("native-code", "code", 40, code_native, "PySide6.QtWidgets",
                  note="QPlainTextEdit + 高亮，编辑可用"))
register(Provider("native-html", "html", 30, html_native, "PySide6.QtWidgets",
                  note="降级：只支持简单 HTML"))