"""
SMC Parameter Viewer Widget
============================
Read-only scrollable panel showing parsed .smc configuration parameters
organized by category with human-readable labels.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.smc_parser import (
    SMC_CATEGORIES,
    SMC_FLAG_LABELS,
    SMC_PARAM_LABELS,
    SmcData,
    parse_smc,
)
from ui.i18n import tr


class SmcViewer(QWidget):
    """Read-only viewer for SIMIND .smc configuration parameters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._smc_data: SmcData | None = None
        self._value_labels: dict[int, QLabel] = {}
        self._flag_labels: dict[int, QLabel] = {}
        self._category_groups: list[QGroupBox] = []
        self._header_label = QLabel()
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Header bar with filename
        self._header_label.setObjectName("smc_viewer_header")
        self._header_label.setText(tr("smc_no_file_loaded"))
        self._header_label.setWordWrap(True)
        outer.addWidget(self._header_label)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(4, 4, 4, 4)
        self._content_layout.setSpacing(8)

        # Build category groups for values
        for cat_name, indices in SMC_CATEGORIES:
            group = QGroupBox(tr(f"smc_cat_{cat_name}"))
            group.setProperty("tr_key", f"smc_cat_{cat_name}")
            self._category_groups.append(group)
            form = QFormLayout(group)
            form.setSpacing(4)
            form.setContentsMargins(8, 12, 8, 8)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

            for idx in indices:
                label_text, _desc = SMC_PARAM_LABELS.get(idx, (f"Index {idx}", ""))
                lbl = QLabel(f"[{idx}] {label_text}")
                lbl.setObjectName("smc_param_label")
                val_lbl = QLabel("--")
                val_lbl.setObjectName("smc_param_value")
                val_lbl.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                self._value_labels[idx] = val_lbl
                form.addRow(lbl, val_lbl)

            self._content_layout.addWidget(group)

        # Flags group
        flags_group = QGroupBox(tr("smc_cat_Flags"))
        flags_group.setProperty("tr_key", "smc_cat_Flags")
        self._category_groups.append(flags_group)
        flags_form = QFormLayout(flags_group)
        flags_form.setSpacing(4)
        flags_form.setContentsMargins(8, 12, 8, 8)
        flags_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for idx in sorted(SMC_FLAG_LABELS.keys()):
            lbl = QLabel(f"[{idx}] {SMC_FLAG_LABELS[idx]}")
            lbl.setObjectName("smc_param_label")
            val_lbl = QLabel("--")
            val_lbl.setObjectName("smc_param_value")
            self._flag_labels[idx] = val_lbl
            flags_form.addRow(lbl, val_lbl)
        self._content_layout.addWidget(flags_group)

        # Data files group
        self._files_group = QGroupBox(tr("smc_cat_DataFiles"))
        self._files_group.setProperty("tr_key", "smc_cat_DataFiles")
        self._category_groups.append(self._files_group)
        self._files_layout = QVBoxLayout(self._files_group)
        self._files_layout.setContentsMargins(8, 12, 8, 8)
        self._files_label = QLabel("--")
        self._files_label.setWordWrap(True)
        self._files_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._files_layout.addWidget(self._files_label)
        self._content_layout.addWidget(self._files_group)

        self._content_layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

    def load_smc(self, path: str | Path) -> SmcData | None:
        """Parse and display an .smc file. Returns SmcData on success."""
        path = Path(path)
        try:
            data = parse_smc(path)
        except (FileNotFoundError, ValueError, OSError):
            self._header_label.setText(
                tr("smc_load_error").format(path=path.name)
            )
            return None

        self._smc_data = data
        self._header_label.setText(
            tr("smc_loaded").format(name=path.name, desc=data.description)
        )

        # Populate values
        for idx, lbl in self._value_labels.items():
            try:
                val = data.get_value(idx)
                # Format integers without decimals
                if val == int(val) and abs(val) < 1e9:
                    lbl.setText(str(int(val)))
                else:
                    lbl.setText(f"{val:.6g}")
            except IndexError:
                lbl.setText("--")

        # Populate flags
        for idx, lbl in self._flag_labels.items():
            try:
                flag = data.get_flag(idx)
                lbl.setText("ON" if flag else "OFF")
                lbl.setStyleSheet(
                    "color: #4CAF50;" if flag else "color: #888;"
                )
            except IndexError:
                lbl.setText("--")

        # Populate data files
        if data.data_files:
            files_text = "\n".join(
                f"  [{i + 1}] {f}" for i, f in enumerate(data.data_files)
            )
            self._files_label.setText(files_text)
        else:
            self._files_label.setText("--")

        return data

    def clear(self):
        """Reset all displayed values."""
        self._smc_data = None
        self._header_label.setText(tr("smc_no_file_loaded"))
        for lbl in self._value_labels.values():
            lbl.setText("--")
        for lbl in self._flag_labels.values():
            lbl.setText("--")
            lbl.setStyleSheet("")
        self._files_label.setText("--")

    @property
    def smc_data(self) -> SmcData | None:
        return self._smc_data

    def retranslate_ui(self):
        if self._smc_data is None:
            self._header_label.setText(tr("smc_no_file_loaded"))
        for group in self._category_groups:
            key = group.property("tr_key")
            if key:
                group.setTitle(tr(str(key)))
