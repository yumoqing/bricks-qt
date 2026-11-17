"""CI 冒烟：把指定目录下的 .ui 全部构建一遍（offscreen，不弹窗），有任何异常即失败。

用法：
    python tests/ci_smoke.py                     # 默认 examples/
    python tests/ci_smoke.py examples _bricks/examples
"""

from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bricks_qt import app as A          # noqa: E402


def main(argv) -> int:
    dirs = argv[1:] or ["examples"]
    files = []
    for d in dirs:
        files += sorted(glob.glob(os.path.join(d, "*.ui")))
    if not files:
        print("没有找到 .ui：%s" % ", ".join(dirs))
        return 1
    bad, warned = [], 0
    for f in files:
        try:
            r = A.inspect(f, [os.path.abspath(os.path.dirname(f) or "."),
                              os.path.abspath(".")])
            if not r["built"]:
                bad.append("%s: build 返回空" % f)
            warned += len(r["warnings"])
        except Exception as e:                     # noqa: BLE001
            bad.append("%s: %s" % (f, str(e)[:120]))
    print("检查 %d 个 .ui，失败 %d 个，未适配/告警 %d 条" % (len(files), len(bad), warned))
    for b in bad:
        print("  -", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
