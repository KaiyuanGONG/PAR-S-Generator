"""
Reusable parameter input widgets.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.parameter_specs import NumericParameterSpec
from ui.i18n import language_manager, tr


class _NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):  # noqa: N802
        event.ignore()


class _NoWheelSlider(QSlider):
    def wheelEvent(self, event):  # noqa: N802
        event.ignore()


class _NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):  # noqa: N802
        event.ignore()


class _NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):  # noqa: N802
        event.ignore()


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

        self._header = QHBoxLayout()
        self._header.setContentsMargins(8, 8, 8, 0)
        self._header.addStretch()

        self._description = QLabel("")
        self._description.setObjectName("param_group_desc")
        self._description.setWordWrap(True)
        self._description.setVisible(False)

        self._inner.addLayout(self._header)
        self._inner.addWidget(self._description)
        self._inner.addLayout(self._form)

    def set_header_widget(self, widget: QWidget):
        while self._header.count() > 1:
            item = self._header.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._header.addWidget(widget, alignment=Qt.AlignmentFlag.AlignRight)

    def set_description(self, text: str):
        self._description.setProperty("tr_key", text)
        self._description.setText(tr(text))
        self._description.setVisible(bool(text))

    def add_row(self, label: str, widget: QWidget, tooltip: str | None = None):
        lbl = QLabel(tr(label))
        lbl.setProperty("tr_key", label)
        lbl.setObjectName("param_row_label")
        if tooltip:
            lbl.setProperty("tooltip_key", tooltip)
            translated = tr(tooltip)
            lbl.setToolTip(translated)
            widget.setToolTip(translated)
        self._form.addRow(lbl, widget)

    def retranslate_rows(self):
        desc_key = self._description.property("tr_key")
        if desc_key:
            self._description.setText(tr(str(desc_key)))
        for row in range(self._form.rowCount()):
            item = self._form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is None or item.widget() is None:
                continue
            lbl = item.widget()
            key = lbl.property("tr_key")
            if key:
                lbl.setText(tr(str(key)))
            tooltip = lbl.property("tooltip_key")
            if tooltip:
                translated = tr(str(tooltip))
                lbl.setToolTip(translated)
                field = self._form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                if field is not None and field.widget() is not None:
                    field.widget().setToolTip(translated)

    def add_widget(self, widget: QWidget):
        self._inner.addWidget(widget)


class SliderSpinControl(QWidget):
    valueChanged = pyqtSignal(object)

    def __init__(
        self,
        spec: NumericParameterSpec,
        value: float,
        parent=None,
        discrete_values: list[float] | None = None,
    ):
        super().__init__(parent)
        self.spec = spec
        self._scale = 10 ** spec.decimals
        self._advanced = False
        self._discrete_values = sorted(float(v) for v in (discrete_values or []))
        self._build_ui()
        self.set_advanced(False)
        self.set_value(value)
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.slider = _NoWheelSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self._on_slider)
        layout.addWidget(self.slider, stretch=1)

        if self.spec.is_int:
            self.spin = _NoWheelSpinBox()
        else:
            self.spin = _NoWheelDoubleSpinBox()
            self.spin.setDecimals(self.spec.decimals)
            self.spin.setSingleStep(1 / self._scale)
        self.spin.setMinimumWidth(92)
        self.spin.valueChanged.connect(self._on_spin)
        layout.addWidget(self.spin)
        self.retranslate_ui()

    def retranslate_ui(self):
        tooltip = tr(self.spec.description)
        self.setToolTip(tooltip)
        self.slider.setToolTip(tooltip)
        self.spin.setToolTip(tooltip)

    def _is_discrete_mode(self) -> bool:
        return bool(self._discrete_values) and not self._advanced

    def _closest_discrete(self, value: float) -> float:
        return min(self._discrete_values, key=lambda item: abs(item - value))

    def _coerce(self, value: float) -> float:
        if self._is_discrete_mode():
            return self._closest_discrete(value)
        lower = self.spec.hard_min if self._advanced else self.spec.recommended_min
        upper = self.spec.hard_max if self._advanced else self.spec.recommended_max
        return min(max(value, lower), upper)

    def _set_bounds(self, min_val: float, max_val: float):
        self.slider.blockSignals(True)
        self.slider.setRange(int(round(min_val * self._scale)), int(round(max_val * self._scale)))
        self.slider.blockSignals(False)
        if self.spec.is_int:
            self.spin.setRange(int(min_val), int(max_val))
        else:
            self.spin.setRange(min_val, max_val)

    def _apply_mode(self):
        if self._is_discrete_mode():
            self.slider.blockSignals(True)
            self.slider.setRange(0, max(len(self._discrete_values) - 1, 0))
            self.slider.blockSignals(False)
            min_val = min(self._discrete_values)
            max_val = max(self._discrete_values)
            if self.spec.is_int:
                self.spin.setRange(int(min_val), int(max_val))
            else:
                self.spin.setRange(min_val, max_val)
            self.spin.setReadOnly(True)
        else:
            if self._advanced:
                self._set_bounds(self.spec.hard_min, self.spec.hard_max)
            else:
                self._set_bounds(self.spec.recommended_min, self.spec.recommended_max)
            self.spin.setReadOnly(False)

    def set_advanced(self, enabled: bool):
        self._advanced = enabled
        self._apply_mode()
        self.set_value(float(self.spin.value()))

    def _on_slider(self, raw: int):
        if self._is_discrete_mode():
            if not self._discrete_values:
                return
            index = min(max(raw, 0), len(self._discrete_values) - 1)
            value = self._discrete_values[index]
        else:
            value = self._coerce(raw / self._scale)

        self.spin.blockSignals(True)
        if self.spec.is_int:
            value = int(round(value))
            self.spin.setValue(value)
        else:
            self.spin.setValue(value)
        self.spin.blockSignals(False)
        self.valueChanged.emit(value)

    def _on_spin(self, value):
        numeric = self._coerce(float(value))

        self.spin.blockSignals(True)
        if self.spec.is_int:
            numeric = int(round(numeric))
            self.spin.setValue(numeric)
        else:
            self.spin.setValue(numeric)
        self.spin.blockSignals(False)

        self.slider.blockSignals(True)
        if self._is_discrete_mode():
            closest = self._closest_discrete(float(numeric))
            self.slider.setValue(self._discrete_values.index(closest))
            numeric = int(round(closest)) if self.spec.is_int else closest
        else:
            self.slider.setValue(int(round(float(numeric) * self._scale)))
        self.slider.blockSignals(False)
        self.valueChanged.emit(numeric)

    def value(self):
        return self.spin.value()

    def set_value(self, value):
        numeric = self._coerce(float(value))
        if self.spec.is_int:
            numeric = int(round(numeric))

        self.spin.blockSignals(True)
        self.spin.setValue(numeric)
        self.spin.blockSignals(False)

        self.slider.blockSignals(True)
        if self._is_discrete_mode():
            closest = self._closest_discrete(float(numeric))
            self.slider.setValue(self._discrete_values.index(closest))
            self.spin.blockSignals(True)
            self.spin.setValue(int(round(closest)) if self.spec.is_int else closest)
            self.spin.blockSignals(False)
        else:
            self.slider.setValue(int(round(float(numeric) * self._scale)))
        self.slider.blockSignals(False)


class EnumControl(QWidget):
    valueChanged = pyqtSignal(str)

    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = _NoWheelComboBox()
        for data, label in items:
            self.combo.addItem(label, data)
        self.combo.currentIndexChanged.connect(lambda _: self.valueChanged.emit(self.value()))
        layout.addWidget(self.combo)

    def value(self) -> str:
        return str(self.combo.currentData())

    def set_value(self, value: str):
        idx = self.combo.findData(value)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)

    def clear_and_set_items(self, items: list[tuple[str, str]]):
        current = self.value()
        self.combo.clear()
        for data, label in items:
            self.combo.addItem(label, data)
        self.set_value(current)


class VolumePresetControl(QWidget):
    presetChanged = pyqtSignal(int, float)

    def __init__(self, presets: list[tuple[int, float]], parent=None):
        super().__init__(parent)
        self._presets = presets
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.combo = _NoWheelComboBox()
        self._rebuild_items()
        self.combo.currentIndexChanged.connect(self._emit_change)
        layout.addWidget(self.combo)
        language_manager().language_changed.connect(lambda _: self.retranslate_ui())

    def _emit_change(self):
        current = self.combo.currentData()
        if current is None:
            return
        self.presetChanged.emit(*self.value())

    def value(self) -> tuple[int, float]:
        matrix, voxel = self.combo.currentData()
        return int(matrix), float(voxel)

    def set_value(self, matrix: int, voxel: float):
        for idx, (m, v) in enumerate(self._presets):
            if m == matrix and abs(v - voxel) < 1e-2:
                self.combo.setCurrentIndex(idx)
                return

    def _rebuild_items(self):
        current = self.combo.currentData() if hasattr(self, "combo") else None
        self.combo.blockSignals(True)
        self.combo.clear()
        for matrix, voxel in self._presets:
            self.combo.addItem(f"{matrix} / {voxel:.2f} {tr('mm')}", (matrix, voxel))
        if current is not None:
            for idx in range(self.combo.count()):
                if self.combo.itemData(idx) == current:
                    self.combo.setCurrentIndex(idx)
                    break
        self.combo.blockSignals(False)

    def retranslate_ui(self):
        self._rebuild_items()


class LabeledCheck(QCheckBox):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("param_advanced_toggle")


def LabelRow(text: str, style: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setObjectName("param_note")
    if style:
        lbl.setStyleSheet(style)
    return lbl
