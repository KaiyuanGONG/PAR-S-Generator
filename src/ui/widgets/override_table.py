"""
Custom SIMIND Override Table Widget
====================================
Table widget for specifying custom SIMIND CLI parameter overrides.
Each row is a (index, value) pair that maps to ``/<index>:<value>`` CLI args.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.i18n import tr


class OverrideTable(QWidget):
    """Editable table for SIMIND ``/<index>:<value>`` CLI overrides."""

    overrides_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels([
            tr("override_col_index"),
            tr("override_col_value"),
        ])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self._table.setColumnWidth(0, 80)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setMaximumHeight(160)
        self._table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._btn_add = QPushButton(tr("override_add"))
        self._btn_add.setObjectName("override_btn_add")
        self._btn_add.clicked.connect(self._add_row)
        btn_row.addWidget(self._btn_add)

        self._btn_remove = QPushButton(tr("override_remove"))
        self._btn_remove.setObjectName("override_btn_remove")
        self._btn_remove.clicked.connect(self._remove_selected)
        btn_row.addWidget(self._btn_remove)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _add_row(self):
        row = self._table.rowCount()
        self._table.insertRow(row)
        idx_item = QTableWidgetItem("")
        idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, 0, idx_item)
        self._table.setItem(row, 1, QTableWidgetItem(""))
        self._table.setCurrentCell(row, 0)
        self._table.editItem(self._table.item(row, 0))

    def _remove_selected(self):
        rows = sorted(
            {idx.row() for idx in self._table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            self._table.removeRow(row)
        self.overrides_changed.emit()

    def _on_cell_changed(self, row: int, col: int):
        if col == 0:
            item = self._table.item(row, 0)
            if item:
                text = item.text().strip()
                try:
                    val = int(text)
                    if val < 1 or val > 120:
                        raise ValueError
                    item.setBackground(Qt.GlobalColor.transparent)
                except (ValueError, TypeError):
                    if text:
                        from PyQt6.QtGui import QColor
                        item.setBackground(QColor(180, 60, 60, 80))
        self.overrides_changed.emit()

    def get_overrides(self) -> list[tuple[int, str]]:
        """Return validated list of (index, value) tuples."""
        result = []
        for row in range(self._table.rowCount()):
            idx_item = self._table.item(row, 0)
            val_item = self._table.item(row, 1)
            if not idx_item or not val_item:
                continue
            try:
                idx = int(idx_item.text().strip())
                val = val_item.text().strip()
                if 1 <= idx <= 120 and val:
                    result.append((idx, val))
            except (ValueError, TypeError):
                continue
        return result

    def set_overrides(self, overrides: list[tuple[int, str]]):
        """Populate the table from a list of (index, value) tuples."""
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for idx, val in overrides:
            row = self._table.rowCount()
            self._table.insertRow(row)
            idx_item = QTableWidgetItem(str(idx))
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, idx_item)
            self._table.setItem(row, 1, QTableWidgetItem(str(val)))
        self._table.blockSignals(False)

    def retranslate_ui(self):
        self._table.setHorizontalHeaderLabels([
            tr("override_col_index"),
            tr("override_col_value"),
        ])
        self._btn_add.setText(tr("override_add"))
        self._btn_remove.setText(tr("override_remove"))
