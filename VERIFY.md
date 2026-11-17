# 验收记录（P0 原型）

环境：Windows 11 + Python 3.14.9 + PySide6 6.12.0（Essentials）+ json5 0.17.3
venv：`C:\Users\safec\AppData\Local\hermes\vendor\bricks-qt\venv`
数据源：`D:\work\repos\bricks\examples\*.ui`（bricks 仓库自带 61 个示例）

## 1. 类型覆盖

```
$ bricks-qt types
已适配 69 类：
  Accordion BlankIcon Button CheckBox Cols Confirm Conform DataGrid DataRow DataViewer
  DynamicAccordion EditableTree FHBox FHbox FVBox FVbox Filler Floaticonbar Floaticontextbar
  Form HBox HFiller HScrollPanel Icon IconBar IconBarText IconTextBar Image Input KeyinText
  Loading Menu Message MiniForm Modal MultipleStateIcon MultipleStateImage NewWindow Paging
  Popup PopupWindow Progress Running ScrollPanel SearchBar Splitter StatedIcon Svg Switch
  TabBar TabPanel Tabular Text Title1-6 Toggle Toolbar Tooltip Tree URLWidget UiCheckBox
  VBox VScrollPanel urlwidget
待接 WebEngine 31 类：
  ASRClient ApiDoc AudioRecorder Camera ChartBar ChartHeatmap ChartKLine ChartLine ChartMap
  ChartPie ChartRadar ChartScatter CodeEditor DOCXviewer EXCELviewer GlbViewer Html IconbarPage
  Iframe MarkdownViewer MdWidget PDFviewer QAFrame ResourceBrowser Scene3D Video VideoPlayer
  WebASR WebSocket WebTTS Wterm
```

## 2. examples/ 冒烟（24 个页面，全部 ok，无未适配类型）

```
file                    root        wid   png       result  web  unknown
hbox.ui                 HBox          5   1100x760  ok      -    -
vbox.ui                 VBox         11   1100x760  ok      -    -
form.ui                 VBox          7   1100x760  ok      -    -
miniform.ui             urlwidget     1   1100x760  ok      -    -
accordion.ui            Accordion     1   1100x760  ok      -    -
dynamicaccordion.ui     DynamicAccordion 1 1100x760 ok      -    -
tab.ui                  TabPanel      1   1100x760  ok      -    -
toolbar.ui              Toolbar       1   1100x760  ok      -    -
tree.ui                 urlwidget     1   1100x760  ok      -    -
editabletree.ui         urlwidget     1   1100x760  ok      -    -
datagrid.ui             urlwidget     1   1100x760  ok      -    -
table.ui                urlwidget     1   1100x760  ok      -    -
tabular.ui              Tabular       1   1100x760  ok      -    -
button.ui               HBox          3   1100x760  ok      -    -
image.ui                Image         1   1100x760  ok      -    -
message.ui              Message       1   1100x760  ok      -    -
modal.ui                urlwidget     1   1100x760  ok      -    -
checkbox.ui             UiCheckBox    1   1100x760  ok      -    -
scroll.ui               VBox          4   1100x760  ok      -    -
text.ui                 VBox         11   1100x760  ok      -    -
conform.ui              Conform       1   1100x760  ok      -    -
keytext.ui              Text          1   1100x760  ok      -    -
iconbar.ui              IconBar       1   1100x760  ok      -    -
```

命令：`python tests/smoke.py --examples D:/work/repos/bricks/examples --out out --dist D:/work/repos/bricks/dist`

## 3. 逐页目检（截图 + 视觉核对）

| 页面 | 结论 |
|---|---|
| form.ui | 13 个字段全部成对渲染：Email/Tel/File/Name/Integer/Float/Date/Password/CheckBox/Code/Image/Audio/Video，标签左列对齐、输入框右列齐宽，中文“提交”“* 必填”清晰无乱码 |
| tree.ui（urlwidget → tree.json） | 真树控件：node1/node11/node112 三级缩进正确，展开三角正常，数据来自 options.data + textField/children |
| tab.ui | 4 个页签（C / JavaScript / PHP / Python），内容区显示当前页文本 “This is a C language documents” |
| datagrid.ui（urlwidget → users.bcrud） | 优雅降级为占位 `<CRUD users.bcrud>（P0 未接入，P2 用 WebEngine 承载）`，无崩溃、无白屏 |

## 4. 组合示例（examples/demo_app.ui）

Toolbar（新建/刷新/导出）+ TabPanel（项目列表：SearchBar + DataGrid 5 列 3 行 + Paging；项目详情：Form 6 字段 + 主色保存按钮）+ 底部 Message 提示条。

```
$ bricks-qt shot examples/demo_app.ui --out out/demo_app.png --size 1100x760
{"ok": true, "size": [1100, 760], "pixels": [1650, 1140], "dpr": 1.5, "warnings": []}
```

目检修正过两轮，最终：

- 表格横向铺满、5 列列宽自适应，长项目名不再截断（`QHeaderView.Stretch`）
- “项目名称/阶段/评分/截止日期/备注”控件类型正确；日期为 `2026-11-17` + 日历弹窗
- “变更通知我”只出现一次（勾选框不再重复文案）
- 保存按钮主色强调（#1677ff）
- 构建耗时 **70~87 ms/页**（含解析 + 建树，PySide6 6.12/Qt 6.10）

## 5. 踩过并修掉的坑

1. **`QT_QPA_PLATFORM=offscreen` 的字库是空的** → 中文全成豆腐块（`QFontDatabase.families()` 返回 0）。
   改用真实平台插件 + `Qt.WA_DontShowOnScreen`（不弹窗但用系统字体），字体 Microsoft YaHei UI、317 个字族。
2. **`.ui` 不是严格 JSON**：有单引号（`'28px'`）、尾逗号、注释 → 用 json5，兼正则清洗兜底。
3. **`urlwidget` 的 url 是模板表达式** `{{entire_url('tree.ui')}}` → 正则取文件名后按 .ui/.json 递归构建，
   `.tmpl`/`.bcrud` 优雅降级（需要后端/WebEngine）。
4. **Form 里 checkbox 文案重复**（行标签 + 勾选框文字）→ 勾选框置空。
5. **QDateEdit 默认显示 1/1/2000** → 无值时取当天，`setCalendarPopup(True)`，格式 yyyy-MM-dd。
6. **DPI**：grab() 出的是物理像素（1100x760 逻辑 → 1650x1140 物理，dpr=1.5），JSON 里两个数都报。

## 6. 尚未验证 / 明确未做

- 真机 GUI 交互（点击/输入/拖拽）只验证了 `binds` 分发与 `ctx.events` 记录，没用真鼠标点。
- `binds[].script` 里的 JS 一律不执行（原生环境没有 JS 运行时），只记录事件。
- 主题变量（bricks 的 colors/尺寸变量）未接 QSS；字体/间距用 Qt 默认 + Fusion。
- Linux 未实测（代码里 POSIX 路径与字体回退已写，但没跑过）。


## 7. 可插拔能力后端（P2）实测

命令：`bricks-qt caps` + 逐个能力强制指定后端截图：

| 能力 | 强制后端 | 结果 |
|---|---|---|
| chart | webengine-chart | ok，echarts 环形图（canvas 导出 `out/m_chart-echarts.png.web.png`，5 扇区+图例+引导线） |
| chart | qtcharts | ok，原生 QChartView，5 色扇区 + 名称/百分比标签（`out/m_chart-qt.png`） |
| chart | pyqtgraph | ok，MIT 原生绘图 |
| markdown | native-markdown | ok，QTextBrowser 直出（标题/链接正常） |
| html | webengine-html | ok |
| docview | pypdfium2 | ok，招标 PDF 首页真实渲图（可滚动） |
| docview | mammoth | ok，Word 转 HTML 后正文/标题层级可读 |
| docview | openpyxl | ok，6 个 sheet 页签 + summary 表 4 列数据 |
| scene3d | webengine-3d | ok，model-viewer 加载本地 triangle.glb，`loaded: true`，WebGL 可用 |

`caps` 现状（装齐 webengine + markdown + pypdfium2 + openpyxl + mammoth + pyqtgraph）：

```
chart     webengine-chart(60) / qtcharts(50) / pyqtgraph(40)
scene3d   webengine-3d(60)
markdown  native-markdown(60) / webengine-markdown(50)
code      webengine-code(60) / native-code(40)
docview   pypdfium2(60) / openpyxl(60) / mammoth(55)
html      webengine-html(60) / native-html(30)
media     native-media(60)
```

### 本段新增的坑
1. **Qt 6.12 的 WebEngine 不在 `PySide6-Addons` 里**，要单独装 `PySide6-WebEngine==6.12.0.140`，否则
   `find_spec('PySide6.QtWebEngineWidgets')` 为 False，provider 静默跳过。
2. **WebEngine 像素抓不到**：`win.grab()` 拍出来是空白（Chromium 独立进程 + 合成器）。三种试法只有一种可行：
   `--disable-gpu` 无效、`QWebEngineView.grab()` 返回 null、**`canvas.toDataURL()` 导出可行**（已内置为 `<out>.web.png`）。
3. **无 GPU 时 WebGL 不可用** → 3D 不加载；加 `--enable-unsafe-swiftshader` 后 `webgl: True`、model-viewer `loaded: true`。
4. **3D 别用外网模型 URL 验收**：本机 Chromium 不走 socks5，远程 glb 一直停在 `loading`；用本地 glb 才测得准。
5. **`.ui` 里的 `%` 会撞 Python %-格式化**（`height:100%` → `unsupported format character`）：模板里写成 `%%`。
6. **pypdfium2 的 `to_pil()` 需要 Pillow**：改用 `to_numpy()` + QImage，省一个依赖，但要注意 `QImage.copy()`
   否则 numpy 缓冲区释放后图变黑。
