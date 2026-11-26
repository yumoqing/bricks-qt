"""RemoteFiles「删除」按钮离线探针：必须二次确认，只有点「确认删除」才真删。

用假 QMessageBox 记录点击的按钮（真代码里是 clickedButton() is yes 判定），
用假 session 记录 remove() 调用，不需要 SSH。
用法：QT_QPA_PLATFORM=offscreen ~/work/venvs/sshdesk-build/bin/python tests/remotefiles_remove_probe.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication                       # noqa: E402

import bricks_qt.remotefiles as rf                               # noqa: E402

ok = True


def check(cond, msg):
    global ok
    print(("  ok   " if cond else "  FAIL ") + msg)
    ok = ok and bool(cond)


class Btn:
    def __init__(self, text):
        self._t = text

    def text(self):
        return self._t


class FakeBox:
    """替代 QMessageBox：不弹真框，按 probe 预设的答案返回 clickedButton()。"""

    Question = 0
    YesRole = 0
    RejectRole = 1
    answer = "取消"
    last = None
    default_text = None

    def __init__(self, parent=None):
        self.buttons_ = []
        self.chosen = None
        FakeBox.last = self

    def setIcon(self, *_a):
        pass

    def setWindowTitle(self, *_a):
        pass

    def setText(self, t):
        self.text_ = t

    def addButton(self, text, _role):
        b = Btn(text)
        self.buttons_.append(b)
        return b

    def setDefaultButton(self, b):
        FakeBox.default_text = b.text()

    def exec(self):
        for b in self.buttons_:
            if b.text() == FakeBox.answer:
                self.chosen = b
        return 0

    def clickedButton(self):
        return self.chosen


class FakeSession:
    def __init__(self):
        self.removed = []

    def realpath(self, p):
        return p

    def listdir(self, p):
        return []

    def is_dir(self, p):
        return False

    def mkdir(self, p):
        pass

    def rmdir(self, p):
        pass

    def remove(self, p):
        self.removed.append(p)

    def stat_size(self, p):
        return 0

    def upload(self, local, remote, cb=None, offset=0, stop=None):
        return 0

    def download(self, remote, local, cb=None, offset=0, stop=None):
        return 0


def run(answer, expect_calls):
    sess = FakeSession()
    w = rf.RemoteFiles()
    w.attach(sess)
    w.selected = lambda: [("/remote/x.txt", False)]
    FakeBox.answer = answer
    FakeBox.default_text = None
    rf.QMessageBox = FakeBox
    w.remove()
    return sess, w


def main():
    QApplication.instance() or QApplication(sys.argv)
    real = rf.QMessageBox
    try:
        sess, w = run("取消", 0)
        check(sess.removed == [], "点「取消」：远端没删（remove 调用 %d 次）" % len(sess.removed))
        check(FakeBox.default_text == "取消", "默认按钮是「取消」（实际 %r）" % FakeBox.default_text)
        check("确认删除" in [b.text() for b in FakeBox.last.buttons_],
              "确认框里确有「确认删除」按钮")

        sess, w = run("确认删除", 1)
        check(sess.removed == ["/remote/x.txt"],
              "点「确认删除」：真的删了（%r）" % (sess.removed,))

        sess, w = run("X关闭", 0)      # 点窗口 X / Esc：clickedButton 为空
        check(sess.removed == [], "关闭框（Esc/X）：远端没删")
    finally:
        rf.QMessageBox = real

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())