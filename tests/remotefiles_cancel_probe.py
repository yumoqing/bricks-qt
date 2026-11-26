"""上传/下载按钮那条通道（_run_batch）的取消探针：点「取消」必须真停。

用法：QT_QPA_PLATFORM=offscreen ~/work/venvs/sshdesk-build/bin/python tests/remotefiles_cancel_probe.py
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer                                   # noqa: E402
from PySide6.QtWidgets import QApplication, QProgressDialog, QPushButton   # noqa: E402

from bricks_qt.remotefiles import RemoteFiles                       # noqa: E402


class Idle:
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
        pass

    def stat_size(self, p):
        return 0

    def upload(self, local, remote, cb=None, offset=0, stop=None):
        return 0

    def download(self, remote, local, cb=None, offset=0, stop=None):
        return 0


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    w = RemoteFiles()
    w.attach(Idle())
    w.resize(700, 400)
    w.show()
    w.cwd = "/"

    seen = {"stopped": False}

    def work(report, stop):
        st = {"files": 0, "sent": 0, "seconds": 0.0, "cancelled": False}
        t0 = time.time()
        for i in range(80):                      # 最长 4 秒，够点取消
            if stop.is_set():
                seen["stopped"] = True
                st["cancelled"] = True
                break
            time.sleep(0.05)
            report(i * 10, 800, "f%d.bin" % i)
        st["seconds"] = time.time() - t0
        return st

    def click_cancel():
        for d in w.findChildren(QProgressDialog):
            if d.isVisible():
                btn = d.findChild(QPushButton)
                if btn is not None:
                    btn.click()                  # 用户真点「取消」
                else:
                    d.cancel()

    QTimer.singleShot(600, click_cancel)
    started = time.time()
    w._run_batch("下载到 /tmp/x", work)
    while time.time() - started < 8:             # 进度框不自己关，等 tick 收尾
        app.processEvents()
        th = getattr(w, "_th", None)
        if th is not None and not th.is_running():
            for _ in range(10):
                app.processEvents()
                time.sleep(0.02)
            break
        time.sleep(0.02)
    elapsed = time.time() - started

    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    check(seen["stopped"], "「取消」真的让工作线程收手（stop 置位）")
    check(elapsed < 2.5, "取消后很快收尾（%.1fs，不是跑满 4s）" % elapsed)
    print("hint:", w.hint.text())
    check("已取消" in w.hint.text(), "提示行写了已取消")
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())