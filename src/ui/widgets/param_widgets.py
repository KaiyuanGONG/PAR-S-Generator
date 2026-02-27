"""Reusable parameter input widgets."""

from PyQt6.QtWidgets import (
    QGroupBox, QFormLayout, QSpinBox, QDoubleSpinBox,
    QWidget, QHBoxLayout, QLabel, QVBoxLayout
)
from PyQt6.QtCore import Qt


class ParamGroup(QGroupBox):
    """Styled group box for parameter sections."""

    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self._form = QFormLayout()
        self._form.setSpacing(8)
        self._form.setContentsMargins(8, 12, 8, 8)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._inner = QVBoxLayout(self)
        self._inner.setContentsMargins(0, 0, 0, 0)
        self._inner.addLayout(self._form)

    def add_row(self, label: str, widget: QWidget):
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #8a9099; font-size: 12px;")
        self._form.addRow(lbl, widget)

    def add_widget(self, widget: QWidget):
        self._inner.addWidget(widget)


def SpinRow(label: str, min_val: int, max_val: int, default: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(min_val, max_val)
    spin.setValue(default)
    spin.setMinimumWidth(100)
    return spin


def DoubleSpinRow(label: str, min_val: float, max_val: float,
                  default: float, decimals: int = 2) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(min_val, max_val)
    spin.setValue(default)
    spin.setDecimals(decimals)
    spin.setMinimumWidth(100)
    return spin


def RangeRow(min_val: float, max_val: float,
             default_min: float, default_max: float,
             decimals: int = 2) -> QWidget:
    w = QWidget()
    layout = QHBoxLayout(w)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    spin_min = QDoubleSpinBox()
    spin_min.setRange(min_val, max_val)
    spin_min.setValue(default_min)
    spin_min.setDecimals(decimals)
    sep = QLabel("–")
    sep.setStyleSheet("color: #6b7280;")
    spin_max = QDoubleSpinBox()
    spin_max.setRange(min_val, max_val)
    spin_max.setValue(default_max)
    spin_max.setDecimals(decimals)
    layout.addWidget(spin_min)
    layout.addWidget(sep)
    layout.addWidget(spin_max)
    w.spin_min = spin_min
    w.spin_max = spin_max
    return w


def LabelRow(text: str, style: str = "") -> QLabel:
    lbl = QLabel(text)
    if style:
        lbl.setStyleSheet(style)
    return lbl
