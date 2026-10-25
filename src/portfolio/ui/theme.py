"""Single source of UI color, typography and spacing; no financial rules."""
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class DesignTokens:
    background: str = "#F6F8FB"
    panel: str = "#FFFFFF"
    on_brand: str = "#FFFFFF"
    sidebar: str = "#F0F3F8"
    heading: str = "#17253D"
    text: str = "#334155"
    muted: str = "#64748B"
    subtle: str = "#94A3B8"
    brand: str = "#2563EB"
    brand_hover: str = "#1D4ED8"
    selected: str = "#E7EFFF"
    border: str = "#E5EAF1"
    alternate: str = "#FAFBFD"
    hover: str = "#F3F6FC"
    profit: str = "#16A34A"
    loss: str = "#DC2626"
    warning: str = "#D97706"
    success_bg: str = "#DCFCE7"
    error_bg: str = "#FEE2E2"
    warning_bg: str = "#FFF7E8"
    buy_bg: str = "#ECFDF3"
    sell_bg: str = "#FEF2F2"
    violet: str = "#8B5CF6"
    teal: str = "#0D9488"
    spacing: tuple = (4, 8, 12, 16, 20, 24, 32)
    page_margin: int = 24
    card_radius: int = 12
    control_radius: int = 8
    row_height: int = 40
    header_height: int = 42


def tokens(widget=None):
    return getattr(widget.window(), "design_tokens", DesignTokens()) if widget else DesignTokens()


def numeric_font(size=13, *, bold=False):
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


class ThemeManager(QObject):
    changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.mode = "light"
        self.green_profit = True

    def apply(self, mode="light", *, green_profit=None):
        self.mode = mode
        if green_profit is not None:
            self.green_profit = green_profit
        dark = mode == "dark" or (mode == "system" and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        t = DesignTokens()
        if dark:
            t = replace(t, background="#141B26", panel="#1C2533", sidebar="#182130", heading="#EDF2FB", text="#D4DEED", muted="#A0B0C6", subtle="#7F92AB", border="#2D3B50", alternate="#202B3B", hover="#263449", selected="#243E69", success_bg="#183B2B", error_bg="#44282D", warning_bg="#3B3020", buy_bg="#183B2B", sell_bg="#44282D")
        if not self.green_profit:
            t = replace(t, profit=t.loss, loss=t.profit, buy_bg=t.sell_bg, sell_bg=t.buy_bg)
        self.window.design_tokens = t
        palette = self.window.palette()
        for role, color in ((QPalette.ColorRole.Window, t.background), (QPalette.ColorRole.Base, t.panel),
                            (QPalette.ColorRole.WindowText, t.text), (QPalette.ColorRole.Text, t.text),
                            (QPalette.ColorRole.Button, t.panel), (QPalette.ColorRole.ButtonText, t.text),
                            (QPalette.ColorRole.Highlight, t.selected), (QPalette.ColorRole.HighlightedText, t.heading)):
            from PySide6.QtGui import QColor
            palette.setColor(role, QColor(color))
        self.window.setPalette(palette)
        self.window.setStyleSheet(stylesheet(t))
        self.changed.emit()
        self.window.update()


def stylesheet(t):
    return f"""
    QWidget {{ font-size:13px; color:{t.text}; }}
    QMainWindow, QStackedWidget, QWidget#workspace {{ background:{t.background}; }}
    QWidget#sidebar {{ background:{t.sidebar}; border-right:1px solid {t.border}; }}
    QLabel#brand {{ color:{t.heading}; font-size:18px; font-weight:600; }}
    QLabel#subtitle, QLabel#secondary, QLabel#scope {{ color:{t.muted}; font-size:12px; }}
    QLabel#heading {{ color:{t.heading}; font-size:24px; font-weight:600; }}
    QLabel#sectionTitle {{ color:{t.heading}; font-size:17px; font-weight:600; }}
    QLabel#navGroup {{ color:{t.subtle}; font-size:11px; padding:12px 12px 4px; }}
    QListWidget#navigation {{ background:transparent; border:0; outline:0; }}
    QListWidget#navigation::item {{ border:0; padding:0 12px; margin:2px 8px; border-radius:8px; }}
    QListWidget#navigation::item:hover {{ background:{t.hover}; }}
    QListWidget#navigation::item:selected {{ background:{t.selected}; color:{t.brand}; }}
    QFrame#card, QWidget#tradeSurface, QWidget#detailDrawer, QFrame#filterCard {{ background:{t.panel}; border:1px solid {t.border}; border-radius:12px; }}
    QLabel#metricName {{ color:{t.muted}; font-size:12px; }}
    QLabel#metricValue {{ color:{t.heading}; font-size:28px; font-weight:600; }}
    QLabel#metricFootnote {{ color:{t.subtle}; font-size:11px; }}
    QLabel#banner {{ color:{t.muted}; background:transparent; padding:4px 0; font-size:12px; }}
    QLabel#banner[state="error"] {{ color:{t.loss}; background:{t.error_bg}; border-radius:8px; padding:10px 12px; }}
    QLabel#banner[state="loading"] {{ color:{t.brand}; }}
    QLabel#banner[state="warning"] {{ color:{t.warning}; }}
    QPushButton, QToolButton, QComboBox, QDateEdit, QLineEdit {{ background:{t.panel}; border:1px solid {t.border}; border-radius:8px; padding:7px 10px; min-height:18px; }}
    QPushButton:hover, QToolButton:hover, QComboBox:hover {{ border-color:{t.brand}; background:{t.hover}; }}
    QPushButton:disabled, QToolButton:disabled {{ color:{t.subtle}; border-color:{t.border}; }}
    QPushButton#primary {{ color:{t.on_brand}; background:{t.brand}; border:1px solid {t.brand}; font-weight:600; }}
    QPushButton#primary:hover {{ background:{t.brand_hover}; }}
    QToolButton#sourceBadge {{ color:{t.muted}; background:transparent; border:0; font-size:12px; padding:4px 0; text-align:left; }}
    QToolButton#filterChip {{ color:{t.brand}; background:{t.selected}; border:0; padding:4px 8px; font-size:11px; }}
    QPushButton#quiet, QToolButton#quiet {{ background:transparent; border:0; color:{t.muted}; padding:3px 6px; min-height:16px; }}
    QPushButton#navToggle {{ background:transparent; border:0; padding:4px; }}
    QLineEdit:focus, QDateEdit:focus, QComboBox:focus {{ border:1px solid {t.brand}; }}
    QComboBox::drop-down, QDateEdit::drop-down {{ border:0; width:20px; }}
    QComboBox#tickerSearch::drop-down {{ border:0; width:0; }}
    QComboBox#tickerSearch::down-arrow {{ image:none; width:0; }}
    QComboBox#tickerSearch QLineEdit {{ border:0; padding:0; background:transparent; }}
    QComboBox QAbstractItemView {{ background:{t.panel}; color:{t.text}; selection-background-color:{t.selected}; selection-color:{t.brand}; padding:4px; border:1px solid {t.border}; }}
    QCheckBox {{ spacing:6px; color:{t.muted}; }}
    QCheckBox::indicator {{ width:14px; height:14px; }}
    QTableView {{ background:{t.panel}; alternate-background-color:{t.alternate}; border:0; gridline-color:{t.border}; selection-background-color:{t.selected}; selection-color:{t.heading}; outline:0; }}
    QTableView::item {{ padding:0 10px; border-bottom:1px solid {t.border}; }}
    QTableView::item:hover {{ background:{t.hover}; }}
    QTableView::item:selected {{ background:{t.selected}; color:{t.heading}; }}
    QHeaderView::section {{ color:{t.muted}; background:{t.alternate}; font-size:12px; font-weight:500; border:0; border-bottom:1px solid {t.border}; padding:0 10px; height:42px; }}
    QPlainTextEdit {{ background:{t.panel}; color:{t.text}; border:1px solid {t.border}; border-radius:8px; padding:10px; }}
    QScrollArea {{ border:0; background:transparent; }}
    QScrollArea > QWidget > QWidget {{ background:transparent; }}
    QScrollBar:vertical {{ width:8px; background:transparent; margin:0; }}
    QScrollBar::handle:vertical {{ background:{t.border}; border-radius:4px; min-height:24px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
    QScrollBar:horizontal {{ height:8px; background:transparent; }}
    QScrollBar::handle:horizontal {{ background:{t.border}; border-radius:4px; min-width:24px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
    QSplitter::handle {{ background:{t.border}; width:1px; height:1px; }}
    QTabWidget::pane {{ border:0; background:{t.panel}; }}
    QTabBar::tab {{ color:{t.muted}; background:transparent; padding:10px 12px; border-bottom:2px solid transparent; }}
    QTabBar::tab:selected {{ color:{t.brand}; border-bottom:2px solid {t.brand}; }}
    QMenu {{ background:{t.panel}; color:{t.text}; border:1px solid {t.border}; padding:6px; }}
    QMenu::item {{ padding:7px 18px; border-radius:4px; }}
    QMenu::item:selected {{ background:{t.selected}; color:{t.brand}; }}
    QToolTip {{ color:{t.text}; background:{t.panel}; border:1px solid {t.border}; padding:8px; }}
    """
