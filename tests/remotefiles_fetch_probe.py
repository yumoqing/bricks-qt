"""拖出准备（_fetch_to_temp）的离线探针：慢的时候要弹进度框、点取消要真的停。

假 session 的 download 故意慢（每 50ms 报一次进度，共 2s），所以必然超过 0.3s 的弹框阈值。
用法：QT_QPA_PLATFORM=offscreen ~/work/venvs/sshdesk-build/bin/python tests/remotefiles_fetch_probe.py
"""

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer                                    # noqa: E402
from PySide6.QtWidgets import QApplication, QProgressDialog, QPushButton    # noqa: E402

from bricks_qt.remotefiles import RemoteFiles                        # noqa: E402

SIZE = 4096


class SlowSession:
    """只有 stat_size / download 会被 _fetch_to_temp 用到。"""

    cancelled = False

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
        return SIZE

    def upload(self, local, remote, cb=None, offset=0, stop=None):
        return 0

    def download(self, remote, local, cb=None, offset=0, stop=None):
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        done = offset
        with open(local, "wb") as f:
            f.truncate(offset)
            while done < SIZE:
                if stop is not None and stop.is_set():
                    self.cancelled = True
                    return done
                time.sleep(0.05)               # 慢：2 秒才下完 4KB
                chunk = b"x" * 100
                f.write(chunk)
                done += len(chunk)
                if cb:
                    cb(done, SIZE)
        return done


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    sess = SlowSession()
    w = RemoteFiles()
    w.attach(sess)
    w.resize(700, 400)
    w.show()
    w.cwd = "/"
    # 探针必须自带干净状态：临时目录 + (mtime,size) 缓存跨进程残留，
    # 否则上一轮下完的 big.bin 会被当缓存命中，第一次就根本不下载（探针假绿/假红都出现过）。
    import shutil as _sh
    _sh.rmtree(os.path.join(tempfile.gettempdir(), "sshdesk_drag"), ignore_errors=True)
    w._drag_cache.clear()

    seen = {"visible": False}
    started = time.time()

    def hit_cancel():
        seen["visible"] = True
        seen["after"] = time.time() - started
        for d in w.findChildren(QProgressDialog):
            if d.isVisible():
                btn = d.findChild(QPushButton)
                if btn is not None:
                    btn.click()                 # 用户真的点「取消」这一步
                else:
                    d.cancel()

    QTimer.singleShot(700, hit_cancel)
    local = w._fetch_to_temp(["big.bin"], popup=True)
    elapsed = time.time() - started

    # 第二阶段：弹了框但传完（不点取消）—— 必须返回文件，不能因为"关框 emit canceled"
    # 被当成用户取消（这条以前会静默地把下好的东西丢掉）
    local2 = w._fetch_to_temp(["big.bin"], popup=True)

    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    check(seen["visible"], "慢的时候弹出了进度框（>=0.3s 才亮）")
    check(sess.cancelled, "取消真的传到了传输线程（stop 置位，线程自己收手）")
    check(local == [], "取消后不返回半截文件给 QDrag")
    check(elapsed < 8, "取消后立刻收尾（%.1fs）" % elapsed)
    check(len(local2) == 1 and os.path.isfile(local2[0]), "传完不点取消 → 正常交出本地文件")
    print("hint:", w.hint.text())
    print("OK" if ok else "FAILED")
    tempfile.gettempdir()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())