# -*- coding: utf-8 -*-

"""
 The plugin main window class.
"""

import os
import typing
import uuid

import datetime

from functools import partial

from pathlib import Path

from qgis.PyQt import (
    QtCore,
    QtGui,
    QtWidgets,
    QtNetwork,
)

from qgis.PyQt.uic import loadUiType

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeedback,
    QgsGeometry,
    QgsProject,
    QgsProcessing,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsRasterLayer,
    QgsRectangle,
    QgsTask,
    QgsWkbTypes,
    QgsColorRampShader,
    QgsSingleBandPseudoColorRenderer,
    QgsRasterShader,
    QgsPalettedRasterRenderer,
    QgsStyle,
    QgsRasterMinMaxOrigin,
)

from qgis.gui import (
    QgsMessageBar,
    QgsRubberBand,
)

from qgis.utils import iface

from .implementation_model_widget import ImplementationModelContainerWidget
from .priority_group_widget import PriorityGroupWidget

from .priority_layer_dialog import PriorityLayerDialog
from .priority_group_dialog import PriorityGroupDialog

from ..models.base import Scenario, ScenarioResult, ScenarioState, SpatialExtent
from ..conf import settings_manager, Settings

from ..lib.extent_check import extent_within_pilot
from ..lib.reports.manager import report_manager, ReportManager
from ..models.helpers import clone_implementation_model

from ..tasks import ScenarioAnalysisTask

from .components.custom_tree_widget import CustomTreeWidget

from ..resources import *

from ..utils import (
    align_rasters,
    clean_filename,
    open_documentation,
    tr,
    log,
    FileUtils,
)

from ..definitions.defaults import (
    ADD_LAYER_ICON_PATH,
    PILOT_AREA_EXTENT,
    PRIORITY_LAYERS,
    OPTIONS_TITLE,
    ICON_PATH,
    QGIS_GDAL_PROVIDER,
    REMOVE_LAYER_ICON_PATH,
    SCENARIO_OUTPUT_FILE_NAME,
    SCENARIO_OUTPUT_LAYER_NAME,
    USER_DOCUMENTATION_SITE,
)
from ..definitions.constants import (
    IM_GROUP_LAYER_NAME,
    IM_WEIGHTED_GROUP_NAME,
    MODEL_IDENTIFIER_PROPERTY,
    NCS_PATHWAYS_GROUP_LAYER_NAME,
    USER_DEFINED_ATTRIBUTE,
)

from .progress_dialog import ProgressDialog

WidgetUi, _ = loadUiType(
    os.path.join(os.path.dirname(__file__), "../ui/qgis_cplus_main_dockwidget.ui")
)


class QgisCplusMain(QtWidgets.QDockWidget, WidgetUi):
    """Main plugin UI class"""

    analysis_finished = QtCore.pyqtSignal(ScenarioResult)

    def __init__(
        self,
        iface,
        parent=None,
    ):
        super().__init__(parent)
        self.setupUi(self)
        self.iface = iface
        self.progress_dialog = None
        self.task = None
        self.processing_cancelled = False

        self.prepare_input()

        # Insert widget for step 2
        self.implementation_model_widget = ImplementationModelContainerWidget(
            self, self.message_bar
        )
        self.implementation_model_widget.ncs_reloaded.connect(
            self.on_ncs_pathways_reloaded
        )
        self.tab_widget.insertTab(
            1, self.implementation_model_widget, self.tr("Step 2")
        )
        self.tab_widget.currentChanged.connect(self.on_tab_step_changed)

        # Step 3, priority weighting layers initialization
        self.priority_groups_widgets = {}
        self.pwl_item_flags = None

        self.initialize_priority_layers()

        self.position_feedback = QgsProcessingFeedback()
        self.processing_context = QgsProcessingContext()

        self.scenario_result = None

        self.analysis_finished.connect(self.post_analysis)

    def prepare_input(self):
        """Initializes plugin input widgets"""
        self.prepare_extent_box()
        self.grid_layout = QtWidgets.QGridLayout()
        self.message_bar = QgsMessageBar()
        self.prepare_message_bar()

        self.progress_dialog = None
        self.scenario_directory = None

        self.help_btn.clicked.connect(self.open_help)
        self.pilot_area_btn.clicked.connect(self.zoom_pilot_area)

        self.run_scenario_btn.clicked.connect(self.run_analysis)
        self.options_btn.clicked.connect(self.open_settings)

        self.restore_scenario()

        self.scenario_name.textChanged.connect(self.save_scenario)
        self.scenario_description.textChanged.connect(self.save_scenario)
        self.extent_box.extentChanged.connect(self.save_scenario)

        # Monitors if current extents are within the pilot AOI
        self.extent_box.extentChanged.connect(self.on_extent_changed)

        icon_pixmap = QtGui.QPixmap(ICON_PATH)
        self.icon_la.setPixmap(icon_pixmap)

        add_layer_icon = QtGui.QIcon(ADD_LAYER_ICON_PATH)
        self.layer_add_btn.setIcon(add_layer_icon)

        remove_layer_icon = QtGui.QIcon(REMOVE_LAYER_ICON_PATH)
        self.layer_remove_btn.setIcon(remove_layer_icon)

        self.layer_add_btn.clicked.connect(self.add_priority_layer_group)
        self.layer_remove_btn.clicked.connect(self.remove_priority_layer_group)

        # Priority groups buttons
        self.add_group_btn.setIcon(FileUtils.get_icon("symbologyAdd.svg"))
        self.edit_group_btn.setIcon(FileUtils.get_icon("mActionToggleEditing.svg"))
        self.remove_group_btn.setIcon(FileUtils.get_icon("symbologyRemove.svg"))

        self.add_group_btn.clicked.connect(self.add_priority_group)
        self.edit_group_btn.clicked.connect(self.edit_priority_group)
        self.remove_group_btn.clicked.connect(self.remove_priority_group)

        # Priority layers buttons
        self.add_pwl_btn.setIcon(FileUtils.get_icon("symbologyAdd.svg"))
        self.edit_pwl_btn.setIcon(FileUtils.get_icon("mActionToggleEditing.svg"))
        self.remove_pwl_btn.setIcon(FileUtils.get_icon("symbologyRemove.svg"))

        self.add_pwl_btn.clicked.connect(self.add_priority_layer)
        self.edit_pwl_btn.clicked.connect(self.edit_priority_layer)
        self.remove_pwl_btn.clicked.connect(self.remove_priority_layer)

        self.priority_layers_list.itemDoubleClicked.connect(
            self._on_double_click_priority_layer
        )

        # Add priority groups list into the groups frame
        self.priority_groups_list = CustomTreeWidget()

        self.priority_groups_list.setHeaderHidden(True)

        self.priority_groups_list.setDragEnabled(True)
        self.priority_groups_list.setDragDropOverwriteMode(True)
        self.priority_groups_list.viewport().setAcceptDrops(True)

        self.priority_groups_list.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)

        self.priority_groups_list.child_dragged_dropped.connect(
            self.priority_groups_update
        )

        layout = QtWidgets.QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self.priority_groups_list)
        self.priority_groups_frame.setLayout(layout)

        # Scenario analysis variables

        self.analysis_scenario_name = None
        self.analysis_scenario_description = None
        self.analysis_extent = None
        self.analysis_implementation_models = None
        self.analysis_weighted_ims = []
        self.analysis_priority_layers_groups = []

    def priority_groups_update(self, target_item, selected_items):
        """Updates the priority groups list item with the passed
         selected layer items.

        :param target_item: The priority group tree widget
         item that is to be updated
        :type target_item: QTreeWidgetItem

        :param selected_items: Priority layers items from the list widget
        :type selected_items: list
        """
        self.priority_groups_list.setCurrentItem(target_item)

        for item in selected_items:
            self.add_priority_layer_group(target_item, item)

    def update_pwl_layers(self, notify=False):
        """Updates the priority layers path available in
        the store implementation models

        :param notify: Whether to show message to user about the update
        :type notify: bool
        """
        settings_manager.update_implementation_models()
        self.update_priority_layers()
        if notify:
            self.show_message(
                tr(
                    "Updated all the implementation models"
                    " with their respective priority layers"
                ),
                Qgis.Info,
            )
        log(
            tr(
                "Updated all the implementation models"
                " with their respective priority layers"
            )
        )

    def save_scenario(self):
        """Save current scenario details into settings"""
        scenario_name = self.scenario_name.text()
        scenario_description = self.scenario_description.text()
        extent = self.extent_box.outputExtent()

        extent_box = [
            extent.xMinimum(),
            extent.xMaximum(),
            extent.yMinimum(),
            extent.yMaximum(),
        ]

        settings_manager.set_value(Settings.SCENARIO_NAME, scenario_name)
        settings_manager.set_value(Settings.SCENARIO_DESCRIPTION, scenario_description)
        settings_manager.set_value(Settings.SCENARIO_EXTENT, extent_box)

    def restore_scenario(self):
        """Update the first tab input with the last scenario details"""
        scenario_name = settings_manager.get_value(Settings.SCENARIO_NAME)
        scenario_description = settings_manager.get_value(Settings.SCENARIO_DESCRIPTION)
        extent = settings_manager.get_value(Settings.SCENARIO_EXTENT)

        self.scenario_name.setText(scenario_name) if scenario_name is not None else None
        self.scenario_description.setText(
            scenario_description
        ) if scenario_description is not None else None

        if extent is not None:
            extent_rectangle = QgsRectangle(
                float(extent[0]), float(extent[2]), float(extent[1]), float(extent[3])
            )
            self.extent_box.setOutputExtentFromUser(
                extent_rectangle,
                QgsCoordinateReferenceSystem("EPSG:4326"),
            )

    def initialize_priority_layers(self):
        """Prepares the priority weighted layers UI with the defaults.

        Gets the store priority layers from plugin settings and populates
        them into the QListWidget as QListWidgetItems then fetches the
        priority groups and adds them to the QTreeWidget as QTreeWidgetItems
        with their corresponding priority layers as their child items.
        """
        self.priority_layers_list.clear()

        for layer in settings_manager.get_priority_layers():
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.DisplayRole, layer.get("name"))
            item.setData(QtCore.Qt.UserRole, layer.get("uuid"))

            if self.pwl_item_flags is None:
                self.pwl_item_flags = item.flags()

            self.priority_layers_list.addItem(item)

        list_items = []
        items_only = []
        stored_priority_groups = settings_manager.get_priority_groups()
        self.priority_groups_list.clear()

        for group in stored_priority_groups:
            group_widget = PriorityGroupWidget(
                group,
            )
            group_widget.input_value_changed.connect(self.group_value_changed)
            group_widget.slider_value_changed.connect(self.group_value_changed)

            self.priority_groups_widgets[group["name"]] = group_widget

            pw_layers = settings_manager.find_layers_by_group(group["name"])

            item = QtWidgets.QTreeWidgetItem()
            item.setSizeHint(0, group_widget.sizeHint())
            item.setExpanded(True)
            item.setData(0, QtCore.Qt.UserRole, group.get("uuid"))

            # Add priority layers into the group as a child items.

            item.setExpanded(True) if len(pw_layers) > 0 else None

            for layer in pw_layers:
                if item.parent() is None:
                    layer_item = QtWidgets.QTreeWidgetItem(item)
                    layer_item.setText(0, layer.get("name"))
                    layer_item.setData(
                        0, QtCore.Qt.UserRole, layer.get(USER_DEFINED_ATTRIBUTE)
                    )

            list_items.append((item, group_widget))
            items_only.append(item)

        self.priority_groups_list.addTopLevelItems(items_only)
        for item in list_items:
            self.priority_groups_list.setItemWidget(item[0], 0, item[1])

        # Trigger process to enable/disable PWLs based on current extents
        self.on_extent_changed(self.extent_box.outputExtent())

    def on_ncs_pathways_reloaded(self):
        """Slot raised when NCS pathways have been reloaded in the view."""
        within_pilot_area = extent_within_pilot(
            self.extent_box.outputExtent(), self.extent_box.outputCrs()
        )
        self.implementation_model_widget.enable_default_items(within_pilot_area)

    def on_extent_changed(self, new_extent: QgsRectangle):
        """Slot raised when scenario extents have changed.

        Used to enable/disable default model items if they are within or
        outside the pilot AOI.
        """
        within_pilot_area = extent_within_pilot(new_extent, self.extent_box.outputCrs())

        if not within_pilot_area:
            msg = tr(
                "Area of interest is outside the pilot area. Please use your "
                "own NCS pathways, implementation models and PWLs."
            )
            self.show_message(msg, Qgis.Info)

        else:
            self.message_bar.clearWidgets()

        self.implementation_model_widget.enable_default_items(within_pilot_area)

        # Enable/disable PWL items
        for i in range(self.priority_layers_list.count()):
            pwl_item = self.priority_layers_list.item(i)
            uuid_str = pwl_item.data(QtCore.Qt.UserRole)
            if not uuid_str:
                continue

            pwl_uuid = uuid.UUID(uuid_str)
            pwl = settings_manager.get_priority_layer(pwl_uuid)
            if USER_DEFINED_ATTRIBUTE not in pwl:
                continue

            is_user_defined = pwl.get(USER_DEFINED_ATTRIBUTE)
            if is_user_defined:
                continue

            if within_pilot_area:
                pwl_item.setFlags(self.pwl_item_flags)
            else:
                pwl_item.setFlags(QtCore.Qt.NoItemFlags)

        # Enable/disable PWL items already defined under the priority groups
        for i in range(self.priority_groups_list.topLevelItemCount()):
            group_item = self.priority_groups_list.topLevelItem(i)

            for c in range(group_item.childCount()):
                pwl_tree_item = group_item.child(c)
                is_user_defined = pwl_tree_item.data(0, QtCore.Qt.UserRole)
                if is_user_defined:
                    continue

                if within_pilot_area:
                    pwl_tree_item.setFlags(self.pwl_item_flags)
                else:
                    pwl_tree_item.setFlags(QtCore.Qt.NoItemFlags)

    def group_value_changed(self, group_name, group_value):
        """Slot to handle priority group widget changes.

        :param group_name: Group name
        :type group_name: str

        :param group_value: Group value
        :type group_value: int
        """

        group = settings_manager.find_group_by_name(group_name)
        group["value"] = group_value
        settings_manager.save_priority_group(group)

        for index in range(self.priority_groups_list.topLevelItemCount()):
            item = self.priority_groups_list.topLevelItem(index)

            for child_index in range(item.childCount()):
                child = item.child(child_index)
                layer = settings_manager.find_layer_by_name(child.text(0))
                new_groups = []
                for group in layer.get("groups"):
                    if group.get("name") == group_name:
                        group["value"] = group_value
                    new_groups.append(group)
                layer["groups"] = new_groups
                settings_manager.save_priority_layer(layer)

    def update_priority_groups(self):
        list_items = []
        items_only = []
        stored_priority_groups = settings_manager.get_priority_groups()
        self.priority_groups_list.clear()

        for group in stored_priority_groups:
            group_widget = PriorityGroupWidget(
                group,
            )
            group_widget.input_value_changed.connect(self.group_value_changed)
            group_widget.slider_value_changed.connect(self.group_value_changed)

            self.priority_groups_widgets[group["name"]] = group_widget

            pw_layers = settings_manager.find_layers_by_group(group["name"])

            item = QtWidgets.QTreeWidgetItem()
            item.setSizeHint(0, group_widget.sizeHint())
            item.setExpanded(True)
            item.setData(0, QtCore.Qt.UserRole, group.get("uuid"))

            # Add priority layers into the group as a child items.

            item.setExpanded(True) if len(pw_layers) > 0 else None

            for layer in pw_layers:
                if item.parent() is None:
                    layer_item = QtWidgets.QTreeWidgetItem(item)
                    layer_item.setText(0, layer.get("name"))
                    layer_item.setData(
                        0, QtCore.Qt.UserRole, layer.get(USER_DEFINED_ATTRIBUTE)
                    )

            list_items.append((item, group_widget))
            items_only.append(item)

        self.priority_groups_list.addTopLevelItems(items_only)
        for item in list_items:
            self.priority_groups_list.setItemWidget(item[0], 0, item[1])

    def update_priority_layers(self, update_groups=True):
        """Updates the priority weighting layers list in the UI.

        :param update_groups: Whether to update the priority groups list or not
        :type update_groups: bool
        """
        self.priority_layers_list.clear()
        for layer in settings_manager.get_priority_layers():
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.DisplayRole, layer.get("name"))
            item.setData(QtCore.Qt.UserRole, layer.get("uuid"))

            self.priority_layers_list.addItem(item)
            if update_groups:
                for index in range(self.priority_groups_list.topLevelItemCount()):
                    group = self.priority_groups_list.topLevelItem(index)
                    if group.text(0) in layer.get("groups"):
                        self.add_priority_layer_group(group, item)
                    else:
                        group_children = group.takeChildren()
                        children = []
                        for child in group_children:
                            if child.text(0) == layer.get("name"):
                                continue
                            children.append(child)
                        group.addChildren(children)

        # Trigger check to enable/disable PWLs
        self.on_extent_changed(self.extent_box.outputExtent())

    def add_priority_layer_group(self, target_group=None, priority_layer=None):
        """Adds priority layer from the weighting layers into a priority group
        If no target_group or priority_layer is passed then the current selected
        group or priority layer from their respective list will be used.

        Checks if priority layer is already in the target group and if so no
        addition is done.

        Once the addition is done, the respective priority layer plugin settings
        are updated to store the new information.

        :param target_group: Priority group where layer will be added to
        :type target_group: dict

        :param priority_layer: Priority weighting layer to be added
        :type priority_layer: dict
        """
        selected_priority_layers = (
            priority_layer or self.priority_layers_list.selectedItems()
        )
        selected_priority_layers = (
            [selected_priority_layers]
            if not isinstance(selected_priority_layers, list)
            else selected_priority_layers
        )

        selected_group = target_group or self.priority_groups_list.currentItem()

        for selected_priority_layer in selected_priority_layers:
            if (
                selected_group is not None and selected_group.parent() is None
            ) and selected_priority_layer is not None:
                children = selected_group.takeChildren()
                item_found = False
                text = selected_priority_layer.data(QtCore.Qt.DisplayRole)
                for child in children:
                    if child.text(0) == text:
                        item_found = True
                        break
                selected_group.addChildren(children)

                if not item_found:
                    selected_group.setExpanded(True)
                    item = QtWidgets.QTreeWidgetItem(selected_group)
                    item.setText(0, text)
                    group_widget = self.priority_groups_list.itemWidget(
                        selected_group, 0
                    )
                    layer_id = selected_priority_layer.data(QtCore.Qt.UserRole)

                    priority_layer = settings_manager.get_priority_layer(layer_id)
                    item.setData(
                        0,
                        QtCore.Qt.UserRole,
                        priority_layer.get(USER_DEFINED_ATTRIBUTE),
                    )
                    target_group_name = (
                        group_widget.group.get("name") if group_widget.group else None
                    )

                    groups = priority_layer.get("groups")
                    new_groups = []
                    group_found = False

                    for group in groups:
                        if target_group_name == group["name"]:
                            group_found = True
                            new_group = settings_manager.find_group_by_name(
                                target_group_name
                            )
                        else:
                            new_group = group
                        new_groups.append(new_group)
                    if not group_found:
                        searched_group = settings_manager.find_group_by_name(
                            target_group_name
                        )
                        new_groups.append(searched_group)

                    priority_layer["groups"] = new_groups
                    settings_manager.save_priority_layer(priority_layer)

        # Trigger check to enable/disable PWLs based on current extent
        self.on_extent_changed(self.extent_box.outputExtent())

    def remove_priority_layer_group(self):
        """Remove the current select priority layer from the current priority group."""
        selected_group = self.priority_groups_list.currentItem()
        parent_item = selected_group.parent() if selected_group is not None else None

        if parent_item:
            priority_layer = settings_manager.find_layer_by_name(selected_group.text(0))
            group_widget = self.priority_groups_list.itemWidget(parent_item, 0)

            groups = priority_layer.get("groups")
            new_groups = []
            for group in groups:
                if group.get("name") == group_widget.group.get("name"):
                    continue
                new_groups.append(group)
            priority_layer["groups"] = new_groups
            settings_manager.save_priority_layer(priority_layer)

            parent_item.removeChild(selected_group)

    def open_help(self):
        """Opens the user documentation for the plugin in a browser"""
        open_documentation(USER_DOCUMENTATION_SITE)

    def add_priority_group(self):
        """Adds a new priority group into the plugin, then updates
        the priority list to show the new added priority group.
        """
        group_dialog = PriorityGroupDialog()
        group_dialog.exec_()
        self.update_priority_groups()

    def edit_priority_group(self):
        """Edits the current selected priority group
        and updates the group box list."""
        if self.priority_groups_list.currentItem() is None:
            self.show_message(
                tr("Select first the priority group from the groups list."),
                Qgis.Critical,
            )
            return

        group_identifier = self.priority_groups_list.currentItem().data(
            0, QtCore.Qt.UserRole
        )

        if group_identifier == "":
            self.show_message(
                tr("Could not fetch the selected priority groups for editing."),
                Qgis.Critical,
            )
            return

        group = settings_manager.get_priority_group(group_identifier)
        group_dialog = PriorityGroupDialog(group)
        group_dialog.exec_()
        self.update_priority_groups()

    def remove_priority_group(self):
        """Removes the current active priority group."""
        if self.priority_groups_list.currentItem() is None:
            self.show_message(
                tr("Select first the priority group from the groups list"),
                Qgis.Critical,
            )
            return
        group_identifier = self.priority_groups_list.currentItem().data(
            0, QtCore.Qt.UserRole
        )

        group = settings_manager.get_priority_group(group_identifier)
        current_text = group.get("name")

        if group_identifier is None or group_identifier == "":
            self.show_message(
                tr("Could not fetch the selected priority group for editing."),
                Qgis.Critical,
            )
            return

        reply = QtWidgets.QMessageBox.warning(
            self,
            tr("QGIS CPLUS PLUGIN"),
            tr('Remove the priority group "{}"?').format(current_text),
            QtWidgets.QMessageBox.Yes,
            QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            settings_manager.delete_priority_group(group_identifier)
            self.update_priority_groups()

    def add_priority_layer(self):
        """Adds a new priority layer into the plugin, then updates
        the priority list to show the new added priority layer.
        """
        layer_dialog = PriorityLayerDialog()
        layer_dialog.exec_()
        self.update_priority_layers(update_groups=False)

    def edit_priority_layer(self):
        """Edits the current selected priority layer
        and updates the layer box list."""
        if self.priority_layers_list.currentItem() is None:
            self.show_message(
                tr("Select first the priority weighting layer from the layers list."),
                Qgis.Critical,
            )
            return

        layer_identifier = self.priority_layers_list.currentItem().data(
            QtCore.Qt.UserRole
        )

        if layer_identifier == "":
            self.show_message(
                tr("Could not fetch the selected priority layer for editing."),
                Qgis.Critical,
            )
            return

        self._show_priority_layer_editor(layer_identifier)

    def _on_double_click_priority_layer(self, list_item: QtWidgets.QListWidgetItem):
        """Slot raised when a priority list item has been double clicked."""
        layer_name = list_item.data(QtCore.Qt.UserRole)
        self._show_priority_layer_editor(layer_name)

    def _show_priority_layer_editor(self, layer_identifier: str):
        """Shows the dialog for editing a priority layer."""
        layer_uuid = uuid.UUID(layer_identifier)
        layer = settings_manager.get_priority_layer(layer_uuid)
        layer_dialog = PriorityLayerDialog(layer)
        layer_dialog.exec_()

    def remove_priority_layer(self):
        """Removes the current active priority layer."""
        if self.priority_layers_list.currentItem() is None:
            self.show_message(
                tr(
                    "Select first the priority " "weighting layer from the layers list."
                ),
                Qgis.Critical,
            )
            return
        current_text = self.priority_layers_list.currentItem().data(
            QtCore.Qt.DisplayRole
        )
        if current_text == "":
            self.show_message(
                tr("Could not fetch the selected priority layer for editing."),
                Qgis.Critical,
            )
            return
        layer = settings_manager.find_layer_by_name(current_text)
        reply = QtWidgets.QMessageBox.warning(
            self,
            tr("QGIS CPLUS PLUGIN"),
            tr('Remove the priority layer "{}"?').format(current_text),
            QtWidgets.QMessageBox.Yes,
            QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            settings_manager.delete_priority_layer(layer.get("uuid"))
            self.update_priority_layers(update_groups=False)

    def prepare_message_bar(self):
        """Initializes the widget message bar settings"""
        self.message_bar.setSizePolicy(
            QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed
        )
        self.grid_layout.addWidget(
            self.message_bar, 0, 0, 1, 1, alignment=QtCore.Qt.AlignTop
        )
        self.dock_widget_contents.layout().insertLayout(0, self.grid_layout)

    def run_analysis(self):
        """Runs the plugin analysis
        Creates new QgsTask, progress dialog and report manager
         for each new scenario analysis.
        """

        extent_list = PILOT_AREA_EXTENT["coordinates"]
        default_extent = QgsRectangle(
            extent_list[0], extent_list[2], extent_list[1], extent_list[3]
        )
        passed_extent = self.extent_box.outputExtent()
        contains = default_extent == passed_extent or default_extent.contains(
            passed_extent
        )
        self.analysis_scenario_name = self.scenario_name.text()
        self.analysis_scenario_description = self.scenario_description.text()

        self.position_feedback = QgsProcessingFeedback()
        self.processing_context = QgsProcessingContext()

        for group in settings_manager.get_priority_groups():
            group_layer_dict = {
                "name": group.get("name"),
                "value": group.get("value"),
                "layers": [],
            }
            for layer in settings_manager.get_priority_layers():
                pwl_items = self.priority_layers_list.findItems(
                    layer.get("name"), QtCore.Qt.MatchExactly
                )
                if len(pwl_items) > 0:
                    # Exclude adding the PWL since its for a disabled default
                    # item outside the pilot AOI.
                    if pwl_items[0].flags() == QtCore.Qt.NoItemFlags:
                        continue

                group_names = [group.get("name") for group in layer.get("groups", [])]
                if group.get("name") in group_names:
                    group_layer_dict["layers"].append(layer.get("name"))
            self.analysis_priority_layers_groups.append(group_layer_dict)

        self.analysis_implementation_models = [
            item.implementation_model
            for item in self.implementation_model_widget.selected_im_items()
            if item.isEnabled()
        ]

        self.analysis_weighted_ims = []

        base_dir = settings_manager.get_value(Settings.BASE_DIR)

        if self.analysis_scenario_name == "" or self.analysis_scenario_name is None:
            self.show_message(
                tr(f"Scenario name cannot be blank."),
                level=Qgis.Critical,
            )
            return
        if (
            self.analysis_scenario_description == ""
            or self.analysis_scenario_description is None
        ):
            self.show_message(
                tr(f"Scenario description cannot be blank."),
                level=Qgis.Critical,
            )
            return
        if (
            self.analysis_implementation_models == []
            or self.analysis_implementation_models is None
        ):
            self.show_message(
                tr("Select at least one implementation models from step two."),
                level=Qgis.Critical,
            )
            return

        if not contains:
            self.show_message(
                tr(f"Selected area of interest is outside the pilot area."),
                level=Qgis.Info,
            )
            default_ext = (
                f"{default_extent.xMinimum()}, {default_extent.xMaximum()},"
                f"{default_extent.yMinimum()}, {default_extent.yMaximum()}"
            )
            log(
                f"Outside the pilot area, passed extent "
                f"{passed_extent}"
                f"default extent{default_ext}"
            )

        if base_dir is None:
            self.show_message(
                tr(
                    f"Plugin base data directory is not set! "
                    f"Go to plugin settings in order to set it."
                ),
                level=Qgis.Critial,
            )
            return
        self.analysis_extent = SpatialExtent(
            bbox=[
                passed_extent.xMinimum(),
                passed_extent.xMaximum(),
                passed_extent.yMinimum(),
                passed_extent.yMaximum(),
            ]
        )
        try:
            self.run_scenario_btn.setEnabled(False)

            scenario = Scenario(
                uuid=uuid.uuid4(),
                name=self.analysis_scenario_name,
                description=self.analysis_scenario_description,
                extent=self.analysis_extent,
                models=self.analysis_implementation_models,
                weighted_models=[],
                priority_layer_groups=self.analysis_priority_layers_groups,
            )

            self.processing_cancelled = False

            # Creates and opens the progress dialog for the analysis
            progress_dialog = ProgressDialog(
                minimum=0,
                maximum=100,
                main_widget=self,
                scenario_id=str(scenario.uuid),
                scenario_name=self.analysis_scenario_name,
            )
            progress_dialog.analysis_cancelled.connect(
                self.on_progress_dialog_cancelled
            )
            progress_dialog.run_dialog()

            progress_dialog.change_status_message(
                tr("Raster calculation for models pathways")
            )

            analysis_task = ScenarioAnalysisTask(
                self.analysis_scenario_name,
                self.analysis_scenario_description,
                self.analysis_implementation_models,
                self.analysis_priority_layers_groups,
                self.analysis_extent,
                passed_extent,
                scenario,
            )

            progress_changed = partial(self.update_progress_bar, progress_dialog)
            analysis_task.custom_progress_changed.connect(progress_changed)

            status_message_changed = partial(
                self.update_progress_dialog, progress_dialog
            )

            analysis_task.status_message_changed.connect(status_message_changed)

            analysis_task.info_message_changed.connect(self.show_message)

            progress_dialog.analysis_task = analysis_task
            progress_dialog.scenario_id = str(scenario.uuid)

            report_running = partial(self.on_report_running, progress_dialog)
            report_error = partial(self.on_report_error, progress_dialog)
            report_finished = partial(self.on_report_finished, progress_dialog)

            # Report manager
            scenario_report_manager = report_manager

            scenario_report_manager.generate_started.connect(report_running)
            scenario_report_manager.generate_error.connect(report_error)
            scenario_report_manager.generate_completed.connect(report_finished)

            analysis_complete = partial(
                self.analysis_complete,
                analysis_task,
                scenario_report_manager,
                progress_dialog,
            )

            analysis_task.taskCompleted.connect(analysis_complete)

            analysis_task.taskTerminated.connect(self.task_terminated)

            QgsApplication.taskManager().addTask(analysis_task)

        except Exception as err:
            self.show_message(
                tr("An error occurred when preparing analysis task"),
                level=Qgis.Info,
            )
            log(
                tr(
                    "An error occurred when preparing analysis task"
                    ', error message "{}"'.format(err)
                )
            )

    def task_terminated(self):
        """Handles logging of the scenario analysis task status
        after it has been terminated.
        """
        log(f"Main task terminated")

    def analysis_complete(self, task, report_manager, progress_dialog):
        """Calls the responsible function for handling analysis results outputs

        :param task: Analysis task
        :type task: ScenarioAnalysisTask

        :param report_manager: Report manager used to generate analysis reports
        :type report_manager: ReportManager
        """

        self.scenario_result = task.scenario_result
        self.scenario_results(task, report_manager, progress_dialog)

    def transform_extent(self, extent, source_crs, dest_crs):
        """Transforms the passed extent into the destination crs

         :param extent: Target extent
        :type extent: QgsRectangle

        :param source_crs: Source CRS of the passed extent
        :type source_crs: QgsCoordinateReferenceSystem

        :param dest_crs: Destination CRS
        :type dest_crs: QgsCoordinateReferenceSystem
        """

        transform = QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance())
        transformed_extent = transform.transformBoundingBox(extent)

        return transformed_extent

    def main_task(self):
        """Serves as a QgsTask function for the main task that contains
        smaller sub-tasks running the actual processing calculations.
        """

        log("Running from main task.")

    def cancel_processing_task(self):
        """Cancels the current processing task."""
        self.processing_cancelled = True

        # # Analysis processing tasks
        # try:
        #     if self.task:
        #         self.task.cancel()
        # except Exception as e:
        #     self.on_progress_dialog_cancelled()
        #     log(f"Problem cancelling task, {e}")
        #
        # # Report generating task
        # try:
        #     if self.reporting_feedback:
        #         self.reporting_feedback.cancel()
        # except Exception as e:
        #     self.on_progress_dialog_cancelled()
        #     log(f"Problem cancelling report generating task, {e}")

    def scenario_results(self, task, report_manager, progress_dialog):
        """Called when the task ends. Sets the progress bar to 100 if it finished.

        :param task: Analysis task
        :type task: ScenarioAnalysisTask

        :param report_manager: Report manager used to generate analysis reports
        :type report_manager: ReportManager
        """
        if task.output is not None:
            self.update_progress_bar(progress_dialog, 100)
            self.scenario_result.analysis_output = task.output
            self.scenario_result.state = ScenarioState.FINISHED
            self.post_analysis(
                self.scenario_result, task, report_manager, progress_dialog
            )
        else:
            status_message = "No valid output from the processing results."
            task.set_status_message(status_message)

            log(f"No valid output from the processing results.")

    def move_layer_to_group(self, layer, group) -> None:
        """Moves a layer open in QGIS to another group.

        :param layer: Raster layer to move
        :type layer: QgsRasterLayer

        :param group: Group to which the raster should be moved
        :type group: QgsLayerTreeGroup
        """
        if layer:
            instance_root = QgsProject.instance().layerTreeRoot()
            layer = instance_root.findLayer(layer.id())
            layer_clone = layer.clone()
            parent = layer.parent()
            group.insertChildNode(0, layer_clone)  # Add to top of group
            parent.removeChildNode(layer)

    def post_analysis(self, scenario_result, task, report_manager, progress_dialog):
        """Handles analysis outputs from the final analysis results.
        Adds the resulting scenario raster to the canvas with styling.
        Adds each of the implementation models to the canvas with styling.
        Adds each IMs pathways to the canvas.

        :param scenario_result: ScenarioResult of output results
        :type scenario_result: ScenarioResult

        :param task: Analysis task
        :type task: ScenarioAnalysisTask

        :param report_manager: Report manager used to generate analysis reports
        :type report_manager: ReportManager
        """

        # If the processing were stopped, no file will be added
        if not self.processing_cancelled:
            list_models = scenario_result.scenario.models
            raster = scenario_result.analysis_output["OUTPUT"]
            im_weighted_dir = os.path.join(os.path.dirname(raster), "weighted_ims")

            scenario_name = scenario_result.scenario.name
            qgis_instance = QgsProject.instance()
            instance_root = qgis_instance.layerTreeRoot()

            # Check if there are other groups for the scenario
            # and assign a suffix.
            counter = 1
            group_name = scenario_name

            # Control to prevent infinite loop
            max_limit = 100
            while True and counter <= max_limit:
                scenario_grp = instance_root.findGroup(group_name)
                if scenario_grp is None:
                    break
                group_name = f"{scenario_name} {counter!s}"
                counter += 1

            # Groups
            scenario_group = instance_root.insertGroup(0, group_name)
            im_group = scenario_group.addGroup(tr(IM_GROUP_LAYER_NAME))
            im_weighted_group = (
                scenario_group.addGroup(tr(IM_WEIGHTED_GROUP_NAME))
                if os.path.exists(im_weighted_dir)
                else None
            )
            pathways_group = scenario_group.addGroup(tr(NCS_PATHWAYS_GROUP_LAYER_NAME))

            # Group settings
            im_group.setExpanded(False)
            im_weighted_group.setExpanded(False) if im_weighted_group else None
            pathways_group.setExpanded(False)
            pathways_group.setItemVisibilityCheckedRecursive(False)

            # Add scenario result layer to the canvas with styling
            layer_file = scenario_result.analysis_output.get("OUTPUT")
            layer_name = (
                f"{SCENARIO_OUTPUT_LAYER_NAME}_"
                f'{datetime.datetime.now().strftime("%Y_%m_%d_%H_%M_%S")}'
            )
            scenario_result.output_layer_name = layer_name
            layer = QgsRasterLayer(layer_file, layer_name, QGIS_GDAL_PROVIDER)
            scenario_layer = qgis_instance.addMapLayer(layer)

            # Scenario result layer styling
            renderer = self.style_models_layer(layer, task.analysis_weighted_ims)
            layer.setRenderer(renderer)
            layer.triggerRepaint()

            """A workaround to add a layer to a group.
            Adding it using group.insertChildNode or group.addLayer causes issues,
            but adding to the root is fine.
            This approach adds it to the root, and then moves it to the group.
            """
            self.move_layer_to_group(scenario_layer, scenario_group)

            # Add implementation models and pathways
            im_index = 0
            for im in list_models:
                im_name = im.name
                im_layer = QgsRasterLayer(im.path, im.name)
                im_layer.setCustomProperty(MODEL_IDENTIFIER_PROPERTY, str(im.uuid))
                list_pathways = im.pathways

                # Add IM layer with styling, if available
                if im_layer:
                    renderer = self.style_model_layer(im_layer, im)

                    added_im_layer = qgis_instance.addMapLayer(im_layer)
                    self.move_layer_to_group(added_im_layer, im_group)

                    im_layer.setRenderer(renderer)
                    im_layer.triggerRepaint()

                # Add IM pathways
                if len(list_pathways) > 0:
                    # im_pathway_group = pathways_group.addGroup(im_name)
                    im_pathway_group = pathways_group.insertGroup(im_index, im_name)
                    im_pathway_group.setExpanded(False)

                    pw_index = 0
                    for pathway in list_pathways:
                        try:
                            # pathway_name = pathway.name
                            pathway_layer = pathway.to_map_layer()

                            added_pw_layer = qgis_instance.addMapLayer(pathway_layer)
                            self.move_layer_to_group(added_pw_layer, im_pathway_group)

                            pathway_layer.triggerRepaint()

                            pw_index = pw_index + 1
                        except Exception as err:
                            self.show_message(
                                tr(
                                    "An error occurred loading a pathway, "
                                    "check logs for more information"
                                ),
                                level=Qgis.Info,
                            )
                            log(
                                tr(
                                    "An error occurred loading a pathway, "
                                    'scenario analysis, error message "{}"'.format(err)
                                )
                            )

                im_index = im_index + 1

            weighted_ims = task.analysis_weighted_ims if task is not None else []

            for model in weighted_ims:
                weighted_im_path = model.path
                weighted_im_name = Path(weighted_im_path).stem

                if not weighted_im_path.endswith(".tif"):
                    continue

                im_weighted_layer = QgsRasterLayer(
                    weighted_im_path, weighted_im_name, QGIS_GDAL_PROVIDER
                )

                # Set UUID for easier retrieval
                im_weighted_layer.setCustomProperty(
                    MODEL_IDENTIFIER_PROPERTY, str(model.uuid)
                )

                renderer = self.style_model_layer(im_weighted_layer, model)
                im_weighted_layer.setRenderer(renderer)
                im_weighted_layer.triggerRepaint()

                added_im_weighted_layer = qgis_instance.addMapLayer(im_weighted_layer)
                self.move_layer_to_group(added_im_weighted_layer, im_weighted_group)

            # Initiate report generation
            self.run_report(progress_dialog, report_manager)
        else:
            # Reinitializes variables if processing were cancelled by the user
            # Not doing this breaks the processing if a user tries to run
            # the processing after cancelling or if the processing fails
            self.position_feedback = QgsProcessingFeedback()
            self.processing_context = QgsProcessingContext()

    def style_models_layer(self, layer, models):
        """Applies the styling to the passed layer that
         contains the passed list of models.

        :param layer: Layer to be styled
        :type layer: QgsRasterLayer

        :param models: List which contains the implementation
        models that were passed to the highest position analysis tool
        :type models: list

        :returns: Renderer for the symbology.
        :rtype: QgsPalettedRasterRenderer
        """
        area_classes = []
        for model in models:
            im_name = model.name

            raster_val = model.style_pixel_value
            color = model.scenario_fill_symbol().color()
            color_ramp_shader = QgsColorRampShader.ColorRampItem(
                float(raster_val), QtGui.QColor(color), im_name
            )
            area_classes.append(color_ramp_shader)

        class_data = QgsPalettedRasterRenderer.colorTableToClassData(area_classes)
        renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, class_data)

        return renderer

    def style_model_layer(self, layer, model):
        """Applies the styling to the layer that contains the passed
         implementation model name.

        :param layer: Raster layer to which to apply the symbology
        :type layer: QgsRasterLayer

        :param model: Implementation model
        :type model: ImplementationModel

        :returns: Renderer for the symbology.
        :rtype: QgsSingleBandPseudoColorRenderer
        """

        # Retrieves a build-in QGIS color ramp
        color_ramp = model.model_color_ramp()

        stats = layer.dataProvider().bandStatistics(1)
        renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1)

        renderer.setClassificationMin(stats.minimumValue)
        renderer.setClassificationMax(stats.maximumValue)

        renderer.createShader(
            color_ramp, QgsColorRampShader.Interpolated, QgsColorRampShader.Continuous
        )

        return renderer

    def update_progress_dialog(
        self,
        progress_dialog,
        message=None,
    ):
        """Run report generation. This should be called after the
         analysis is complete.

        :param progress_dialog: Dialog responsible for showing
         all the analysis operations progress.
        :type progress_dialog: ProgressDialog

        :param message: Report manager used to generate analysis reports
        :type message: ReportManager
        """

        progress_dialog.change_status_message(message) if message is not None else None

    def update_progress_bar(self, progress_dialog, value):
        """Sets the value of the progress bar

        :param progress_dialog: Dialog responsible for showing
         all the analysis operations progress.
        :type progress_dialog: ProgressDialog

        :param value: Value to be set on the progress bar
        :type value: float
        """
        if progress_dialog and not self.processing_cancelled:
            try:
                progress_dialog.update_progress_bar(int(value))
            except RuntimeError:
                log(tr("Error setting value to a progress bar"), notify=False)

    def update_message_bar(self, message):
        """Changes the message in the message bar item.

        :param message: Message to be updated
        :type message: str
        """
        message_bar_item = self.message_bar.createMessage(message)
        self.message_bar.pushWidget(message_bar_item, Qgis.Info)

    def show_message(self, message, level=Qgis.Warning):
        """Shows message on the main widget message bar.

        :param message: Text message
        :type message: str

        :param level: Message level type
        :type level: Qgis.MessageLevel
        """
        self.message_bar.clearWidgets()
        self.message_bar.pushMessage(message, level=level)

    def zoom_pilot_area(self):
        """Zoom the current main map canvas to the pilot area extent."""
        map_canvas = iface.mapCanvas()
        extent_list = PILOT_AREA_EXTENT["coordinates"]
        default_extent = QgsRectangle(
            extent_list[0], extent_list[2], extent_list[1], extent_list[3]
        )
        zoom_extent = QgsRectangle(
            extent_list[0] - 0.5, extent_list[2], extent_list[1] + 0.5, extent_list[3]
        )

        canvas_crs = map_canvas.mapSettings().destinationCrs()
        original_crs = QgsCoordinateReferenceSystem("EPSG:4326")

        if canvas_crs.authid() != original_crs.authid():
            zoom_extent = self.transform_extent(zoom_extent, original_crs, canvas_crs)
            default_extent = self.transform_extent(
                default_extent, original_crs, canvas_crs
            )

        aoi = QgsRubberBand(iface.mapCanvas(), QgsWkbTypes.PolygonGeometry)

        aoi.setFillColor(QtGui.QColor(0, 0, 0, 0))
        aoi.setStrokeColor(QtGui.QColor(88, 128, 8))
        aoi.setWidth(3)
        aoi.setLineStyle(QtCore.Qt.DashLine)

        geom = QgsGeometry.fromRect(default_extent)

        aoi.setToGeometry(geom, canvas_crs)

        map_canvas.setExtent(zoom_extent)
        map_canvas.refresh()

    def prepare_extent_box(self):
        """Configure the spatial extent box with the initial settings."""

        self.extent_box.setOutputCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        map_canvas = iface.mapCanvas()
        self.extent_box.setCurrentExtent(
            map_canvas.mapSettings().destinationCrs().bounds(),
            map_canvas.mapSettings().destinationCrs(),
        )
        self.extent_box.setOutputExtentFromCurrent()
        self.extent_box.setMapCanvas(map_canvas)

        extent_list = PILOT_AREA_EXTENT["coordinates"]
        default_extent = QgsRectangle(
            extent_list[0], extent_list[2], extent_list[1], extent_list[3]
        )

        self.extent_box.setOutputExtentFromUser(
            default_extent,
            QgsCoordinateReferenceSystem("EPSG:4326"),
        )

    def on_tab_step_changed(self, index: int):
        """Slot raised when the current tab changes.

        :param index: Zero-based index position of new current tab
        :type index: int
        """
        if index == 1:
            self.implementation_model_widget.can_show_error_messages = True
            self.implementation_model_widget.load()

        elif index == 2:
            # Validate implementation model selection
            selected_implementation_models = (
                self.implementation_model_widget.selected_im_items()
            )
            if len(selected_implementation_models) == 0:
                msg = self.tr("Please select at least one implementation model.")
                self.show_message(msg)
                self.tab_widget.setCurrentIndex(1)

            else:
                self.message_bar.clearWidgets()

    def open_settings(self):
        """Options the CPLUS settings in the QGIS options dialog."""
        self.iface.showOptionsDialog(currentPage=OPTIONS_TITLE)

    def run_report(self, progress_dialog, report_manager):
        """Run report generation. This should be called after the
        analysis is complete.

        :param progress_dialog: Dialog responsible for showing
         all the analysis operations progress.
        :type progress_dialog: ProgressDialog

        :param report_manager: Report manager used to generate analysis reports
        :type report_manager: ReportManager
        """
        if self.processing_cancelled:
            # Will not proceed if processing has been cancelled by the user
            return

        if self.scenario_result is None:
            log(
                "Cannot run report generation, scenario result is not defined",
                info=False,
            )
            return

        reporting_feedback = self.reset_reporting_feedback(progress_dialog)
        self.reporting_feedback = reporting_feedback

        submit_result = report_manager.generate(
            self.scenario_result, reporting_feedback
        )
        if not submit_result.status:
            msg = self.tr("Unable to submit report request for scenario")
            self.show_message(f"{msg} {self.scenario_result.scenario.name}.")

    def on_report_running(self, progress_dialog, scenario_id: str):
        """Slot raised when report task has started.

        :param progress_dialog: Dialog responsible for showing
         all the analysis operations progress.
        :type progress_dialog: ProgressDialog

        :param scenario_id: Scenario analysis id
        :type scenario_id: str
        """
        if not self.report_job_is_for_current_scenario(scenario_id):
            return

        progress_dialog.update_progress_bar(0)
        progress_dialog.report_running = True
        progress_dialog.change_status_message(
            tr("Generating report for the analysis output")
        )

    def on_report_error(self, progress_dialog, message: str):
        """Slot raised when report task error has occured.

        :param progress_dialog: Dialog responsible for showing
         all the analysis operations progress.
        :type progress_dialog: ProgressDialog
        """
        progress_dialog.report_running = True
        progress_dialog.change_status_message(
            tr("Error generating report, see logs for more info.")
        )
        log(message)

        self.run_scenario_btn.setEnabled(True)

    def reset_reporting_feedback(self, progress_dialog):
        """Creates a new reporting feedback object and reconnects
        the signals.

        We are doing this to address cases where the feedback is canceled
        and the same object has to be reused for subsequent report
        generation tasks.

        :param progress_dialog: Dialog responsible for showing
         all the analysis operations progress.
        :type progress_dialog: ProgressDialog

        :returns reporting_feedback: Feedback instance to be used in storing
        processing status details.
        :rtype reporting_feedback: QgsFeedback

        """

        progress_changed = partial(self.on_reporting_progress_changed, progress_dialog)

        reporting_feedback = QgsFeedback(self)
        reporting_feedback.progressChanged.connect(progress_changed)

        return reporting_feedback

    def on_reporting_progress_changed(self, progress_dialog, progress: float):
        """Slot raised when the reporting progress has changed.

        :param progress_dialog: Dialog responsible for showing
         all the analysis operations progress.
        :type progress_dialog: ProgressDialog

        :param progress: Analysis progress value between 0 and 100
        :type progress: float
        """
        progress_dialog.update_progress_bar(progress)

    def on_report_finished(self, progress_dialog, scenario_id: str):
        """Slot raised when report task has finished.

        :param progress_dialog: Dialog responsible for showing
         all the analysis operations progress.
        :type progress_dialog: ProgressDialog

        :param scenario_id: Scenario analysis id
        :type scenario_id: str
        """
        if not self.report_job_is_for_current_scenario(scenario_id):
            return

        progress_dialog.set_report_complete()
        progress_dialog.change_status_message(tr("Report generation complete"))

        self.run_scenario_btn.setEnabled(True)

    def report_job_is_for_current_scenario(self, scenario_id: str) -> bool:
        """Checks if the given scenario identifier is for the current
        scenario result.

        This is to ensure that signals raised by the report manager refer
        to the current scenario result object and not for old jobs.

        :param scenario_id: Scenario identifier usually from a signal
        raised by the report manager.
        :type scenario_id: str

        :returns: True if the scenario identifier matches the current
        scenario object in the results, else False.
        :rtype: bool
        """
        if self.scenario_result is None:
            return False

        current_scenario = self.scenario_result.scenario
        if current_scenario is None:
            return False

        if str(current_scenario.uuid) == scenario_id:
            return True

        return False

    def on_progress_dialog_cancelled(self):
        """Slot raised when analysis has been cancelled in progress dialog."""
        if not self.run_scenario_btn.isEnabled():
            self.run_scenario_btn.setEnabled(True)
