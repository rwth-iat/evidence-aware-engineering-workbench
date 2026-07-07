from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from copy import deepcopy
from typing import Any

from qtpy.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QDragEnterEvent, QDropEvent, QKeySequence
from qtpy.QtWidgets import QGraphicsItem, QGraphicsView, QLineEdit, QMenu, QTreeWidget, QTreeWidgetItem, QWidgetAction
from qtpynodeeditor import (
    Connection,
    ConnectionDataTypeFailure,
    ConnectionGraphicsObject,
    DataModelRegistry,
    FlowScene,
    FlowView,
    Node,
    NodeData,
    NodeDataModel,
    NodeDataType,
    NodeGraphicsObject,
    Port,
    PortType,
    StyleCollection,
)

from iev4pi_transformation_tool.core.utils import clean_cell, normalize_identifier
from iev4pi_transformation_tool.tx import ALLOWED_TX_NODE_TYPES, TxEdge, TxNode, TxRuleSet, build_default_uc1_rule_set
from iev4pi_transformation_tool.ui.node_tooltips import NodeTooltipContext, build_node_tooltip


PALETTE_MIME_TYPE = "application/x-ievpi-tx-node"
AUTO_LAYOUT_VERSION = 3

TX_VALUE_TYPE = NodeDataType("tx_value", "Value")
TX_PROPERTY_TYPE = NodeDataType("tx_property", "Property")

NODE_GROUPS: OrderedDict[str, tuple[str, ...]] = OrderedDict(
    {
        "Inputs": ("InputColumn", "Constant"),
        "Transforms": ("NormalizeIdentifier", "RegexExtract", "MapEnum", "BoolMap", "Concat", "PreferFirstNonEmpty", "Condition", "ConfidenceGate"),
        "Outputs": ("OutputProperty", "OutputSubmodel"),
    }
)

NODE_LAYOUT_RANK = {
    node_type: index
    for index, node_type in enumerate(node_type for group in NODE_GROUPS.values() for node_type in group)
}

NODE_TYPE_COLORS: dict[str, tuple[str, str, str, str]] = {
    "InputColumn": ("#CFE7FF", "#9BC9FF", "#7CB3F5", "#5F93D6"),
    "Constant": ("#E8F2FF", "#BDD8FF", "#9CC1F2", "#7A9BD1"),
    "NormalizeIdentifier": ("#E5F6F0", "#BDE7D8", "#8DD0B7", "#69A68D"),
    "RegexExtract": ("#FFF1DD", "#FBD9A8", "#F2BE79", "#CC9358"),
    "MapEnum": ("#FFF4DE", "#F7DEAB", "#E9BF71", "#C19043"),
    "BoolMap": ("#F2F3FF", "#D6DAFF", "#B9C0FF", "#8D95DB"),
    "Concat": ("#E8FBFF", "#BAEEF7", "#8FD7E6", "#62A9BD"),
    "PreferFirstNonEmpty": ("#E7FBF3", "#BAEFD6", "#8AD1B0", "#5FA183"),
    "Condition": ("#FFF0F0", "#F7C5C5", "#E89B9B", "#BF7272"),
    "ConfidenceGate": ("#F9F3FF", "#E6D2FF", "#D0AEFF", "#A17ACF"),
    "OutputProperty": ("#EAF2FF", "#BFD3FF", "#9FB9FF", "#7490D1"),
    "OutputSubmodel": ("#EFF7E8", "#C8E0B4", "#A4CC83", "#789856"),
}

_DEFAULT_TX_CONFIGS: dict[str, dict[str, object]] = {
    "InputColumn": {"field": "", "mode": "join", "separator": " | "},
    "Constant": {"value": ""},
    "NormalizeIdentifier": {},
    "RegexExtract": {"pattern": "", "group": 1, "default": ""},
    "MapEnum": {"mapping": {}, "default": ""},
    "BoolMap": {"true_value": "true", "false_value": "false"},
    "Concat": {"separator": " | "},
    "PreferFirstNonEmpty": {"fallback": ""},
    "Condition": {"operator": "equals", "true_value": "", "false_value": "", "compare_to": ""},
    "ConfidenceGate": {"min_confidence": 0.7, "fallback": ""},
    "OutputProperty": {"property_name": ""},
    "OutputSubmodel": {"id_short": ""},
}

_SINGLE_INPUT_PORT_NAMES: dict[str, tuple[str, ...]] = {
    "NormalizeIdentifier": ("value",),
    "RegexExtract": ("value",),
    "MapEnum": ("value",),
    "BoolMap": ("value",),
    "OutputProperty": ("value",),
}

_FIXED_INPUT_PORT_NAMES: dict[str, tuple[str, ...]] = {
    "Condition": ("subject", "compare"),
    "ConfidenceGate": ("value", "confidence"),
}

_FIXED_OUTPUT_PORT_NAMES: dict[str, tuple[str, ...]] = {
    "OutputProperty": ("property",),
}

_DEFAULT_DYNAMIC_INPUTS: dict[str, int] = {
    "Concat": 4,
    "PreferFirstNonEmpty": 4,
    "OutputSubmodel": 6,
}

_SCENE_STYLE_JSON = {
    "FlowViewStyle": {
        "BackgroundColor": [241, 245, 250],
        "FineGridColor": [221, 230, 239],
        "CoarseGridColor": [198, 212, 228],
    },
    "ConnectionStyle": {
        "ConstructionColor": "#8AA9C8",
        "NormalColor": "#4F7FAF",
        "SelectedColor": "#245E9A",
        "SelectedHaloColor": "#FFB55A",
        "HoveredColor": "#2D8BD8",
        "LineWidth": 3.0,
        "ConstructionLineWidth": 2.2,
        "PointDiameter": 9.0,
        "UseDataDefinedColors": False,
    },
    "NodeStyle": {
        "NormalBoundaryColor": "#4D6A84",
        "SelectedBoundaryColor": "#1A6BC4",
        "GradientColor0": "#EAF2FB",
        "GradientColor1": "#D4E3F4",
        "GradientColor2": "#C6D8EC",
        "GradientColor3": "#B6CAE2",
        "ShadowColor": [60, 84, 108, 35],
        "FontColor": "#17324B",
        "FontColorFaded": "#567188",
        "ConnectionPointColor": "#5E7F9B",
        "FilledConnectionPointColor": "#2F6FA8",
        "ErrorColor": "#D75A5A",
        "WarningColor": "#D0A348",
        "PenWidth": 1.2,
        "HoveredPenWidth": 1.8,
        "ConnectionPointDiameter": 8.0,
        "Opacity": 0.96,
    },
}


def default_tx_config(node_type: str) -> dict[str, object]:
    return dict(_DEFAULT_TX_CONFIGS.get(node_type, {}))


def source_type_targets(source_type: str) -> dict[str, list[str]]:
    rule_set = build_default_uc1_rule_set(source_type)
    edge_targets = {edge.from_node: edge.to_node for edge in rule_set.edges}
    node_map = {node.id: node for node in rule_set.nodes}
    properties_by_submodel: dict[str, list[str]] = defaultdict(list)
    for node in rule_set.nodes:
        if node.node_type != "OutputProperty":
            continue
        property_name = clean_cell(node.config.get("property_name", "")) or node.label or node.id
        submodel_id = edge_targets.get(node.id, "")
        submodel_node = node_map.get(submodel_id)
        submodel_name = clean_cell(submodel_node.config.get("id_short", "")) if submodel_node else ""
        if submodel_name:
            properties_by_submodel[submodel_name].append(property_name)
    return {
        "submodels": sorted(properties_by_submodel.keys()),
        "properties": sorted({property_name for items in properties_by_submodel.values() for property_name in items}),
        "properties_by_submodel": {
            key: sorted(values)
            for key, values in sorted(properties_by_submodel.items())
        },
    }


def create_scene_style(node_type: str | None = None) -> StyleCollection:
    style_doc = deepcopy(_SCENE_STYLE_JSON)
    if node_type:
        color0, color1, color2, color3 = NODE_TYPE_COLORS.get(node_type, NODE_TYPE_COLORS["InputColumn"])
        style_doc["NodeStyle"].update(
            {
                "GradientColor0": color0,
                "GradientColor1": color1,
                "GradientColor2": color2,
                "GradientColor3": color3,
            }
        )
    return StyleCollection.from_json(style_doc)


def _display_caption(node_type: str, label: str) -> str:
    cleaned_label = clean_cell(label)
    if not cleaned_label or cleaned_label == node_type:
        return node_type
    return f"{node_type}: {cleaned_label}"


def _base_input_port_count(node_type: str) -> int:
    if node_type in {"InputColumn", "Constant"}:
        return 0
    if node_type in _FIXED_INPUT_PORT_NAMES:
        return len(_FIXED_INPUT_PORT_NAMES[node_type])
    if node_type in _SINGLE_INPUT_PORT_NAMES:
        return 1
    if node_type in _DEFAULT_DYNAMIC_INPUTS:
        return _DEFAULT_DYNAMIC_INPUTS[node_type]
    return 1


def resolve_input_capacity(node_type: str, incoming_count: int = 0) -> int:
    base = _base_input_port_count(node_type)
    if node_type in _DEFAULT_DYNAMIC_INPUTS:
        return max(base, incoming_count + 1)
    return base


def output_port_count(node_type: str) -> int:
    return 0 if node_type == "OutputSubmodel" else 1


def input_port_names(node_type: str, capacity: int) -> list[str]:
    if node_type in _FIXED_INPUT_PORT_NAMES:
        return list(_FIXED_INPUT_PORT_NAMES[node_type][:capacity])
    if node_type in _SINGLE_INPUT_PORT_NAMES:
        return list(_SINGLE_INPUT_PORT_NAMES[node_type])
    if node_type == "Concat":
        return [f"item_{index + 1}" for index in range(capacity)]
    if node_type == "PreferFirstNonEmpty":
        return [f"candidate_{index + 1}" for index in range(capacity)]
    if node_type == "OutputSubmodel":
        return [f"property_{index + 1}" for index in range(capacity)]
    return ["value" for _index in range(capacity)]


def output_port_names(node_type: str) -> list[str]:
    if node_type in _FIXED_OUTPUT_PORT_NAMES:
        return list(_FIXED_OUTPUT_PORT_NAMES[node_type])
    if node_type == "OutputSubmodel":
        return []
    return ["value"]


def input_port_key(node_type: str, index: int) -> str:
    names = input_port_names(node_type, max(index + 1, _base_input_port_count(node_type)))
    if index < len(names):
        return names[index]
    if node_type == "OutputSubmodel":
        return f"property_{index + 1}"
    return f"in_{index + 1}"


def output_port_key(node_type: str, index: int = 0) -> str:
    names = output_port_names(node_type)
    if index < len(names):
        return names[index]
    return "value"


def _parse_dynamic_port_index(prefix: str, port_name: str) -> int | None:
    cleaned = clean_cell(port_name).lower()
    if cleaned == prefix:
        return 0
    if cleaned.startswith(f"{prefix}_"):
        suffix = cleaned.split("_")[-1]
        if suffix.isdigit():
            return max(0, int(suffix) - 1)
    return None


def resolve_target_port_index(node_type: str, target_port: str, used_indexes: set[int], capacity: int) -> int:
    cleaned = clean_cell(target_port)
    if node_type in _FIXED_INPUT_PORT_NAMES:
        candidates = list(_FIXED_INPUT_PORT_NAMES[node_type])
        if cleaned in candidates:
            return candidates.index(cleaned)
    if node_type in _SINGLE_INPUT_PORT_NAMES:
        return 0
    if node_type == "OutputSubmodel":
        parsed = _parse_dynamic_port_index("property", cleaned)
        if parsed is not None and parsed not in used_indexes and parsed < capacity:
            return parsed
    if node_type == "Concat":
        parsed = _parse_dynamic_port_index("item", cleaned)
        if parsed is None:
            parsed = _parse_dynamic_port_index("in", cleaned)
        if parsed is not None and parsed not in used_indexes and parsed < capacity:
            return parsed
    if node_type == "PreferFirstNonEmpty":
        parsed = _parse_dynamic_port_index("candidate", cleaned)
        if parsed is None:
            parsed = _parse_dynamic_port_index("in", cleaned)
        if parsed is not None and parsed not in used_indexes and parsed < capacity:
            return parsed
    for index in range(capacity):
        if index not in used_indexes:
            return index
    return max(0, capacity - 1)


class StableNodeGraphicsObject(NodeGraphicsObject):
    def __init__(self, scene, node):
        super().__init__(scene, node)
        # DeviceCoordinateCache clips large nodes badly after zooming because the
        # cached pixmap is generated in view pixels. Repaint directly instead.
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.refresh_tooltip()

    def paint(self, painter, option, widget):
        from qtpynodeeditor.node_painter import NodePainter

        NodePainter.paint(
            painter,
            self._node,
            self._scene,
            node_style=self._style.node,
            connection_style=self._style.connection,
        )

    def refresh_tooltip(self) -> None:
        model = getattr(self._node, "model", None)
        if model is None:
            self.setToolTip("")
            return
        context = NodeTooltipContext(
            language=clean_cell(getattr(self._scene, "tooltip_language", "")) or "en",
            editor_kind="tx",
            node_type=clean_cell(getattr(model, "node_type", "")),
            label=clean_cell(getattr(model, "label_text", "")),
            config=dict(getattr(model, "config", {}) or {}),
            source_type=clean_cell(getattr(self._scene, "tooltip_source_type", "")),
            port_names=input_port_names(
                clean_cell(getattr(model, "node_type", "")),
                int(model.num_ports[PortType.input]),
            ),
            connected_input_count=len(getattr(self._node.state, "input_connections", [])),
            connected_output_count=len(getattr(self._node.state, "output_connections", [])),
        )
        self.setToolTip(build_node_tooltip(context))

    def hoverEnterEvent(self, event):
        self.refresh_tooltip()
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event):
        self.refresh_tooltip()
        super().hoverMoveEvent(event)


class TxValueData(NodeData):
    data_type = TX_VALUE_TYPE

    def __init__(self, value: str = "") -> None:
        self.value = value


class TxPropertyData(NodeData):
    data_type = TX_PROPERTY_TYPE

    def __init__(self, value: str = "") -> None:
        self.value = value


class TxNodeDataModel(NodeDataModel):
    name = "TxNode"
    caption = "TxNode"
    caption_visible = True

    def __init__(
        self,
        *,
        node_type: str,
        tx_node_id: str = "",
        label: str = "",
        config: dict[str, Any] | None = None,
        input_capacity: int | None = None,
        style=None,
        parent=None,
    ) -> None:
        super().__init__(style=style, parent=parent)
        self.node_type = node_type
        self.tx_node_id = tx_node_id
        self.label_text = clean_cell(label)
        self.config = dict(config or {})
        self._input_capacity = resolve_input_capacity(node_type, 0) if input_capacity is None else max(0, int(input_capacity))
        self._output_capacity = output_port_count(node_type)
        self.name = node_type
        self.caption = _display_caption(node_type, self.label_text)

    @property
    def num_ports(self) -> dict[PortType, int]:
        return {
            PortType.input: self._input_capacity,
            PortType.output: self._output_capacity,
        }

    @property
    def port_caption(self) -> dict[PortType, dict[int, str]]:
        return {
            PortType.input: {index: name for index, name in enumerate(input_port_names(self.node_type, self._input_capacity))},
            PortType.output: {index: name for index, name in enumerate(output_port_names(self.node_type))},
        }

    @property
    def port_caption_visible(self) -> dict[PortType, dict[int, bool]]:
        return {
            PortType.input: {index: True for index in range(self._input_capacity)},
            PortType.output: {index: True for index in range(self._output_capacity)},
        }

    @property
    def data_type(self) -> dict[PortType, dict[int, NodeDataType]]:
        input_type = TX_PROPERTY_TYPE if self.node_type == "OutputSubmodel" else TX_VALUE_TYPE
        output_type = TX_PROPERTY_TYPE if self.node_type == "OutputProperty" else TX_VALUE_TYPE
        return {
            PortType.input: {index: input_type for index in range(self._input_capacity)},
            PortType.output: {index: output_type for index in range(self._output_capacity)},
        }

    def embedded_widget(self):
        return None

    def out_data(self, port: int) -> NodeData:
        value = self.label_text or self.node_type
        if self.node_type == "OutputProperty":
            return TxPropertyData(value)
        return TxValueData(value)

    def set_in_data(self, node_data: NodeData, port: Port) -> None:
        return None

    def save(self) -> dict[str, object]:
        return {
            "name": self.node_type,
            "node_type": self.node_type,
            "tx_node_id": self.tx_node_id,
            "label": self.label_text,
            "config": deepcopy(self.config),
            "input_capacity": self._input_capacity,
        }

    def restore(self, doc: dict) -> None:
        self.node_type = clean_cell(doc.get("node_type") or doc.get("name") or self.node_type) or self.node_type
        self.tx_node_id = clean_cell(doc.get("tx_node_id", self.tx_node_id)) or self.tx_node_id
        self.label_text = clean_cell(doc.get("label", self.label_text))
        config = doc.get("config", {})
        self.config = dict(config if isinstance(config, dict) else {})
        try:
            self._input_capacity = max(0, int(doc.get("input_capacity", self._input_capacity)))
        except (TypeError, ValueError):
            self._input_capacity = resolve_input_capacity(self.node_type, 0)
        self._output_capacity = output_port_count(self.node_type)
        self.name = self.node_type
        self.caption = _display_caption(self.node_type, self.label_text)

    def update_label(self, label: str) -> None:
        self.label_text = clean_cell(label)
        self.caption = _display_caption(self.node_type, self.label_text)
        self.embedded_widget_size_updated.emit()

    def update_config(self, config: dict[str, Any]) -> None:
        self.config = dict(config)


class TxFlowScene(FlowScene):
    constraint_failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(registry=DataModelRegistry(), style=create_scene_style(), parent=parent)
        self.tooltip_language = "en"
        self.tooltip_source_type = ""

    def create_tx_node(
        self,
        node_type: str,
        *,
        node_id: str,
        label: str = "",
        config: dict[str, Any] | None = None,
        position: tuple[float, float] | QPointF | None = None,
        input_capacity: int | None = None,
    ) -> Node:
        model = TxNodeDataModel(
            node_type=node_type,
            tx_node_id=node_id,
            label=label,
            config=config,
            input_capacity=input_capacity,
            style=create_scene_style(node_type),
        )
        node = Node(model)
        node._uid = node_id
        node.graphics_object = StableNodeGraphicsObject(self, node)
        self._nodes[node.id] = node
        if position is not None:
            node.position = position
        self.node_created.emit(node)
        self.node_placed.emit(node)
        node.graphics_object.refresh_tooltip()
        return node

    def create_connection(self, port_a: Port, port_b: Port = None, *, converter=None, check_cycles=True) -> Connection:
        if port_a is not None and port_b is not None:
            in_port = port_a if port_a.port_type == PortType.input else port_b
            out_port = port_b if port_a.port_type == PortType.input else port_a
            in_node_type = getattr(in_port.node.model, "node_type", "")
            out_node_type = getattr(out_port.node.model, "node_type", "")
            if out_node_type == "OutputProperty" and in_node_type != "OutputSubmodel":
                raise ConnectionDataTypeFailure("OutputProperty nodes can only connect to OutputSubmodel nodes.")
            if in_node_type == "OutputSubmodel" and out_node_type != "OutputProperty":
                raise ConnectionDataTypeFailure("OutputSubmodel nodes only accept OutputProperty inputs.")
        return super().create_connection(port_a, port_b, converter=converter, check_cycles=check_cycles)

    def selected_connection_items(self) -> list[ConnectionGraphicsObject]:
        return [item for item in self.selectedItems() if isinstance(item, ConnectionGraphicsObject)]

    def refresh_node_tooltips(self) -> None:
        for node in self.nodes.values():
            graphics_object = getattr(node, "graphics_object", None)
            if graphics_object is not None and hasattr(graphics_object, "refresh_tooltip"):
                graphics_object.refresh_tooltip()


class TxFlowView(FlowView):
    node_create_requested = Signal(str, QPointF)
    SCENE_PADDING = 240.0

    def __init__(self, scene: TxFlowScene, parent=None) -> None:
        super().__init__(scene, parent=parent)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def scene(self):
        return self._scene

    def refresh_scene_bounds(self, *, center_on_contents: bool = False, padding: float | None = None) -> None:
        scene = self.scene()
        if scene is None:
            return
        bounds = scene.itemsBoundingRect()
        if not bounds.isValid() or bounds.isEmpty():
            current = scene.sceneRect()
            if current.isValid() and not current.isEmpty():
                bounds = QRectF(current)
            else:
                bounds = self.mapToScene(self.viewport().rect()).boundingRect()
        margin = max(0.0, float(self.SCENE_PADDING if padding is None else padding))
        padded = bounds.adjusted(-margin, -margin, margin, margin)
        current_center = None if center_on_contents else self.mapToScene(self.viewport().rect().center())
        scene.setSceneRect(padded)
        if center_on_contents:
            self.centerOn(padded.center())
        elif current_center is not None:
            self.centerOn(current_center)

    def _create_insert_menu(self, pos: QPoint) -> QMenu:
        menu = QMenu(self)
        filter_edit = QLineEdit(menu)
        filter_edit.setPlaceholderText("Search nodes")
        filter_edit.setClearButtonEnabled(True)
        filter_action = QWidgetAction(menu)
        filter_action.setDefaultWidget(filter_edit)
        menu.addAction(filter_action)

        tree = QTreeWidget(menu)
        tree.header().hide()
        tree_action = QWidgetAction(menu)
        tree_action.setDefaultWidget(tree)
        menu.addAction(tree_action)

        top_level: dict[str, QTreeWidgetItem] = {}
        for category, node_types in NODE_GROUPS.items():
            top_level_item = QTreeWidgetItem(tree)
            top_level_item.setText(0, category)
            top_level_item.setData(0, Qt.ItemDataRole.UserRole, "")
            top_level[category] = top_level_item
            for node_type in node_types:
                item = QTreeWidgetItem(top_level_item)
                item.setText(0, node_type)
                item.setData(0, Qt.ItemDataRole.UserRole, node_type)
        tree.expandAll()

        def handle_item_clicked(item: QTreeWidgetItem) -> None:
            node_type = clean_cell(item.data(0, Qt.ItemDataRole.UserRole))
            if not node_type:
                return
            self.node_create_requested.emit(node_type, self.mapToScene(pos))
            menu.close()

        def handle_filter_changed(text: str) -> None:
            needle = clean_cell(text).lower()
            for category_item in top_level.values():
                visible_children = 0
                for index in range(category_item.childCount()):
                    child = category_item.child(index)
                    visible = not needle or needle in child.text(0).lower()
                    child.setHidden(not visible)
                    if visible:
                        visible_children += 1
                category_item.setHidden(visible_children == 0)

        tree.itemClicked.connect(handle_item_clicked)
        filter_edit.textChanged.connect(handle_filter_changed)
        filter_edit.setFocus()
        return menu

    def _open_insert_menu(self, pos: QPoint) -> None:
        menu = self._create_insert_menu(pos)
        menu.exec(self.mapToGlobal(pos))

    def contextMenuEvent(self, event) -> None:
        if self.itemAt(event.pos()):
            super().contextMenuEvent(event)
            return
        self._open_insert_menu(event.pos())

    def keyPressEvent(self, event) -> None:
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace} or event.matches(QKeySequence.StandardKey.Delete):
            self.delete_selected()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Tab:
            self._open_insert_menu(self.viewport().rect().center())
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(PALETTE_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(PALETTE_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasFormat(PALETTE_MIME_TYPE):
            node_type = bytes(event.mimeData().data(PALETTE_MIME_TYPE)).decode("utf-8")
            if clean_cell(node_type):
                self.node_create_requested.emit(node_type, self.mapToScene(event.position().toPoint()))
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class TxGraphAdapter:
    layout_version = AUTO_LAYOUT_VERSION

    def __init__(self, scene: TxFlowScene) -> None:
        self.scene = scene
        self._template_rule_set = build_default_uc1_rule_set("instrument_list")

    def load_rule_set(self, rule_set: TxRuleSet) -> None:
        self._template_rule_set = deepcopy(rule_set)
        self.scene.clear_scene()
        incoming_counts: dict[str, int] = defaultdict(int)
        for edge in rule_set.edges:
            incoming_counts[edge.to_node] += 1

        node_lookup: dict[str, Node] = {}
        for tx_node in rule_set.nodes:
            node_lookup[tx_node.id] = self.scene.create_tx_node(
                tx_node.node_type,
                node_id=tx_node.id,
                label=tx_node.label,
                config=tx_node.config,
                position=tx_node.position,
                input_capacity=resolve_input_capacity(tx_node.node_type, incoming_counts.get(tx_node.id, 0)),
            )

        used_inputs: dict[str, set[int]] = defaultdict(set)
        for edge in sorted(rule_set.edges, key=lambda item: (item.order, item.id)):
            source = node_lookup.get(edge.from_node)
            target = node_lookup.get(edge.to_node)
            if source is None or target is None:
                continue
            source_index = 0
            target_capacity = target.model.num_ports[PortType.input]
            target_index = resolve_target_port_index(
                getattr(target.model, "node_type", ""),
                edge.target_port,
                used_inputs[target.id],
                target_capacity,
            )
            connection = self.scene.create_connection_by_index(target, target_index, source, source_index, converter=None)
            setattr(connection, "tx_edge_id", edge.id)
            setattr(connection, "tx_edge_order", edge.order)
            setattr(connection, "tx_source_port", edge.source_port)
            setattr(connection, "tx_target_port", edge.target_port)
            used_inputs[target.id].add(target_index)

    def create_node(self, node_type: str, position: QPointF | tuple[float, float]) -> Node:
        if node_type not in ALLOWED_TX_NODE_TYPES:
            raise ValueError(f"Unsupported node type: {node_type}")
        node_id = self._unique_node_id(node_type)
        label = node_type
        config = default_tx_config(node_type)
        return self.scene.create_tx_node(
            node_type,
            node_id=node_id,
            label=label,
            config=config,
            position=position,
            input_capacity=resolve_input_capacity(node_type, 0),
        )

    def to_rule_set(
        self,
        *,
        source_type: str | None = None,
        view_state: dict[str, object] | None = None,
    ) -> TxRuleSet:
        template = deepcopy(self._template_rule_set)
        node_ids: list[str] = []
        tx_nodes: list[TxNode] = []
        for node in sorted(self.scene.nodes.values(), key=lambda item: (item.position.x(), item.position.y(), item.id)):
            model = node.model
            position = node.position
            tx_node = TxNode(
                id=getattr(model, "tx_node_id", node.id) or node.id,
                node_type=getattr(model, "node_type", ""),
                label=getattr(model, "label_text", ""),
                position=(round(position.x(), 1), round(position.y(), 1)),
                config=deepcopy(getattr(model, "config", {})),
            )
            tx_nodes.append(tx_node)
            node_ids.append(tx_node.id)

        edge_rows: list[tuple[str, str, int, int, str, Connection]] = []
        for connection in self.scene.connections:
            if not connection.is_complete:
                continue
            source_node = connection.output_node
            target_node = connection.input_node
            edge_rows.append(
                (
                    getattr(source_node.model, "tx_node_id", source_node.id) or source_node.id,
                    getattr(target_node.model, "tx_node_id", target_node.id) or target_node.id,
                    connection.get_port_index(PortType.output),
                    connection.get_port_index(PortType.input),
                    getattr(connection, "tx_edge_id", ""),
                    connection,
                )
            )
        edge_rows.sort(key=lambda item: (item[1], item[3], item[0], item[2], item[4]))

        tx_edges: list[TxEdge] = []
        for order, (source_id, target_id, source_index, target_index, preserved_id, connection) in enumerate(edge_rows):
            source_node = next(node for node in tx_nodes if node.id == source_id)
            target_node = next(node for node in tx_nodes if node.id == target_id)
            edge_id = preserved_id or self._unique_edge_id(source_id, target_id, order)
            tx_edges.append(
                TxEdge(
                    id=edge_id,
                    from_node=source_id,
                    to_node=target_id,
                    source_port=clean_cell(getattr(connection, "tx_source_port", "")) or output_port_key(source_node.node_type, source_index),
                    target_port=clean_cell(getattr(connection, "tx_target_port", "")) or input_port_key(target_node.node_type, target_index),
                    order=order,
                )
            )

        metadata = deepcopy(template.metadata)
        if view_state:
            metadata.setdefault("ui", {})
            metadata["ui"].update(view_state)

        return TxRuleSet(
            source_type=clean_cell(source_type or template.source_type) or template.source_type,
            version=template.version,
            title=template.title,
            description=template.description,
            workbook_kind=template.workbook_kind,
            primary_sheet_name=template.primary_sheet_name,
            identity_fields=list(template.identity_fields),
            nodes=tx_nodes,
            edges=tx_edges,
            metadata=metadata,
        )

    def export_selection(self) -> dict[str, object]:
        selected_nodes = self.scene.selected_nodes()
        if not selected_nodes:
            return {}
        selected_ids = {getattr(node.model, "tx_node_id", node.id) or node.id for node in selected_nodes}
        rule_set = self.to_rule_set()
        nodes = [node.model_dump(mode="json") for node in rule_set.nodes if node.id in selected_ids]
        edges = [edge.model_dump(mode="json") for edge in rule_set.edges if edge.from_node in selected_ids and edge.to_node in selected_ids]
        min_x = min(node["position"][0] for node in nodes)
        min_y = min(node["position"][1] for node in nodes)
        return {
            "nodes": nodes,
            "edges": edges,
            "anchor": [min_x, min_y],
        }

    def paste_selection(self, fragment: dict[str, object], position: QPointF | tuple[float, float]) -> list[str]:
        nodes_payload = fragment.get("nodes", [])
        edges_payload = fragment.get("edges", [])
        if not isinstance(nodes_payload, list) or not nodes_payload:
            return []
        try:
            anchor_x, anchor_y = fragment.get("anchor", [0.0, 0.0])
        except (TypeError, ValueError):
            anchor_x, anchor_y = 0.0, 0.0
        if not isinstance(position, QPointF):
            px, py = position
            position = QPointF(px, py)
        existing_ids = {getattr(node.model, "tx_node_id", node.id) or node.id for node in self.scene.nodes.values()}
        id_map: dict[str, str] = {}
        created_ids: list[str] = []
        created_nodes: dict[str, Node] = {}
        for raw_node in nodes_payload:
            tx_node = TxNode.model_validate(raw_node)
            new_id = self._next_unique_id(tx_node.id, existing_ids)
            existing_ids.add(new_id)
            id_map[tx_node.id] = new_id
            offset_x = tx_node.position[0] - float(anchor_x)
            offset_y = tx_node.position[1] - float(anchor_y)
            created_nodes[new_id] = self.scene.create_tx_node(
                tx_node.node_type,
                node_id=new_id,
                label=tx_node.label,
                config=tx_node.config,
                position=(position.x() + offset_x, position.y() + offset_y),
                input_capacity=resolve_input_capacity(tx_node.node_type, 0),
            )
            created_ids.append(new_id)

        used_inputs: dict[str, set[int]] = defaultdict(set)
        for raw_edge in edges_payload:
            edge = TxEdge.model_validate(raw_edge)
            source_id = id_map.get(edge.from_node)
            target_id = id_map.get(edge.to_node)
            if not source_id or not target_id:
                continue
            source = created_nodes[source_id]
            target = created_nodes[target_id]
            target_capacity = target.model.num_ports[PortType.input]
            target_index = resolve_target_port_index(target.model.node_type, edge.target_port, used_inputs[target_id], target_capacity)
            connection = self.scene.create_connection_by_index(target, target_index, source, 0, converter=None)
            setattr(connection, "tx_edge_id", self._unique_edge_id(source_id, target_id, len(self.scene.connections)))
            used_inputs[target_id].add(target_index)

        return created_ids

    def arrange_scene(
        self,
        *,
        left: float = 120.0,
        top: float = 90.0,
        column_gap: float = 72.0,
        row_gap: float = 24.0,
    ) -> None:
        if not self.scene.nodes:
            return
        indegree: dict[str, int] = {node_id: 0 for node_id in self.scene.nodes}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for connection in self.scene.connections:
            if not connection.is_complete:
                continue
            source_id = clean_cell(getattr(connection.output_node.model, "tx_node_id", connection.output_node.id)) or connection.output_node.id
            target_id = clean_cell(getattr(connection.input_node.model, "tx_node_id", connection.input_node.id)) or connection.input_node.id
            outgoing[source_id].append(target_id)
            indegree[target_id] = indegree.get(target_id, 0) + 1

        queue = deque(sorted((node_id for node_id, degree in indegree.items() if degree == 0), key=lambda item: self._layout_sort_key(self.scene.nodes[item])))
        levels: dict[str, int] = {node_id: 0 for node_id in indegree}
        seen: set[str] = set()
        while queue:
            current = queue.popleft()
            seen.add(current)
            for target_id in outgoing.get(current, []):
                levels[target_id] = max(levels.get(target_id, 0), levels[current] + 1)
                indegree[target_id] -= 1
                if indegree[target_id] == 0:
                    queue.append(target_id)

        for node_id in self.scene.nodes:
            levels.setdefault(node_id, 0)

        columns: dict[int, list[Node]] = defaultdict(list)
        for node_id, node in self.scene.nodes.items():
            columns[levels.get(node_id, 0)].append(node)

        column_positions: dict[int, float] = {}
        current_x = left
        for level in sorted(columns):
            column_positions[level] = current_x
            current_x += self._column_width(columns[level]) + column_gap

        for level, nodes in sorted(columns.items()):
            current_y = top
            for node in sorted(nodes, key=self._layout_sort_key):
                node.position = (column_positions[level], current_y)
                current_y += self._node_height(node) + row_gap

    def apply_view_state(self, view: FlowView, rule_set: TxRuleSet) -> None:
        ui = rule_set.metadata.get("ui", {}) if isinstance(rule_set.metadata, dict) else {}
        zoom = ui.get("zoom")
        if isinstance(zoom, (int, float)) and zoom > 0:
            current = view.transform().m11()
            if current > 0:
                factor = float(zoom) / current
                view.scale(factor, factor)

    def collect_view_state(self, view: FlowView, *, last_selected_node: str = "") -> dict[str, object]:
        return {
            "zoom": round(view.transform().m11(), 3),
            "last_selected_node": clean_cell(last_selected_node),
            "layout_version": self.layout_version,
        }

    def _layout_sort_key(self, node: Node) -> tuple[int, float, str]:
        node_type = clean_cell(getattr(node.model, "node_type", ""))
        node_id = clean_cell(getattr(node.model, "tx_node_id", node.id)) or node.id
        return (
            NODE_LAYOUT_RANK.get(node_type, 999),
            float(node.position.y()) if node.position is not None else 0.0,
            node_id,
        )

    def _unique_node_id(self, node_type: str) -> str:
        existing = {getattr(node.model, "tx_node_id", node.id) or node.id for node in self.scene.nodes.values()}
        base = normalize_identifier(node_type) or "node"
        index = 1
        while f"{base}_{index}" in existing:
            index += 1
        return f"{base}_{index}"

    def _next_unique_id(self, base_id: str, existing: set[str]) -> str:
        cleaned = normalize_identifier(base_id) or "node"
        candidate = cleaned
        index = 2
        while candidate in existing:
            candidate = f"{cleaned}_{index}"
            index += 1
        return candidate

    def _unique_edge_id(self, source_id: str, target_id: str, order: int) -> str:
        existing = {
            getattr(connection, "tx_edge_id", "")
            for connection in self.scene.connections
            if getattr(connection, "tx_edge_id", "")
        }
        base = normalize_identifier(f"edge_{source_id}_{target_id}_{order}") or f"edge_{order}"
        candidate = base
        index = 2
        while candidate in existing:
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    def _column_width(self, nodes: list[Node]) -> float:
        if not nodes:
            return 0.0
        return max(self._node_width(node) for node in nodes)

    def _node_width(self, node: Node) -> float:
        return max(150.0, float(node.geometry.bounding_rect.width()))

    def _node_height(self, node: Node) -> float:
        return max(84.0, float(node.geometry.bounding_rect.height()))
