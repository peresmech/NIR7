from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QDockWidget, QDialog, QComboBox,
    QLabel, QHBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QPushButton
)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtWidgets import QDateEdit
from PyQt5.QtCore import QDate
from urllib.parse import parse_qs, unquote, urlparse

import sys
import os
import sqlite3
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DISPLAY_NAMES = {
    "volgograd_hub": "Волгоградский гидроузел"
}
PARAMETER_LABELS = {
    "discharge": "Расход воды",
    "level_upper": "Уровень верхнего бьефа",
    "level_lower": "Уровень нижнего бьефа",
    "hysteresis": "Петля гистерезиса",
}
PARAMETER_OPTIONS = [
    ("Обзор гидроузла", "overview"),
    ("Расход воды", "discharge"),
    ("Верхний бьеф", "level_upper"),
    ("Нижний бьеф", "level_lower"),
    ("Петля гистерезиса", "hysteresis"),
]
PERIOD_OPTIONS = [
    ("30 дн.", 30),
    ("90 дн.", 90),
    ("180 дн.", 180),
    ("1 год", 365),
    ("Весь ряд", 0),
]
OVERVIEW_OPTIONS = [
    ("Вся система", "all"),
    ("Волга", "volga"),
    ("Кама", "kama"),
    ("Притоки Волги", "volga_tributaries"),
    ("Притоки Камы", "kama_tributaries"),
]

# ===== ПЕРЕХВАТ ССЫЛОК =====
class WebPage(QWebEnginePage):
    def __init__(self, parent=None, app=None):
        super().__init__(parent)
        self.app = app

    def acceptNavigationRequest(self, url, _type, isMainFrame):
        if url.toString().startswith("pyqt://"):
            self.app.handle_url(url)
            return False
        return super().acceptNavigationRequest(url, _type, isMainFrame)


# ===== ВИДЖЕТ ГРАФИКА =====
class PlotWidget(FigureCanvas):
    def __init__(self):
        self.fig, self.ax = plt.subplots()
        self.fig.patch.set_facecolor("#f5f7fb")
        super().__init__(self.fig)

    def _ensure_single_axis(self):
        if len(self.fig.axes) != 1:
            self.fig.clf()
            self.ax = self.fig.add_subplot(111)
        else:
            self.ax = self.fig.axes[0]

    def _style_axis(self, ax):
        ax.set_facecolor("white")
        ax.grid(True, color="#dbe4ef", linewidth=0.8, alpha=0.9)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#b9c7d8")
        ax.spines["bottom"].set_color("#b9c7d8")
        ax.tick_params(axis="both", labelsize=10, colors="#41576d")

    def _apply_date_axis_format(self, ax, df):
        if df is None or df.empty:
            return

        start = df["date"].min()
        end = df["date"].max()
        span_days = max((end - start).days, 1)

        if span_days <= 45:
            locator = mdates.WeekdayLocator(interval=1)
            formatter = mdates.DateFormatter("%d.%m")
        elif span_days <= 120:
            locator = mdates.WeekdayLocator(interval=2)
            formatter = mdates.DateFormatter("%d.%m")
        elif span_days <= 370:
            locator = mdates.MonthLocator(interval=1)
            formatter = mdates.DateFormatter("%m.%Y")
        elif span_days <= 3 * 365:
            locator = mdates.MonthLocator(interval=3)
            formatter = mdates.DateFormatter("%m.%Y")
        else:
            locator = mdates.YearLocator(base=1)
            formatter = mdates.DateFormatter("%Y")

        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        ax.tick_params(axis="x", rotation=35, labelsize=10, colors="#41576d")

    def plot(self, df, title, parameter):
        self._ensure_single_axis()
        self.ax.clear()
        self._style_axis(self.ax)

        if df.empty:
            self.ax.set_title(f"{title} (нет данных)")
        else:
            line_color = "#0D47A1" if parameter == "discharge" else "#00838F"
            self.ax.plot(df["date"], df["value"], color=line_color, linewidth=2.2)
            self.ax.set_title(title)

        self._apply_date_axis_format(self.ax, df)
        self.ax.set_xlabel("Дата", fontsize=11, color="#30485f")

        if parameter == "discharge":
            self.ax.set_ylabel("Расход воды, м3/с", fontsize=11, color="#30485f")
        elif parameter == "level_upper":
            self.ax.set_ylabel("Уровень верхнего бьефа, м", fontsize=11, color="#30485f")
        elif parameter == "level_lower":
            self.ax.set_ylabel("Уровень нижнего бьефа, м", fontsize=11, color="#30485f")

        self.fig.tight_layout()
        self.draw()

    def plot_overview(self, station_name, discharge_df, upper_df, lower_df):
        self.fig.clf()
        ax_q = self.fig.add_subplot(311)
        ax_levels = self.fig.add_subplot(312, sharex=ax_q)
        ax_delta = self.fig.add_subplot(313, sharex=ax_q)
        for ax in (ax_q, ax_levels, ax_delta):
            self._style_axis(ax)

        has_any = any(not df.empty for df in (discharge_df, upper_df, lower_df))
        if not has_any:
            ax_q.set_title(f"{station_name}: нет данных", fontsize=13, color="#19324a")
            self.fig.tight_layout()
            self.draw()
            return

        if not discharge_df.empty:
            ax_q.plot(discharge_df["date"], discharge_df["value"], color="#0D47A1", linewidth=2.2)
        ax_q.set_title(f"{station_name}: обзор гидрологического режима", fontsize=14, color="#19324a")
        ax_q.set_ylabel("Q, м3/с", fontsize=11, color="#30485f")

        if not upper_df.empty:
            ax_levels.plot(upper_df["date"], upper_df["value"], color="#00838F", linewidth=2.0, label="Верхний бьеф")
        if not lower_df.empty:
            ax_levels.plot(lower_df["date"], lower_df["value"], color="#43A047", linewidth=2.0, label="Нижний бьеф")
        ax_levels.set_ylabel("Уровень, м", fontsize=11, color="#30485f")
        if not upper_df.empty or not lower_df.empty:
            ax_levels.legend(loc="upper right", fontsize=9, frameon=False)

        if not upper_df.empty and not lower_df.empty:
            delta = pd.merge(
                upper_df[["date", "value"]].rename(columns={"value": "upper"}),
                lower_df[["date", "value"]].rename(columns={"value": "lower"}),
                on="date",
            )
            if not delta.empty:
                delta["delta"] = delta["upper"] - delta["lower"]
                ax_delta.plot(delta["date"], delta["delta"], color="#F39C12", linewidth=2.0)
        ax_delta.set_ylabel("Перепад уровней, м", fontsize=11, color="#30485f")
        ax_delta.set_xlabel("Дата", fontsize=11, color="#30485f")

        reference_df = discharge_df
        if reference_df.empty and not upper_df.empty:
            reference_df = upper_df
        if reference_df.empty and not lower_df.empty:
            reference_df = lower_df

        for ax in (ax_q, ax_levels, ax_delta):
            self._apply_date_axis_format(ax, reference_df)

        ax_q.tick_params(axis="x", labelbottom=False)
        ax_levels.tick_params(axis="x", labelbottom=False)

        self.fig.tight_layout()
        self.draw()


# ===== ОКНО АНАЛИТИКИ =====
class PlotWindow(QDialog):
    def __init__(self, station, display_name=None):
        super().__init__()

        self.station = station.lower()  # 🔥 НОРМАЛИЗАЦИЯ
        self.display_name = display_name or DISPLAY_NAMES.get(self.station, station)

        self.setWindowTitle(f"Графики: {self.display_name}")

        self.resize(1000, 600)
        self.setStyleSheet("""
            QDialog {
                background: #f5f7fb;
            }
            QLabel {
                font-size: 14px;
                font-weight: 600;
                color: #243447;
                margin-top: 8px;
            }
            QComboBox, QDateEdit {
                font-size: 15px;
                color: #1f2d3d;
                background: white;
                border: 1px solid #c8d4e3;
                border-radius: 10px;
                min-height: 42px;
                padding: 6px 12px;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
            }
            QComboBox:hover, QDateEdit:hover {
                border-color: #7aa7d9;
            }
            QComboBox:focus, QDateEdit:focus {
                border: 2px solid #2f6db2;
                padding: 5px 11px;
            }
            QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
                width: 18px;
                border: none;
            }
            QComboBox QAbstractItemView {
                selection-background-color: #d9e8fb;
                selection-color: #14324f;
            }
            QPushButton#PeriodButton {
                font-size: 12px;
                font-weight: 600;
                color: #21405f;
                background: #eef4fb;
                border: 1px solid #d5dfeb;
                border-radius: 10px;
                min-height: 34px;
                min-width: 68px;
                padding: 0 8px;
            }
            QPushButton#PeriodButton:hover {
                background: #dfeafb;
                border-color: #9dbde1;
            }
        """)

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(18)

        # ===== ЛЕВАЯ ПАНЕЛЬ =====
        control_panel = QWidget()
        control_panel.setFixedWidth(260)
        control_panel.setStyleSheet("""
            QWidget {
                background: white;
                border: 1px solid #d9e3ef;
                border-radius: 16px;
            }
        """)
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(16, 16, 16, 16)
        control_layout.setSpacing(10)

        panel_title = QLabel("Параметры графика")
        panel_title.setStyleSheet("""
            QLabel {
                font-size: 18px;
                font-weight: 700;
                color: #18324a;
                margin-top: 0;
                margin-bottom: 8px;
            }
        """)
        control_layout.addWidget(panel_title)

        self.param_box = QComboBox()
        for label, value in PARAMETER_OPTIONS:
            self.param_box.addItem(label, value)
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate(2010, 1, 1))

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())

        self.period_buttons = []
        period_row_top = QHBoxLayout()
        period_row_bottom = QHBoxLayout()
        period_row_top.setSpacing(6)
        period_row_bottom.setSpacing(6)
        period_layouts = [period_row_top, period_row_bottom]
        for index, (label, days) in enumerate(PERIOD_OPTIONS):
            button = QPushButton(label)
            button.setObjectName("PeriodButton")
            button.clicked.connect(lambda checked=False, period_days=days: self.apply_period(period_days))
            self.period_buttons.append(button)
            period_layouts[index // 3].addWidget(button)

        control_layout.addWidget(QLabel("С даты:"))
        control_layout.addWidget(self.date_from)

        control_layout.addWidget(QLabel("По дату:"))
        control_layout.addWidget(self.date_to)
        control_layout.addWidget(QLabel("Быстрый период:"))
        control_layout.addLayout(period_row_top)
        control_layout.addLayout(period_row_bottom)
        control_layout.addWidget(QLabel("Параметр:"))
        control_layout.addWidget(self.param_box)
        control_layout.addStretch()

        # ===== ГРАФИК =====
        self.plot = PlotWidget()

        main_layout.addWidget(control_panel, 1)
        main_layout.addWidget(self.plot, 4)

        self.setLayout(main_layout)

        self.param_box.currentTextChanged.connect(self.update_plot)
        self.date_from.dateChanged.connect(self.update_plot)
        self.date_to.dateChanged.connect(self.update_plot)

        self.apply_period(90)
        self.update_plot()

    def apply_period(self, days):
        end_date = QDate.currentDate()

        self.date_from.blockSignals(True)
        self.date_to.blockSignals(True)

        self.date_to.setDate(end_date)
        if days == 0:
            self.date_from.setDate(QDate(2010, 1, 1))
        else:
            self.date_from.setDate(end_date.addDays(-days))

        self.date_from.blockSignals(False)
        self.date_to.blockSignals(False)
        self.update_plot()

    # ===== ЗАГРУЗКА ДАННЫХ =====
    def load_data(self, parameter):
        conn = sqlite3.connect("data/hydro.db")

        date_from = self.date_from.date().toString("yyyy-MM-dd")
        date_to = self.date_to.date().toString("yyyy-MM-dd")

        query = """
        SELECT date, value
        FROM observations
        WHERE LOWER(station) = ?
        AND parameter = ?
        AND date BETWEEN ? AND ?
        ORDER BY date
        """

        df = pd.read_sql(query, conn, params=(self.station, parameter, date_from, date_to))
        conn.close()

        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])

        return df

    # ===== ГИСТЕРЕЗИС =====
    def load_hysteresis(self):
        conn = sqlite3.connect("data/hydro.db")

        q = pd.read_sql("""
            SELECT date, value as discharge
            FROM observations
            WHERE LOWER(station) = ?
            AND parameter = 'discharge'
        """, conn, params=(self.station,))

        h = pd.read_sql("""
            SELECT date, value as level
            FROM observations
            WHERE LOWER(station) = ?
            AND parameter = 'level_upper'
        """, conn, params=(self.station,))

        conn.close()

        df = pd.merge(q, h, on="date")

        return df

    def plot_hysteresis(self, df):
        self.plot._ensure_single_axis()
        self.plot.ax.clear()  # 🔥 ВАЖНО
        self.plot._style_axis(self.plot.ax)

        if df.empty:
            self.plot.ax.set_title("Нет данных")
        else:
            df["date"] = pd.to_datetime(df["date"])
            df["year"] = df["date"].dt.year

            for year, group in df.groupby("year"):
                self.plot.ax.plot(group["discharge"], group["level"], label=str(year), linewidth=1.8)

            self.plot.ax.set_xlabel("Расход воды, м3/с", fontsize=11, color="#30485f")
            self.plot.ax.set_ylabel("Уровень верхнего бьефа, м", fontsize=11, color="#30485f")
            self.plot.ax.legend(title="Год", fontsize=9, title_fontsize=10)

        self.plot.fig.tight_layout()

        self.plot.draw()

    # ===== ОБНОВЛЕНИЕ =====
    def update_plot(self):
        param = self.param_box.currentData()

        if param == "overview":
            discharge_df = self.load_data("discharge")
            upper_df = self.load_data("level_upper")
            lower_df = self.load_data("level_lower")
            self.plot.plot_overview(self.display_name, discharge_df, upper_df, lower_df)
        elif param == "hysteresis":
            df = self.load_hysteresis()
            self.plot_hysteresis(df)
        else:
            df = self.load_data(param)
            param_label = PARAMETER_LABELS.get(param, param)
            self.plot.plot(df, f"{self.display_name} — {param_label}", param)


# ===== ГЛАВНОЕ ОКНО =====
class GISApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.selected_station = None
        self.station_records = self.load_station_records()

        self.setWindowTitle("ГИС Волго-Камского бассейна")
        self.resize(1400, 800)
        self.setStyleSheet("""
            QDockWidget {
                font-size: 18px;
                font-weight: 700;
                color: #1e3144;
                background: #eef3f9;
                border-right: 1px solid #d7e0ea;
            }
            QDockWidget::title {
                text-align: left;
                background: #e6edf5;
                padding: 16px 18px;
                border-bottom: 1px solid #ccd7e3;
            }
            QLabel#SectionTitle {
                font-size: 13px;
                font-weight: 600;
                letter-spacing: 0.5px;
                text-transform: uppercase;
                color: #60758b;
                margin-top: 8px;
            }
            QComboBox, QLineEdit {
                font-size: 15px;
                color: #23384d;
                background: white;
                border: 1px solid #d5dfeb;
                border-radius: 12px;
                min-height: 42px;
                padding: 6px 12px;
            }
            QComboBox:hover, QLineEdit:hover, QListWidget:hover {
                border-color: #8cb1d8;
            }
            QComboBox:focus, QLineEdit:focus {
                border: 2px solid #2f6db2;
                padding: 5px 11px;
            }
            QListWidget {
                font-size: 15px;
                color: #243447;
                background: white;
                border: 1px solid #d5dfeb;
                border-radius: 16px;
                padding: 8px;
                outline: none;
            }
            QListWidget::item {
                border-radius: 10px;
                padding: 10px 12px;
                margin: 2px 0;
            }
            QListWidget::item:selected {
                background: #d9e8fb;
                color: #14324f;
            }
            QListWidget::item:hover {
                background: #f2f7fd;
            }
            QLabel#ObjectTitle {
                font-size: 17px;
                font-weight: 700;
                color: #16324a;
            }
            QLabel#ObjectMeta {
                font-size: 14px;
                color: #5c7085;
                line-height: 1.5;
            }
            QPushButton {
                font-size: 15px;
                font-weight: 600;
                color: #15324d;
                background: #f4c95d;
                border: 1px solid #e2b84a;
                border-radius: 12px;
                min-height: 44px;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: #efbf42;
            }
            QPushButton:disabled {
                background: #c3d2e3;
                color: #6b7f93;
                border: 1px solid #c3d2e3;
            }
        """)

        self.browser = QWebEngineView()
        self.browser.setPage(WebPage(self.browser, self))

        self.map_path = os.path.join(PROJECT_ROOT, "map.html")

        self.setCentralWidget(self.browser)

        # ===== DOCK =====
        dock = QDockWidget("Навигация", self)
        dock.setMinimumWidth(320)

        dock_widget = QWidget()
        dock_widget.setStyleSheet("background: #eef3f9;")
        dock_layout = QVBoxLayout(dock_widget)
        dock_layout.setContentsMargins(16, 16, 16, 16)
        dock_layout.setSpacing(12)

        overview_title = QLabel("Обзор сети")
        overview_title.setObjectName("SectionTitle")
        dock_layout.addWidget(overview_title)

        self.overview_box = QComboBox()
        for label, value in OVERVIEW_OPTIONS:
            self.overview_box.addItem(label, value)
        dock_layout.addWidget(self.overview_box)

        search_title = QLabel("Гидроузлы")
        search_title.setObjectName("SectionTitle")
        dock_layout.addWidget(search_title)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск по названию")
        dock_layout.addWidget(self.search_input)

        self.station_list = QListWidget()
        dock_layout.addWidget(self.station_list, 1)

        object_title = QLabel("Выбранный объект")
        object_title.setObjectName("SectionTitle")
        dock_layout.addWidget(object_title)

        self.object_card = QWidget()
        self.object_card.setObjectName("ObjectCard")
        self.object_card.setStyleSheet("""
            QWidget#ObjectCard {
                background: white;
                border: 1px solid #d5dfeb;
                border-radius: 16px;
            }
            QWidget#ObjectCard QLabel {
                background: transparent;
                border: none;
            }
        """)
        object_layout = QVBoxLayout(self.object_card)
        object_layout.setContentsMargins(16, 16, 16, 16)
        object_layout.setSpacing(10)

        self.object_name = QLabel("Выберите гидроузел")
        self.object_name.setObjectName("ObjectTitle")
        object_layout.addWidget(self.object_name)

        self.object_meta = QLabel("Список слева поможет быстро перейти к нужному объекту.")
        self.object_meta.setObjectName("ObjectMeta")
        self.object_meta.setWordWrap(True)
        object_layout.addWidget(self.object_meta)

        self.open_plot_btn = QPushButton("Открыть графики")
        self.open_plot_btn.setEnabled(False)
        object_layout.addWidget(self.open_plot_btn)

        dock_layout.addWidget(self.object_card)

        dock.setWidget(dock_widget)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

        self.overview_box.currentIndexChanged.connect(self.handle_overview_change)
        self.search_input.textChanged.connect(self.populate_station_list)
        self.station_list.currentItemChanged.connect(self.handle_station_selection)
        self.station_list.itemDoubleClicked.connect(self.open_selected_plot)
        self.open_plot_btn.clicked.connect(self.open_selected_plot)

        self.populate_station_list()
        self.update_map()

    def load_station_records(self):
        nodes = gpd.read_file("data/network_nodes.geojson")
        nodes = nodes[nodes["type"] == "station"].copy()
        if "db_station" in nodes.columns:
            nodes["station_id"] = nodes["db_station"]
        else:
            nodes["station_id"] = nodes["name"].str.lower()

        records = []
        if "sort_order" not in nodes.columns:
            nodes["sort_order"] = 0
        if "object_type" not in nodes.columns:
            nodes["object_type"] = "hub"
        nodes["type_rank"] = nodes["object_type"].map({"hub": 0, "post": 1}).fillna(9)
        for _, row in nodes.sort_values(["river", "type_rank", "sort_order", "name"]).iterrows():
            records.append({
                "name": str(row["name"]),
                "river": str(row.get("river", "")),
                "station_id": str(row.get("station_id", "")),
                "object_type": str(row.get("object_type", "hub")),
                "river_section": str(row.get("river_section", "")),
            })
        return records

    def current_overview(self):
        return self.overview_box.currentData()

    def station_matches_overview(self, record):
        overview = self.current_overview()
        if overview in {"all", "volga", "volga_tributaries"}:
            if overview == "all":
                return True
            return record["river"] == "Волга"
        if overview in {"kama", "kama_tributaries"}:
            return record["river"] == "Кама"
        return True

    def populate_station_list(self):
        search_value = self.search_input.text().strip().lower()
        previous_station_id = self.selected_station["station_id"] if self.selected_station else None

        self.station_list.blockSignals(True)
        self.station_list.clear()

        selected_item = None
        for record in self.station_records:
            if not self.station_matches_overview(record):
                continue
            if search_value and search_value not in record["name"].lower():
                continue

            label = (
                record["name"]
                if record.get("object_type") == "hub"
                else f"г/п {record['name']}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, record)
            tooltip = f"Река: {record['river']}"
            if record.get("river_section"):
                tooltip += f"\nУчасток: {record['river_section']}"
            tooltip += (
                "\nТип: гидроузел"
                if record.get("object_type") == "hub"
                else "\nТип: гидропост"
            )
            item.setToolTip(tooltip)
            self.station_list.addItem(item)
            if previous_station_id and record["station_id"] == previous_station_id:
                selected_item = item

        self.station_list.blockSignals(False)

        if selected_item is not None:
            self.station_list.setCurrentItem(selected_item)
        elif self.station_list.count() > 0:
            self.station_list.setCurrentRow(0)
        else:
            self.selected_station = None
            self.update_selected_object_card()
            self.update_map()

    def handle_overview_change(self):
        self.populate_station_list()
        self.update_map()

    def handle_station_selection(self, current, previous):
        if current is None:
            self.selected_station = None
        else:
            self.selected_station = current.data(Qt.UserRole)
        self.update_selected_object_card()
        self.update_map()

    def update_selected_object_card(self):
        if not self.selected_station:
            self.object_name.setText("Выберите гидроузел")
            self.object_meta.setText("Список слева поможет быстро перейти к нужному объекту.")
            self.open_plot_btn.setEnabled(False)
            return

        self.object_name.setText(self.selected_station["name"])
        self.object_meta.setText(
            f"Река: {self.selected_station['river']}\n"
            f"Тип: {'гидроузел' if self.selected_station.get('object_type') == 'hub' else 'гидропост'}"
            + (
                f"\nУчасток: {self.selected_station['river_section']}"
                if self.selected_station.get("river_section")
                else ""
            )
        )
        self.open_plot_btn.setEnabled(True)

    # ===== ОБНОВЛЕНИЕ КАРТЫ =====
    def update_map(self):
        from viz.map import create_map

        selected_station_id = self.selected_station["station_id"] if self.selected_station else None
        m = create_map(
            "data/network_edges.geojson",
            "data/network_nodes.geojson",
            overview=self.current_overview(),
            selected_station_id=selected_station_id,
        )
        m.save(self.map_path)

        self.browser.setUrl(QUrl("about:blank"))
        self.browser.setUrl(QUrl.fromLocalFile(self.map_path))

    def open_selected_plot(self):
        if not self.selected_station:
            return
        self.plot_window = PlotWindow(
            self.selected_station["station_id"],
            self.selected_station["name"],
        )
        self.plot_window.show()

    def select_station_by_id(self, station_id):
        if not station_id:
            return
        for index in range(self.station_list.count()):
            item = self.station_list.item(index)
            record = item.data(Qt.UserRole)
            if record and record.get("station_id") == station_id:
                self.station_list.setCurrentItem(item)
                self.station_list.scrollToItem(item)
                return

    # ===== КЛИК =====
    def handle_url(self, url):
        url_str = url.toString()
        parsed = urlparse(url_str)

        if parsed.scheme == "pyqt" and parsed.netloc == "station":
            station = unquote(parsed.path.lstrip("/"))
            label = parse_qs(parsed.query).get("label", [station])[0]
            label = unquote(label)

            print("🔥 Клик по станции:", station)

            self.select_station_by_id(station)
            self.plot_window = PlotWindow(station, label)
            self.plot_window.show()


# ===== RUN =====
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = GISApp()
    window.show()
    sys.exit(app.exec_())
