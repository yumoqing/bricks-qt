"""纯原生图表后端：QtCharts（GPLv3！）与 pyqtgraph（MIT）。"""

from __future__ import annotations

from .base import Provider, register
from .common import chart_kind, chart_points, chart_title


def qt_chart(node, ctx):
    from PySide6.QtCharts import (QBarCategoryAxis, QBarSeries, QBarSet, QChart,
                                  QChartView, QLineSeries, QPieSeries, QScatterSeries,
                                  QValueAxis)
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPen

    kind, pts, title = chart_kind(node), chart_points(node), chart_title(node)
    chart = QChart()
    chart.setTitle(title)
    names = [n for n, _ in pts]
    values = [v for _, v in pts]

    if kind == "pie":
        from PySide6.QtGui import QColor
        palette = ["#1677ff", "#52c41a", "#faad14", "#f5222d", "#722ed1",
                   "#13c2c2", "#eb2f96", "#a0d911"]
        s = QPieSeries()
        pie_opt = node.opt("pie_options", default={}) or {}
        radius = pie_opt.get("radius") or []
        # echarts 的 radius: ["40%","70%"] 表示环形；单值表示实心饼
        s.setHoleSize(0.25 if (isinstance(radius, list) and len(radius) > 1) else 0.0)
        total = sum(v for _, v in pts) or 1.0
        for i, (n, v) in enumerate(pts):
            sl = s.append(n, v)
            sl.setLabel("%s %.1f%%" % (n, v * 100.0 / total))
            sl.setLabelVisible(True)
            sl.setColor(QColor(palette[i % len(palette)]))
        chart.addSeries(s)
    elif kind == "bar":
        from PySide6.QtGui import QColor
        s, st = QBarSeries(), QBarSet(title or "值")
        st.setColor(QColor("#1677ff"))
        for v in values:
            st.append(v)
        s.append(st)
        chart.addSeries(s)
        ax = QBarCategoryAxis()
        ax.append(names)
        chart.addAxis(ax, Qt.AlignBottom)
        s.attachAxis(ax)
        ay = QValueAxis()
        chart.addAxis(ay, Qt.AlignLeft)
        s.attachAxis(ay)
    else:
        if kind != "scatter":
            s = QLineSeries()
            s.setPen(QPen(QColor("#1677ff"), 2))
        else:
            s = QScatterSeries()
            s.setColor(QColor("#1677ff"))
            s.setMarkerSize(9)
        for i, v in enumerate(values):
            s.append(i, v)
        chart.addSeries(s)
        ax = QBarCategoryAxis()
        ax.append(names)
        chart.addAxis(ax, Qt.AlignBottom)
        s.attachAxis(ax)
        ay = QValueAxis()
        chart.addAxis(ay, Qt.AlignLeft)
        s.attachAxis(ay)
    chart.legend().setVisible(bool(names))

    view = QChartView(chart)
    view.setRenderHint(QPainter.Antialiasing)
    return view


def pg_chart(node, ctx):
    import pyqtgraph as pg

    kind, pts = chart_kind(node), chart_points(node)
    w = pg.PlotWidget(title=chart_title(node))
    w.setBackground("w")
    xs = list(range(len(pts)))
    ys = [v for _, v in pts]
    if kind == "bar":
        w.addItem(pg.BarGraphItem(x=xs, height=ys, width=0.6, brush="#1677ff"))
    else:
        w.plot(xs, ys, pen=pg.mkPen("#1677ff", width=2),
               symbol="o" if kind == "scatter" else None)
    w.getAxis("bottom").setTicks([[  (i, n) for i, (n, _) in enumerate(pts) ]])
    return w


register(Provider("qtcharts", "chart", 50, qt_chart, "PySide6.QtCharts", "qtcharts",
                  note="Qt Charts 是 GPLv3 / 商业授权，商用闭源请改用 webengine 或 pyqtgraph"))
register(Provider("pyqtgraph", "chart", 40, pg_chart, "pyqtgraph", "charts",
                  note="MIT，交互缩放好"))