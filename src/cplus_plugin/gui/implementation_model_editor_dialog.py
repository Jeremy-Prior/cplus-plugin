# -*- coding: utf-8 -*-
"""
Dialog for creating or editing an implementation model.
"""

import os
import typing
import uuid

from qgis.core import (
    Qgis,
    QgsColorRamp,
    QgsFillSymbol,
    QgsFillSymbolLayer,
    QgsMapLayerProxyModel,
    QgsRasterLayer,
)
from qgis.gui import QgsGui, QgsMessageBar

from qgis.PyQt import QtGui, QtWidgets

from qgis.PyQt.uic import loadUiType

from ..conf import Settings, settings_manager
from ..definitions.constants import (
    COLOR_RAMP_PROPERTIES_ATTRIBUTE,
    COLOR_RAMP_TYPE_ATTRIBUTE,
    IM_LAYER_STYLE_ATTRIBUTE,
    IM_SCENARIO_STYLE_ATTRIBUTE,
)
from ..definitions.defaults import ICON_PATH, USER_DOCUMENTATION_SITE
from ..models.base import ImplementationModel
from ..utils import FileUtils, open_documentation, tr

WidgetUi, _ = loadUiType(
    os.path.join(
        os.path.dirname(__file__), "../ui/implementation_model_editor_dialog.ui"
    )
)


class ImplementationModelEditorDialog(QtWidgets.QDialog, WidgetUi):
    """Dialog for creating or editing an implementation model entry."""

    def __init__(self, parent=None, implementation_model=None, excluded_names=None):
        super().__init__(parent)
        self.setupUi(self)

        QgsGui.enableAutoGeometryRestore(self)

        self._message_bar = QgsMessageBar()
        self.vl_notification.addWidget(self._message_bar)

        self.style_btn.setSymbolType(Qgis.SymbolType.Fill)

        self.btn_color_ramp.setShowNull(False)
        self.btn_color_ramp.setRandomColorRamp()
        self.btn_color_ramp.setColorRampDialogTitle(
            self.tr("Set Color Ramp for Output Implementation Model")
        )

        self.buttonBox.accepted.connect(self._on_accepted)
        self.btn_select_file.clicked.connect(self._on_select_file)
        self.btn_help.clicked.connect(self.open_help)

        icon_pixmap = QtGui.QPixmap(ICON_PATH)
        self.icon_la.setPixmap(icon_pixmap)

        self.cbo_layer.setFilters(QgsMapLayerProxyModel.Filter.RasterLayer)

        self._edit_mode = False
        self._layer = None

        self._excluded_names = excluded_names
        if excluded_names is None:
            self._excluded_names = []

        self._implementation_model = implementation_model
        if self._implementation_model is not None:
            self._edit_mode = True
            self._layer = self._implementation_model.to_map_layer()
            self._update_controls()

        help_icon = FileUtils.get_icon("mActionHelpContents.svg")
        self.btn_help.setIcon(help_icon)

        self.txt_description.textChanged.connect(self.description_changed)

    @property
    def implementation_model(self) -> ImplementationModel:
        """Returns a reference to the ImplementationModel object.

        :returns: Reference to the ImplementationModel object.
        :rtype: ImplementationModel
        """
        return self._implementation_model

    @property
    def edit_mode(self) -> bool:
        """Returns the state of the editor.

        :returns: True if the editor is editing an existing
        ImplementationModel object, else False if its creating
        a new object.
        :rtype: bool
        """
        return self._edit_mode

    @property
    def layer(self) -> QgsRasterLayer:
        """Returns the raster layer specified by the user,
        either existing layers in the map canvas or from the
        selected file.

        :returns: The raster layer specified by the user or
        None if not set.
        :rtype: QgsRasterLayer
        """
        return self._layer

    def description_changed(self):
        """Slot to handle description text changes, it currently
        limits the number of characters to only be 300 characters
        per description
        """

        description = self.txt_description.toPlainText()
        if len(description) > 300:
            self.txt_description.setPlainText(description[:300])

    def _update_controls(self):
        """Update controls with data from the ImplementationModel
        object.
        """
        if self._implementation_model is None:
            return

        self.txt_name.setText(self._implementation_model.name)
        self.txt_description.setPlainText(self._implementation_model.description)

        self.layer_gb.setCollapsed(True)

        if len(self._implementation_model.pathways) > 0:
            self.layer_gb.setEnabled(False)
        else:
            self.layer_gb.setEnabled(True)

        if self._layer:
            self.layer_gb.setCollapsed(False)
            self.layer_gb.setChecked(True)

            layer_path = self._layer.source()
            self._add_layer_path(layer_path)

        # Set scenario fill style
        symbol = self._implementation_model.scenario_fill_symbol()
        if symbol:
            self.style_btn.setSymbol(symbol)

        # Set output layer color ramp
        output_model_color_ramp = self._implementation_model.model_color_ramp()
        if output_model_color_ramp:
            self.btn_color_ramp.setColorRamp(output_model_color_ramp)

    def _add_layer_path(self, layer_path: str):
        """Select or add layer path to the map layer combobox."""
        matching_index = -1
        num_layers = self.cbo_layer.count()
        for index in range(num_layers):
            layer = self.cbo_layer.layer(index)
            if layer is None:
                continue
            if os.path.normpath(layer.source()) == os.path.normpath(layer_path):
                matching_index = index
                break

        if matching_index == -1:
            self.cbo_layer.setAdditionalItems([layer_path])
            # Set added path as current item
            self.cbo_layer.setCurrentIndex(num_layers)
        else:
            self.cbo_layer.setCurrentIndex(matching_index)

    def validate(self) -> bool:
        """Validates if name has been specified.

        :returns: True if the name have been set.
        :rtype: True
        """
        status = True

        self._message_bar.clearWidgets()

        name = self.txt_name.text()
        if not name:
            msg = tr("Implementation model name cannot be empty.")
            self._show_warning_message(msg)
            status = False

        if name.lower() in self._excluded_names:
            msg = tr("name has already been used.")
            self._show_warning_message(f"'{name}' {msg}")
            status = False

        if not self.txt_description.toPlainText():
            msg = tr("Description cannot be empty.")
            self._show_warning_message(msg)
            status = False

        layer = self._get_selected_map_layer()
        if layer and not layer.isValid():
            msg = tr("Map layer is not valid.")
            self._show_warning_message(msg)
            status = False

        fill_symbol_layer = self.scenario_fill_symbol_layer()
        if fill_symbol_layer is None:
            msg = tr("No fill symbol defined for the scenario layer.")
            self._show_warning_message(msg)
            status = False

        output_model_color_ramp = self.btn_color_ramp.colorRamp()
        if output_model_color_ramp is None:
            msg = tr("No color ramp defined for the output model layer.")
            self._show_warning_message(msg)
            status = False

        return status

    def _show_warning_message(self, message):
        """Shows a warning message in the message bar."""
        self._message_bar.pushMessage(message, Qgis.MessageLevel.Warning)

    def _create_implementation_model(self):
        """Create or update implementation model from user input."""
        if self._implementation_model is None:
            self._implementation_model = ImplementationModel(
                uuid.uuid4(), self.txt_name.text(), self.txt_description.toPlainText()
            )
            self._implementation_model.user_defined = True
        else:
            # Update mode
            self._implementation_model.name = self.txt_name.text()
            self._implementation_model.description = self.txt_description.toPlainText()

        self._layer = self._get_selected_map_layer()

        scenario_fill_symbol_layer = self.scenario_fill_symbol_layer()
        self._implementation_model.layer_styles = {}
        if scenario_fill_symbol_layer:
            self._implementation_model.layer_styles[
                IM_SCENARIO_STYLE_ATTRIBUTE
            ] = scenario_fill_symbol_layer.properties()

        output_model_color_ramp = self.btn_color_ramp.colorRamp()
        if output_model_color_ramp:
            color_ramp_info = {
                COLOR_RAMP_PROPERTIES_ATTRIBUTE: output_model_color_ramp.properties(),
                COLOR_RAMP_TYPE_ATTRIBUTE: output_model_color_ramp.typeString(),
            }
            self._implementation_model.layer_styles[
                IM_LAYER_STYLE_ATTRIBUTE
            ] = color_ramp_info

    def _get_selected_map_layer(self) -> typing.Union[QgsRasterLayer, None]:
        """Returns the currently selected map layer or None if there is
        no item in the combobox.
        """
        if not self.layer_gb.isChecked():
            return None

        layer = self.cbo_layer.currentLayer()

        if layer is None:
            layer_paths = self.cbo_layer.additionalItems()
            current_path = self.cbo_layer.currentText()
            if current_path in layer_paths:
                layer_name = os.path.basename(current_path)
                layer = QgsRasterLayer(current_path, layer_name)

        return layer

    def _on_accepted(self):
        """Validates user input before closing."""
        if not self.validate():
            return

        self._create_implementation_model()
        self.accept()

    def scenario_fill_symbol_layer(self) -> QgsFillSymbolLayer:
        """Gets the first fill symbol layer in the symbol as
        set in the button.

        It checks to ensure that there is at least one fill symbol
        layer contained in the symbol.

        :returns: Fill symbol layer to be used in the implementation
        model.
        :rtype: QgsFillSymbolLayer
        """
        fill_symbol_layer = None
        btn_symbol = self.style_btn.symbol()

        for i in range(btn_symbol.symbolLayerCount()):
            symbol_layer = btn_symbol.symbolLayer(i)
            if isinstance(symbol_layer, QgsFillSymbolLayer):
                fill_symbol_layer = symbol_layer
                break

        return fill_symbol_layer

    def output_layer_color_ramp(self) -> QgsColorRamp:
        """Returns the selected color ramp.

        :returns: The color ramp selected by the user.
        :rtype: QgsColorRamp
        """
        color_ramp = self.btn_color_ramp.colorRamp()

        return color_ramp

    def open_help(self, activated: bool):
        """Opens the user documentation for the plugin in a browser."""
        open_documentation(USER_DOCUMENTATION_SITE)

    def _on_select_file(self, activated: bool):
        """Slot raised to upload a raster layer."""
        data_dir = settings_manager.get_value(Settings.LAST_DATA_DIR, "")
        if not data_dir and self._layer:
            data_path = self._layer.source()
            if os.path.exists(data_path):
                data_dir = os.path.dirname(data_path)

        if not data_dir:
            data_dir = "/home"

        filter_tr = tr("All files")

        layer_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.tr("Select Implementation Model Layer"),
            data_dir,
            f"{filter_tr} (*.*)",
            options=QtWidgets.QFileDialog.DontResolveSymlinks,
        )
        if not layer_path:
            return

        existing_paths = self.cbo_layer.additionalItems()
        if layer_path in existing_paths:
            return

        self.cbo_layer.setAdditionalItems([])

        self._add_layer_path(layer_path)
        settings_manager.set_value(Settings.LAST_DATA_DIR, os.path.dirname(layer_path))
