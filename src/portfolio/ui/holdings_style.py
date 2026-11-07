"""Scoped holdings presentation, using the user's existing profit/loss tokens."""
STYLE='''
QWidget#holdingsManager { background:#F5F7FB; color:#1E293B; font-size:13px; }
QWidget#holdingsManager QFrame#filterCard, QWidget#holdingsManager QFrame#holdingSurface { background:white; border:1px solid #E8EDF5; border-radius:12px; }
QWidget#holdingsManager QLabel { border:0; background:transparent; }
QWidget#holdingsManager QLabel#fieldCaption { color:#64748B; font-size:12px; }
QWidget#holdingsManager QPushButton { min-height:20px; padding:7px 12px; border:1px solid #E2E8F0; border-radius:7px; background:white; color:#334155; }
QWidget#holdingsManager QPushButton:hover { background:#EFF6FF; border-color:#BFDBFE; }
QWidget#holdingsManager QPushButton#primary { background:#2563EB; color:white; border-color:#2563EB; font-weight:600; }
QWidget#holdingsManager QPushButton#quietAction { color:#2563EB; border:0; background:transparent; }
QWidget#holdingsManager QPushButton#holdingChip { background:#EBF2FF; border:0; color:#2563EB; min-height:16px; padding:4px 8px; font-size:11px; }
QWidget#holdingsManager QComboBox, QWidget#holdingsManager QLineEdit { min-height:20px; padding:6px 9px; border:1px solid #E2E8F0; border-radius:7px; background:white; }
QWidget#holdingsManager QTableView { border:0; background:white; alternate-background-color:#FAFCFF; selection-background-color:#EAF2FF; selection-color:#1E293B; font-size:13px; }
QWidget#holdingsManager QTableView::item { border-bottom:1px solid #F0F3F8; padding:0 10px; }
QWidget#holdingsManager QTableView::item:hover { background:#F0F6FF; }
QWidget#holdingsManager QHeaderView::section { background:#F1F5FB; color:#64748B; border:0; border-bottom:1px solid #E8EDF5; padding:0 10px; font-size:12px; }
QWidget#holdingsManager QTabBar::tab { background:#F1F5FB; color:#64748B; padding:9px 10px; border:0; }
QWidget#holdingsManager QTabBar::tab:selected { background:#EFF6FF; color:#2563EB; border-bottom:2px solid #2563EB; }
'''
