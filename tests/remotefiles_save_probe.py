"""RemoteFiles 拖出收尾的离线探针：_save_prepared（已准备好 → 选择保存位置 → 带进度复制）。

不需要 SSH：用假 session + 临时目录，只验"复制整棵树、保留相对结构、进度框能收尾"。
用法：QT_QPA_PLATFORM=offscreen ~/work/venvs/sshdesk-build/bin/python tests/remotefiles_save_probe.py
"""

import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QFileDialog           # noqa: E402

from bricks_qt.remotefiles import RemoteFiles                    # noqa: E402


class FakeSession:
    """只够 attach() 用；探针不碰远端。"""

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

    def upload(self, local, remote, cb=None, **kw):
        return 0

    def download(self, remote, local, cb=None, **kw):
        return 0


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    root = tempfile.mkdtemp(prefix="rfprobe_")
    prepared = os.path.join(root, "prepared", "tree")
    os.makedirs(os.path.join(prepared, "sub", "deep"))
    os.makedirs(os.path.join(prepared, "empty"))
    for rel, data in (("a.txt", b"aaa"), ("sub/b.txt", b"bbbb"),
                      ("sub/deep/c.bin", b"c" * 5000)):
        with open(os.path.join(prepared, rel), "wb") as f:
            f.write(data)
    dst = os.path.join(root, "dest")
    os.makedirs(dst)

    w = RemoteFiles()
    w.attach(FakeSession())
    w.resize(700, 400)
    w.show()

    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: dst)   # 选文件夹
    w._save_prepared([prepared])

    t0 = time.time()
    while time.time() - t0 < 8:            # 等后台复制 + 进度框收尾
        app.processEvents()
        th = getattr(w, "_th", None)
        if th is not None and not th.is_running():
            for _ in range(20):
                app.processEvents()
                time.sleep(0.02)
            break
        time.sleep(0.02)

    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + msg)
        ok = ok and bool(cond)

    tree = os.path.join(dst, "tree")
    check(os.path.isfile(os.path.join(tree, "a.txt")), "顶层文件落地")
    check(os.path.isfile(os.path.join(tree, "sub", "b.txt")), "子目录结构保留")
    check(os.path.isfile(os.path.join(tree, "sub", "deep", "c.bin")), "深层文件落地")
    check(os.path.isdir(os.path.join(tree, "empty")), "空目录也建出来")
    with open(os.path.join(tree, "sub", "deep", "c.bin"), "rb") as f:
        check(len(f.read()) == 5000, "内容长度一致")
    print("hint:", w.hint.text())
    check("完成" in w.hint.text() or "已取消" in w.hint.text(), "进度框收尾写了结果")

    shutil.rmtree(root, ignore_errors=True)
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())