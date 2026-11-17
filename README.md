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