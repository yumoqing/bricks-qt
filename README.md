# bricks-qt — bricks 的 Qt6 原生渲染器（P0 原型）

一句话：**同一份 `.ui` JSON，浏览器里是 bricks，桌面上是 Qt 原生控件**。这是 Windows/Linux 的 adapter，
和 swiftbricks（macOS/iOS）、bricks_compose（Android/Kotlin）并列；协议层共用，只有渲染层不同。

## 快速看效果

```bat
bricks-qt show  D:\work\repos\bricks-qt\examples\demo_app.ui          :: 真窗口
bricks-qt shot  D:\work\repos\bricks-qt\examples\demo_app.ui --out out\demo.png --size 1100x760
bricks-qt shot  D:\work\repos\bricks\examples\form.ui --out out\form.png --asset D:\work\repos\bricks\dist
bricks-qt check D:\work\repos\bricks\examples\datagrid.ui --json
bricks-qt types
```

`examples/demo_app.ui` 是组合示例：Toolbar + TabPanel（SearchBar + DataGrid + Paging / 6 字段 Form + 主色保存按钮）+ Message。
实测渲染 70~87 ms/页。

## 覆盖情况（P0）

已适配 **69 类** widgettype（`bricks_qt/widgets.py` 的 `REGISTRY`）：

| 类别 | widgettype | Qt 实现 |
|---|---|---|
| 布局 | VBox / HBox / Cols / FHBox / FVBox / Filler / HFiller / VScrollPanel / ScrollPanel / Splitter | QVBoxLayout / QHBoxLayout / QSplitter / QScrollArea |
| 文本 | Text / Title1..Title6 | QLabel（22→13pt，Title 加粗） |
| 输入 | Input / KeyinText / SearchBar / CheckBox(UiCheckBox/Switch/Toggle) | QLineEdit / QCheckBox |
| 表单 | Form / Conform / MiniForm（str/tel/email/url/password/number/int/float/date/textarea/select/file/checkbox） | QGroupBox + QFormLayout + 原生控件（日期带日历弹窗、数值带范围步进、提交按钮主色） |
| 数据 | DataGrid / DataViewer / DataRow / Tabular / Tree / EditableTree / Paging | QTableWidget（列宽 Stretch）/ QTreeWidget（options.data + textField/children）/ 翻页条（首末页禁用） |
| 导航 | TabPanel / TabBar / Toolbar / Menu / Accordion / DynamicAccordion / IconBar / IconTextBar / Floaticonbar | QTabWidget / 工具条 / QMenuBar（有主窗口时）/ QToolBox / QToolButton 行 |
| 反馈 | Message / Tooltip / Modal / Popup / PopupWindow / NewWindow / Running / Progress | 彩色条 / QDialog（auto_open 支持）/ QProgressBar |
| 媒体 | Image / Icon / Svg / MultipleStateIcon / MultipleStateImage | QLabel + QPixmap |
| 组装 | **urlwidget**（`{{entire_url('x.ui')}}` 或直接路径；.ui/.json 递归构建，深度上限 6） | 递归 build；`.tmpl`/`.bcrud` 优雅降级 |
| 事件 | click / change / submit / search / select / node_selected / prev / next | `Ctx.dispatch` → `ctx.events`；`actiontype=urlwidget` 触发导航回调 |

未适配 **31 类**（`WEB_TYPES`）：Chart* ×10、MarkdownViewer/MdWidget、CodeEditor、Scene3D/GlbViewer、
DOCX/PDF/EXCEL viewer、Html/Iframe、Wterm、Camera/ASR/TTS/WebSocket 等——P2 用 QWebEngineView
直接嵌 bricks 现有 JS 组件，不重写 echarts/three.js。现在遇到它们画带说明的虚线占位框，不崩。

## 可插拔能力后端（图表 / 3D / 文档预览 / Markdown / 代码 / 媒体）

不把任何一个"重"控件写死成 WebEngine：每个能力可以有多个后端，按优先级择优，也可用环境变量强制指定。
装哪个依赖就启用哪个，缺依赖时该控件降级成带安装提示的占位框，不影响其它控件。

```bat
bricks-qt caps                                       :: 看每个能力当前用哪个后端、缺什么
set BRICKS_QT_PROVIDERS=chart=qtcharts,markdown=native  :: 强制指定（逗号分隔）
```

| 能力 | 后端（优先级） | 依赖 / pip extra | 说明 |
|---|---|---|---|
| chart | webengine-chart(60) / qtcharts(50) / pyqtgraph(40) | `[webengine]` / `[qtcharts]` / `[charts]` | echarts 保真最高；Qt Charts 是 **GPLv3**，闭源商用别选它；pyqtgraph 是 MIT |
| scene3d | webengine-3d(60) | `[webengine]` | model-viewer，支持 glTF/GLB，需要 WebGL |
| markdown | native-markdown(60) / webengine-markdown(50) | `[markdown]` / `[webengine]` | 原生 QTextBrowser 秒开，默认走它 |
| code | webengine-code(60) / native-code(40) | `[webengine]` | CodeMirror vs QPlainTextEdit+高亮 |
| docview | pypdfium2(60) / openpyxl(60) / mammoth(55) | `[pdf]` `[excel]` `[docx]` | PDF 逐页渲图（BSD/Apache）、Excel 按 sheet 出表、Word 转 HTML |
| html | webengine-html(60) / native-html(30) | `[webengine]` | 有 Chromium 才支持现代 HTML |
| media | native-media(60) | PySide6-Essentials | QtMultimedia，本地/HTTP 播放 |

```
$ bricks-qt caps
chart      用 webengine-chart
    ok  webengine-chart    p=60    · echarts，与浏览器一致
    ok  qtcharts           p=50    · Qt Charts 是 GPLv3 / 商业授权…
    ok  pyqtgraph          p=40    · MIT，交互缩放好
docview    用 pypdfium2
    ok  pypdfium2          p=60    · PDF 逐页渲染成图，BSD/Apache，可商用
```

两个实测踩出来的坑：
- **Qt 6.12 起 WebEngine 是独立 wheel**（`PySide6-WebEngine`），不在 `PySide6-Addons` 里；`caps` 会报"缺模块 PySide6.QtWebEngineWidgets"。
- **WebEngine 的像素不在窗口位图里**（Chromium 独立进程，`win.grab()` 拍到空白）。截图路径改为把页面里第一块 canvas
  用 `toDataURL()` 导出成 `<out>.web.png`；无 GPU 环境还要 `--enable-unsafe-swiftshader` 才有 WebGL（3D 依赖它）。

## 为什么这样分层

- **协议层不动**：`.ui` 的 `widgettype/options/subwidgets/binds` 就是协议，dspy/HTTP/WebSocket 也不动。
- **adapter 只做映射**：`widgettype → QWidget`，事件桥 `binds → Ctx.dispatch`；换平台只换这一层。
- **能原生就原生**：表格/树/表单/页签用 Qt 控件（性能、可访问性、输入法、原生观感都对）。
- **JS 重的别硬写**：echarts/three.js/CodeMirror 用 WebEngine 嵌现有 dist，省几十人日。

## 容错解析（bricks 的 .ui 不是严格 JSON）

实测 `.ui` 里有单引号（`"item_size":'28px'`）、尾逗号、注释。`bricks_qt.model.loads()` 依次尝试
`json5` → `json` → 正则清洗后的 `json`，三种都留着，缺 json5 时也能跑。

## 尺寸语义

`width/height`：`700px` → 固定；`100%`/百分号 → Expanding；`auto`/空 → 交给 sizeHint。
和 bricks 里 `item_size`、`width:"auto"` 的用法一致。

## 已知边界（P0）

- 不执行 `binds[].script` 里的 JS（没有 JS 运行时）；这类事件记进 `ctx.events`，供上层接 native handler。
- 未接后端：`dataProvider`/`submit_url` 不发请求，数据来自 `.ui` 里的静态 `data`。
- 主题/QSS 没接 bricks 的样式变量；字体用 Fusion 默认 + Microsoft YaHei UI（`bricks_qt/theme.py`）。
- 截图**不要**用 `QT_QPA_PLATFORM=offscreen`：那个插件字库为空，中文全成豆腐块；用真实平台插件 +
  `Qt.WA_DontShowOnScreen`（见 `app.render`），既有系统字体又不弹窗。
- Linux 未实测（字体回退与 POSIX 路径已写，没跑过）。

## 验收记录（本机实测）

见仓库根 `VERIFY.md`：24 个 examples 页面冒烟 + 4 张截图目检 + 组合示例两轮修正，逐条贴命令与结果。
## 宿主扩展（桌面应用怎么接）

bricks 的 .ui 只描述"长什么样 + 点了谁"，不带业务。桌面侧把业务挂上去有三个入口：

```python
from bricks_qt import Ctx, load_file, build, register

node = load_file("ui/main.ui")
ctx = Ctx(base_dir="ui")

ctx.on("connect", lambda n, e, p: do_connect())      # .ui 里 script="connect" 的按钮会走到这
build(node, ctx)
term = ctx.get("term")                               # 按 .ui 里的 id 取控件
```

`.ui` 里对应写法：

```json
{"widgettype": "Button", "id": "btn", "options": {"label": "连接"},
 "binds": [{"event": "click", "actiontype": "script", "script": "connect"}]}
```

同一份 .ui 在浏览器里跑的是同名 JS 函数，在桌面里跑的是 `ctx.on()` 注册的 Python 函数——JSON 不用改。

自定义控件类型：

```python
@register("MyWidget")
def b_my(node, ctx):
    w = MyWidget()
    _apply_size(w, node)      # 让 width/height 生效
    return w
```

### 已有的两个桌面专属控件

| 类型 | 模块 | 说明 |
|---|---|---|
| `Terminal` | `bricks_qt/terminal.py` | pyte 屏幕 + QPainter 自绘：ANSI 16/256/真彩、粗体反显、拖选/双击选词/复制、Shift+PgUp 回滚、中文输入法。宿主 `set_sender(fn)` 或 `attach_channel(chan)`，往里喂 `feed(bytes)` |
| `RemoteFiles` | `bricks_qt/remotefiles.py` | 单栏远端文件面板：`..` 上一级、双击进目录、拖入上传、拖出下载、上传/下载/新建/刷新/删除。宿主 `attach(session)` 挂数据源 |

对应 web 侧的 `Wterm`（xterm.js）与 `ResourceBrowser`，位置和事件语义一致。
