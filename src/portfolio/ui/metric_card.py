"""Metric cards on macOS dashboard without redundant hover tooltips."""
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QPushButton


class MetricCard(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            event.accept()
            return True
        return super().event(event)
