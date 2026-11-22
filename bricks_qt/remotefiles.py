"""RemoteFiles：bricks 的原生远端文件面板（单栏 + 拖拽）。

对应 bricks 里 `ResourceBrowser` 的桌面版：

- 只显示远端目录；`..` 一行回上级，双击目录进入
- 从资源管理器拖文件/目录进来 = 上传（目录递归、保留相对路径）
- 把远端文件拖到资源管理器 = 先下到临时目录再交出 QDrag
- 工具栏 上传… / 下载到… / 新建 / 刷新 / 删除 与拖拽等价
- **大文件**：进度是整批累计的（+ 速率/剩余时间），可取消；**断点续传**——目标比源短就从断点续，
  一样长且不比源旧就跳过（拖出下载用临时目录里的半截文件续上）

宿主用法：

    w = RemoteFiles()            # .ui 里 widgettype=RemoteFiles
    w.attach(session)            # session 需提供 realpath/listdir/is_dir/mkdir/rmdir/remove/upload/download/stat_size
                                 # 可选：stat_info(path)->(size, mtime) 更准的断点判断；
                                 #       upload/download 支持 offset=/stop= 才有续传与协作取消
"""

from __future__ import annotations

import inspect
import os
import posixpath
import tempfile
import threading
import time
import traceback
from typing import List, Optional, Tuple

from PySide6.QtCore import QMimeData, QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog,
                               QFileIconProvider, QHBoxLayout,
                               QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QProgressDialog, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%d %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0
    return str(n)


def human_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def human_duration(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return "%d 秒" % sec
    if sec < 3600:
        return "%d 分 %02d 秒" % (sec // 60, sec % 60)
    return "%d 时 %02d 分" % (sec // 3600, (sec % 3600) // 60)


def rate_text(done: int, total: int, t0: float) -> str:
    """进度副标题：百分比 · 速率 · 预计剩余（大文件才有意义）。"""
    elapsed = max(0.2, time.time() - t0)
    rate = done / elapsed
    out = []
    if total:
        out.append("%.1f%%" % min(100.0, done * 100.0 / total))
    out.append("%s/s" % human_size(int(rate)))
    if total and 0 < done < total and rate > 1024:
        out.append("剩余 %s" % human_duration((total - done) / rate))
    return " · ".join(out)


def accepts_kwargs(fn, *names: str) -> bool:
    """宿主 session 是否支持可续传签名（老宿主没有 offset/stop 就退回整传）。"""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return True
    return all(n in params for n in names)


class RemoteFS:
    """远端目录：走 SSHSession（appPublic.sshx / asyncssh）的同步接口。"""

    label = "远端"

    def __init__(self, session):
        self.session = session

    def home(self) -> str:
        return self.session.realpath(".")

    def listdir(self, path: str):
        return self.session.listdir(path)

    def is_dir(self, path: str) -> bool:
        return self.session.is_dir(path)

    def join(self, *parts: str) -> str:
        return posixpath.join(*parts)

    def parent(self, path: str) -> str:
        p = posixpath.dirname(path.rstrip("/"))
        return p or "/"

    def mkdir(self, path: str) -> None:
        self.session.mkdir(path)

    def remove(self, path: str) -> None:
        if self.is_dir(path):
            self.session.rmdir(path)
        else:
            self.session.remove(path)


class _Transfer:
    """一批文件的上传/下载。工作是普通线程，GUI 侧**轮询**共享状态，不用跨线程信号。

    为什么不用 QThread + Signal：进度产生于 session 的 asyncio 线程，跨线程 emit 会排队到
    GUI 线程；进度框/面板销毁后，积压的调用会打到已删的 C++ 对象上，PySide 偶尔直接段错误
    ——症状是「传完文件再开一个会话就崩」，还挑时机复现。轮询一个 dict 没有接收方对象要活着，
    也没有排队事件，这一整类崩溃消失。

    - `stop`：协作式取消，传输层逐块检查。**不要 terminate 线程**：会打断正在写的 SFTP
      句柄让连接不可用；协作式取消还会把已写下去的部分留着给下次续传。
    """

    def __init__(self, fn):
        self.fn = fn
        self.stop = threading.Event()
        self.t0 = time.time()
        self.state = {"done": 0, "total": 0, "name": "", "running": True,
                      "error": "", "result": None}
        self._th = threading.Thread(target=self._main, daemon=True)

    # ---- GUI 侧 ----
    def start(self) -> None:
        self._th.start()

    def is_running(self) -> bool:
        return self._th.is_alive()

    def cancel(self) -> None:
        self.stop.set()

    def wait(self, timeout: Optional[float] = None) -> None:
        self._th.join(timeout)

    @property
    def result(self) -> dict:
        return self.state.get("result") or {}

    # ---- 工作线程 / 给传输层的回调 ----
    def report(self, done: int, total: int, name: str = "") -> None:
        st = self.state
        st["done"], st["total"], st["name"] = int(done), int(total), name or ""

    def _main(self) -> None:
        try:
            self.state["result"] = self.fn(self.report, self.stop) or {}
        except Exception as e:                  # noqa: BLE001
            if self.stop.is_set():
                # 取消时传输层抛 TransferCancelled 是正常路径（半截文件留着续传），
                # 不能当失败弹框，但要在界面上说明是用户取消的
                self.state["cancelled"] = True
                self.state["result"] = {"cancelled": True}
            else:
                self.state["error"] = str(e) or e.__class__.__name__
        finally:
            self.state["running"] = False


class RemoteTable(QTableWidget):
    """一条目录列表：支持拖入上传、拖出下载、双击进入。"""

    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.setHorizontalHeaderLabels(["名称", "大小", "修改时间"])
        self.setShowGrid(False)
        self.verticalHeader().setDefaultSectionSize(24)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

        # 由 FileBrowser 注入：收拖入的本地路径 / 把远端文件落到临时文件返回本地路径
        self.collect_drop_paths = None
        self.fetch_to_temp = None

    # ---- 拖入：从资源管理器拖文件 → 上传 ----
    def dragEnterEvent(self, e) -> None:                # noqa: N802
        if e.mimeData().hasUrls():
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:                 # noqa: N802
        if e.mimeData().hasUrls():
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e) -> None:                     # noqa: N802
        if not e.mimeData().hasUrls():
            super().dropEvent(e)
            return
        paths = []
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p and os.path.exists(p):
                paths.append(p)
        e.setDropAction(Qt.CopyAction)
        e.accept()
        if paths and self.collect_drop_paths:
            try:
                self.collect_drop_paths(paths)
            except Exception as err:         # noqa: BLE001
                # 拖拽事件是 Qt/系统(OLE)派发进来的：异常漏出去会让 Windows 的拖放状态机会卡在半截，
                # 表现成"拖一下就没了反应"。这里兜住，只报错不炸事件链。
                traceback.print_exc()
                try:
                    self.hint.setText("拖入处理失败：%s" % str(err)[:120])
                except Exception:            # noqa: BLE001
                    pass

    # ---- 拖出：拖到资源管理器 → 下载 ----
    def startDrag(self, actions) -> None:               # noqa: N802
        if not self.fetch_to_temp:
            return
        rows = sorted({i.row() for i in self.selectedIndexes()})
        names = []
        for r in rows:
            it = self.item(r, 0)
            data = it.data(Qt.UserRole) if it else None
            if data and not data[1] and not data[2]:    # 只拖文件，目录和 .. 不拖
                names.append(data[0])
        if not names:
            return
        local_files = [p for p in self.fetch_to_temp(names) if p]
        if not local_files:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in local_files])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class RemoteFiles(QWidget):
    """远端文件面板（单栏）。"""

    def __init__(self, session=None, parent=None):
        super().__init__(parent)
        self.session = session
        self.fs = RemoteFS(session) if session is not None else None
        self.cwd = self.fs.home() if self.fs is not None else ""
        self._th: Optional[_Transfer] = None
        self._resumable = False                 # 宿主 session 支持 offset/stop 才能续传/取消
        self._refresh_caps()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        bar = QHBoxLayout()
        bar.setSpacing(4)
        tag = QLabel("远端文件")
        tag.setStyleSheet("color:#444; font-weight:600")
        tag.setFixedWidth(60)
        bar.addWidget(tag)
        self.path_edit = QLineEdit(self.cwd)
        self.path_edit.setMinimumWidth(120)
        self.path_edit.setToolTip("%s\n输入路径回车跳转" % self.cwd)
        self.path_edit.returnPressed.connect(lambda: self.chdir(self.path_edit.text().strip()))
        bar.addWidget(self.path_edit, 1)
        lay.addLayout(bar)

        ops = QHBoxLayout()
        ops.setSpacing(4)
        ops.setContentsMargins(60, 0, 0, 0)
        self.btn_upload = QPushButton("上传…")
        self.btn_upload.setToolTip("选择本地文件上传到当前目录（也可以直接从资源管理器拖进来）\n"
                                   "中断后再传会从断点续传，远端已完整的文件自动跳过")
        self.btn_upload.clicked.connect(self.upload)
        self.btn_download = QPushButton("下载到…")
        self.btn_download.setToolTip("把选中的远端文件下载到本地目录（也可以直接拖到资源管理器）\n"
                                     "本地已有部分会自动续传，已完整的文件跳过")
        self.btn_download.setEnabled(False)
        self.btn_download.clicked.connect(self.download)
        self.btn_mkdir = QPushButton("新建")
        self.btn_mkdir.setToolTip("在当前目录新建文件夹")
        self.btn_mkdir.clicked.connect(self.mkdir)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setToolTip("重新读取目录（F5）")
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_remove = QPushButton("删除")
        self.btn_remove.setToolTip("删除选中项")
        self.btn_remove.setEnabled(False)
        self.btn_remove.clicked.connect(self.remove)
        for b in (self.btn_upload, self.btn_download, self.btn_mkdir,
                  self.btn_refresh, self.btn_remove):
            ops.addWidget(b)
        ops.addStretch(1)
        self.hint = QLabel("拖文件进来上传 · 拖出去下载 · 双击目录进入")
        self.hint.setStyleSheet("color:#767676")
        ops.addWidget(self.hint)
        lay.addLayout(ops)

        self.table = RemoteTable(self)
        self.table.doubleClicked.connect(self._on_double)
        self.table.itemSelectionChanged.connect(self._on_sel)
        self.table.collect_drop_paths = self.upload_paths
        self.table.fetch_to_temp = self._fetch_to_temp
        lay.addWidget(self.table, 1)

        if self.fs is not None:
            self.reload()
        else:
            self._set_disabled(True)

    def sizeHint(self):                          # noqa: N802
        return QSize(720, 420)

    def minimumSizeHint(self):                   # noqa: N802
        return QSize(360, 200)

    # ---------------- 宿主挂载 ----------------
    def attach(self, session) -> None:
        """宿主连上以后把 SSH 会话挂进来。"""
        self.session = session
        self.fs = RemoteFS(session)
        self.cwd = self.fs.home()
        self.path_edit.setText(self.cwd)
        self._refresh_caps()
        self._set_disabled(False)
        self.reload()

    def _refresh_caps(self) -> None:
        sess = self.session
        self._resumable = bool(sess is not None and
                               accepts_kwargs(sess.upload, "offset", "stop") and
                               accepts_kwargs(sess.download, "offset", "stop"))

    def _set_disabled(self, on: bool) -> None:
        for b in (self.btn_upload, self.btn_download, self.btn_mkdir, self.btn_refresh, self.btn_remove):
            b.setEnabled(not on)
        self.table.setEnabled(not on)
        self.hint.setText("未连接" if on else "拖文件进来上传 · 拖出去下载 · 双击目录进入")

    # ---------------- 目录 ----------------
    def chdir(self, path: str, quiet: bool = False) -> bool:
        path = (path or self.cwd).strip()
        if not path:
            return False
        try:
            ok = self.fs.is_dir(path)
        except Exception as e:                  # noqa: BLE001
            if not quiet:
                QMessageBox.warning(self, "打不开", "%s\n%s" % (path, e))
            return False
        if not ok:
            if not quiet:
                QMessageBox.warning(self, "打不开", "%s 不是目录" % path)
            return False
        self.cwd = path
        self.path_edit.setText(path)
        self.path_edit.setToolTip("%s\n输入路径回车跳转" % path)
        self.reload()
        return True

    def reload(self) -> None:
        self.table.setRowCount(0)
        try:
            rows = self.fs.listdir(self.cwd)
        except Exception as e:                  # noqa: BLE001
            self.path_edit.setText("%s   ⚠ %s" % (self.cwd, str(e)[:60]))
            self.hint.setText("列目录失败：%s" % str(e)[:120])
            return
        ic = QFileIconProvider()
        dir_icon = ic.icon(QFileIconProvider.Folder)
        file_icon = ic.icon(QFileIconProvider.File)

        parent = self.fs.parent(self.cwd)
        up_row = 1 if parent != self.cwd else 0
        self.table.setRowCount(len(rows) + up_row)
        if up_row:
            it = QTableWidgetItem("..")
            it.setIcon(dir_icon)
            it.setData(Qt.UserRole, ("..", True, True))     # (name, is_dir, is_up)
            self.table.setItem(0, 0, it)
            self.table.setItem(0, 1, QTableWidgetItem(""))
            self.table.setItem(0, 2, QTableWidgetItem(""))
        for i, (name, size, mtime, is_dir) in enumerate(rows, start=up_row):
            item = QTableWidgetItem(name)
            item.setIcon(dir_icon if is_dir else file_icon)
            item.setData(Qt.UserRole, (name, is_dir, False))
            self.table.setItem(i, 0, item)
            self.table.setItem(i, 1, QTableWidgetItem("" if is_dir else human_size(size)))
            self.table.setItem(i, 2, QTableWidgetItem(human_time(mtime)))

    def _on_double(self, idx) -> None:
        it = self.table.item(idx.row(), 0)
        data = it.data(Qt.UserRole) if it else None
        if not data:
            return
        name, is_dir, is_up = data
        if is_up:
            self.chdir(self.fs.parent(self.cwd))
        elif is_dir:
            self.chdir(self.fs.join(self.cwd, name))
        else:
            self.hint.setText("已选：%s" % self.fs.join(self.cwd, name))

    def _on_sel(self, *_a) -> None:
        files = [d for _p, d in self.selected() if not d]
        self.btn_remove.setEnabled(bool(self.table.selectedIndexes()))
        self.btn_download.setEnabled(bool(files))
        self.hint.setText("" if files else "拖文件进来上传 · 拖出去下载 · 双击目录进入")

    def selected(self) -> List[Tuple[str, bool]]:
        out = []
        for idx in self.table.selectionModel().selectedRows():
            it = self.table.item(idx.row(), 0)
            data = it.data(Qt.UserRole) if it else None
            if not data or data[2]:             # 跳过 ..
                continue
            out.append((self.fs.join(self.cwd, data[0]), data[1]))
        return out

    def mkdir(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建目录", "目录名：")
        if not (ok and name.strip()):
            return
        try:
            self.fs.mkdir(self.fs.join(self.cwd, name.strip()))
            self.reload()
        except Exception as e:                  # noqa: BLE001
            QMessageBox.warning(self, "失败", str(e))

    def remove(self) -> None:
        items = self.selected()
        if not items:
            return
        if QMessageBox.question(self, "确认", "删除选中 %d 项？" % len(items)) != QMessageBox.Yes:
            return
        for path, _isdir in items:
            try:
                self.fs.remove(path)
            except Exception as e:              # noqa: BLE001
                QMessageBox.warning(self, "删除失败", "%s\n%s" % (path, e))
        self.reload()

    # ---------------- 传输 ----------------
    # 三条不变量（大文件才暴露）：
    #  1) 进度按整批累计（+ 速率/剩余），不是"每个文件从 0 走到 100"
    #  2) 取消是协作式的：只置 stop，传输层逐块看，已写下去的部分留着续传
    #  3) 拖出下载不能阻塞 GUI：早先 GUI 线程 msleep 死等，整个窗口假死、取消都点不动
    def _plan(self, total: int, have: Optional[Tuple[int, float]],
              local_mtime: float = 0.0) -> Tuple[int, bool]:
        """断点决策 → (offset, skip)。

        - 目标不存在 / 比源长 / 比源旧：offset=0 整传
        - 目标更短：offset=目标长度，从断点续传
        - 目标一样长且不比本地旧：整份跳过（`local_mtime` 传 0 表示只比大小）
        """
        if have is None:
            return 0, False
        size, mtime = have
        if size == total and (not local_mtime or mtime + 2 >= local_mtime):
            return 0, True
        if 0 < size < total:
            return size, False
        return 0, False

    def _probe(self, remote: str) -> Optional[Tuple[int, float]]:
        """远端文件的 (size, mtime)；不存在返回 None。"""
        try:
            if hasattr(self.session, "stat_info"):
                info = self.session.stat_info(remote)
                return int(info[0]), float(info[1])
            return int(self.session.stat_size(remote)), 0.0
        except Exception:                       # noqa: BLE001
            return None

    def _run_batch(self, title: str, work) -> None:
        """跑一批传输：`work(report, stop)` 在工作线程里执行，返回汇总 dict。

        进度用 QTimer 每 80ms 读一次 `th.state`（没有跨线程信号，见 _Transfer 说明）。
        定时器挂在进度框名下：框被销毁 → 定时器跟着销毁 → 回调不可能打到已删控件上。
        """
        dlg = QProgressDialog(title, "取消", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setLabelText(title)
        th = _Transfer(work)
        self._th = th
        timer = QTimer(dlg)
        timer.setInterval(80)

        def tick() -> None:
            st = th.state
            if st["running"]:
                total = st["total"] or st["done"]
                dlg.setValue(min(100, int(st["done"] * 100 / total)) if total else 0)
                dlg.setLabelText("%s\n%s / %s · %s%s" % (
                    title, human_size(st["done"]), human_size(total),
                    rate_text(st["done"], total, th.t0),
                    ("\n当前：%s" % st["name"]) if st["name"] else ""))
                return
            timer.stop()
            dlg.close()
            dlg.deleteLater()       # 一批一个进度框，别攒着（攒多了销毁顺序会咬人）
            self._after(th)

        timer.timeout.connect(tick)
        dlg.canceled.connect(lambda: th.is_running() and th.cancel())   # 绝不 terminate 线程
        th.start()
        timer.start()

    def _after(self, th: _Transfer) -> None:
        self.reload()
        err = th.state.get("error")
        if err:
            self.hint.setText("传输失败：%s" % str(err)[:120])
            QMessageBox.warning(self, "传输失败", str(err))
            return
        self.hint.setText(self.summary(th.result))

    @staticmethod
    def summary(st: dict) -> str:
        if st.get("cancelled"):
            return "已取消：已传的部分保留，下次同一个目标会自动续传"
        text = "%d 个文件 · %s · 用时 %s" % (
            st.get("files", 0), human_size(st.get("sent", 0)),
            human_duration(st.get("seconds", 0)))
        if st.get("resumed"):
            text += " · 续传 %d" % st["resumed"]
        if st.get("skipped"):
            text += " · 跳过 %d（已完整）" % st["skipped"]
        return text

    def upload(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "上传到 %s" % self.cwd)
        if files:
            self.upload_paths(files)

    def upload_paths(self, paths: List[str]) -> None:
        """把本地路径（文件/目录）上传到当前远端目录：断点续传、已完整的跳过。"""
        files: List[str] = []
        skipped: List[str] = []

        def _walk_err(err: OSError) -> None:
            # Windows 上拖进来的目录里常有没权限的（系统目录、junction），os.walk 默认会把
            # 异常抛到调用者 = 抛进 Qt 的拖放事件里，整次拖入作废。这里只记下来、跳过。
            skipped.append(getattr(err, "filename", "") or str(err))

        for p in paths:
            if os.path.isdir(p):
                for root, _dirs, fs in os.walk(p, onerror=_walk_err):
                    for f in fs:
                        files.append(os.path.join(root, f))
            elif os.path.isfile(p):
                files.append(p)
        if not files:
            self.hint.setText("没有可上传的文件（空目录或没有读取权限）")
            return
        if skipped:
            self.hint.setText("跳过 %d 个无法读取的位置" % len(skipped))
        target = self.cwd

        def rel_name(local: str) -> str:
            for src in paths:
                if os.path.isdir(src) and local.startswith(src + os.sep):
                    return posixpath.join(os.path.basename(src),
                                          os.path.relpath(local, src).replace(os.sep, "/"))
            return os.path.basename(local)

        jobs = [(local, posixpath.join(target, rel_name(local))) for local in files]

        def work(report, stop) -> dict:
            # 先探一遍断点，才知道整批要传多少（进度条要的是总量）
            plan = []
            for local, remote in jobs:
                total = os.path.getsize(local)
                off, skip = self._plan(total, self._probe(remote), os.path.getmtime(local))
                plan.append((local, remote, off, total, skip))
            grand = sum(0 if k else t - o for _l, _r, o, t, k in plan)
            report(0, grand, "准备上传 %d 个文件" % len(plan))
            st = {"files": 0, "skipped": 0, "resumed": 0, "sent": 0, "cancelled": False}
            t0 = time.time()
            base = 0
            made = set()
            for local, remote, off, total, skip in plan:
                if skip:
                    st["skipped"] += 1
                    continue
                if stop.is_set():
                    st["cancelled"] = True
                    break
                d = posixpath.dirname(remote)
                # 目录可能多层（拖进来的整棵树）：从 target 往下逐级建，父目录先建，
                # 免得 os.walk 顺序一变就"父目录不存在 → 整棵树传失败"。
                chain = []
                # 必须带 `d != target` 退出条件：目标就是 "/" 时 dirname("/") == "/"，
                # 只靠 startswith(target) 会永远成立 → 死循环（上传一个根目录下的文件就挂住，
                # 界面表现为点/拖之后一直"准备上传"，直到内存耗尽）。
                while d and d != target and d not in made and d.startswith(target):
                    chain.append(d)
                    d = posixpath.dirname(d)
                for d in reversed(chain):
                    try:
                        self.session.mkdir(d)
                    except Exception:           # noqa: BLE001
                        pass
                    made.add(d)
                name = posixpath.basename(remote)

                def cb(done: int, _tot: int, _base=base, _name=name) -> None:
                    report(_base + done, grand, _name)

                if self._resumable:
                    sent = self.session.upload(local, remote, cb, offset=off, stop=stop)
                else:
                    sent = self.session.upload(local, remote, cb)
                st["files"] += 1
                st["sent"] += int(sent or 0)
                if off:
                    st["resumed"] += 1
                base += total - off
            st["seconds"] = time.time() - t0
            return st

        self.hint.setText("上传 %d 个文件到 %s" % (len(jobs), target))
        self._run_batch("上传到 %s" % target, work)

    def download(self) -> None:
        items = [p for p, d in self.selected() if not d]
        if not items:
            return
        target = QFileDialog.getExistingDirectory(self, "下载到", os.path.expanduser("~"))
        if not target:
            return

        def work(report, stop) -> dict:
            plan = []
            for remote in items:
                local = os.path.join(target, posixpath.basename(remote))
                total = int(self.session.stat_size(remote))
                have = (os.path.getsize(local), os.path.getmtime(local)) \
                    if os.path.isfile(local) else None
                off, skip = self._plan(total, have)
                plan.append((remote, local, off, total, skip))
            grand = sum(0 if k else t - o for _r, _l, o, t, k in plan)
            report(0, grand, "准备下载 %d 个文件" % len(plan))
            st = {"files": 0, "skipped": 0, "resumed": 0, "sent": 0, "cancelled": False}
            t0 = time.time()
            base = 0
            for remote, local, off, total, skip in plan:
                if skip:
                    st["skipped"] += 1
                    continue
                if stop.is_set():
                    st["cancelled"] = True
                    break
                name = posixpath.basename(remote)

                def cb(done: int, _tot: int, _base=base, _name=name) -> None:
                    report(_base + done, grand, _name)

                if self._resumable:
                    sent = self.session.download(remote, local, cb, offset=off, stop=stop)
                else:
                    sent = self.session.download(remote, local, cb)
                st["files"] += 1
                st["sent"] += int(sent or 0)
                if off:
                    st["resumed"] += 1
                base += total - off
            st["seconds"] = time.time() - t0
            return st

        self._run_batch("下载到 %s" % target, work)

    def _fetch_to_temp(self, names: List[str]) -> List[str]:
        """拖出前先把远端文件下到临时目录，返回本地路径（失败/取消返回空列表）。

        拖放必须在 startDrag 返回前拿到数据，所以这里同步等后台线程——但**用嵌套事件循环等**，
        不是 GUI 线程 `msleep` 死等（那样拖个大文件整个窗口假死，取消也点不动）。
        临时目录里的半截文件，下次拖同一个文件时按断点续传续上。
        """
        tmpdir = os.path.join(tempfile.gettempdir(), "sshdesk_drag")
        os.makedirs(tmpdir, exist_ok=True)
        out: List[str] = []
        errors: List[str] = []
        jobs = [(posixpath.join(self.cwd, n), os.path.join(tmpdir, posixpath.basename(n)))
                for n in names]

        def work(report, stop) -> dict:
            plan = []
            for remote, local in jobs:
                total = int(self.session.stat_size(remote))
                have = (os.path.getsize(local), os.path.getmtime(local)) \
                    if os.path.isfile(local) else None
                off, skip = self._plan(total, have)
                plan.append((remote, local, off, total, skip))
            grand = sum(0 if k else t - o for _r, _l, o, t, k in plan)
            report(0, grand, "准备拖动 %d 个文件" % len(plan))
            sent = 0
            base = 0
            for remote, local, off, total, skip in plan:
                if skip:
                    out.append(local)           # 临时目录里已有完整副本，直接拖它
                    continue
                if stop.is_set():
                    break

                def cb(done: int, _tot: int, _base=base) -> None:
                    report(_base + done, grand, os.path.basename(remote))

                try:
                    if self._resumable:
                        sent += int(self.session.download(remote, local, cb,
                                                          offset=off, stop=stop) or 0)
                    else:
                        sent += int(self.session.download(remote, local, cb) or 0)
                    out.append(local)
                except Exception as e:          # noqa: BLE001
                    if stop.is_set():           # 用户取消，不算失败
                        break
                    errors.append(str(e))
                    break
                base += total - off
            return {"files": len(out), "sent": sent, "cancelled": stop.is_set()}

        th = _Transfer(work)
        dlg = QProgressDialog("准备拖动（先下载到临时目录）", "取消", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(200)
        dlg.canceled.connect(lambda: th.is_running() and th.cancel())
        th.start()
        app = QApplication.instance()
        while th.is_running():                  # 事件照转（进度能画、取消能点），但不是死等
            if app is not None:
                app.processEvents()
            th.wait(0.02)
        dlg.close()
        dlg.deleteLater()
        if th.stop.is_set():
            return []                           # 半截文件留在临时目录，下次续上
        if errors:
            QMessageBox.warning(self, "下载失败", errors[0])
            return []
        return out
