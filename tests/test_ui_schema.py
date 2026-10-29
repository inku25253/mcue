from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import generate_schemas as generator
from bedrock_source import PackFile


class UiGeneratorTests(unittest.TestCase):
    def test_pattern_fields_coexist_with_fixed_fields_and_round_trip(self) -> None:
        value = {
            "kinds": ["object"],
            "fields": {"namespace": {"kinds": ["string"]}},
            "pattern_fields": [
                {"regex": "^\\$", "shape": {"kinds": ["number"]}},
                {"exclude": ["namespace"], "exclude_regex": "^\\$", "shape": {"kinds": ["ref:#Element"]}},
            ],
        }
        shape = generator.Shape.from_dict(value)
        self.assertEqual(shape.as_dict(), value)
        merged = generator.Shape()
        merged.merge(shape)
        merged.merge(shape)
        self.assertEqual(merged.as_dict(), value)
        rendered = generator.emit_shape(merged)
        self.assertIn('"namespace"?: string', rendered)
        self.assertIn('[key=string & =~"^\\\\$"]', rendered)
        self.assertIn('[key=string & !~"^\\\\$" & !="namespace"]: #Element', rendered)

    def test_expression_and_disjunction_constraints_round_trip(self) -> None:
        value = {
            "kinds": [],
            "alternatives": [
                {"kinds": [], "literals": ["left", "right"]},
                {"kinds": ["string"], "string_pattern": "^\\$"},
            ],
        }
        shape = generator.Shape.from_dict(value)
        self.assertEqual(shape.as_dict(), value)
        merged = generator.Shape()
        merged.merge(shape)
        merged.merge(shape)
        self.assertEqual(merged.as_dict(), value)
        self.assertIn('=~"^\\\\$"', generator.emit_shape(shape))

    def test_conditional_fields_round_trip_without_adding_selector(self) -> None:
        value = {
            "kinds": ["object"],
            "fields": {"target": {"kinds": ["string"]}},
            "conditional_fields": [{"field": "target", "equals": "controls", "fields": {"value": {"kinds": ["ref:#Controls"]}}}],
        }
        shape = generator.Shape.from_dict(value)
        self.assertEqual(shape.as_dict(), value)
        merged = generator.Shape()
        merged.merge(shape)
        merged.merge(shape)
        self.assertEqual(merged.as_dict(), value)
        self.assertIn('Selector0="target"?: string', generator.emit_shape(shape))
        self.assertIn('if Selector0 != _|_', generator.emit_shape(shape))
        self.assertIn('if Selector0 == "controls"', generator.emit_shape(shape))

    def test_property_group_intersections_and_conditional_references_round_trip(self) -> None:
        value = {
            "kinds": ["object"],
            "fields": {"type": {"kinds": ["string"]}},
            "all_of": [{"kinds": ["ref:#CommonProperties"]}],
            "conditional_fields": [
                {"field": "type", "equals": "panel", "shape": {"kinds": ["ref:#Panel"]}},
                {"field": "type", "shape": {"kinds": ["ref:#ElementProperties"]}},
            ],
        }
        shape = generator.Shape.from_dict(value)
        self.assertEqual(shape.as_dict(), value)
        merged = generator.Shape()
        merged.merge(shape)
        merged.merge(shape)
        self.assertEqual(merged.as_dict(), value)
        rendered = generator.emit_shape(shape)
        self.assertIn("& #CommonProperties", rendered)
        self.assertIn("\n\t\t\t#Panel\n", rendered)
        self.assertIn("\n\t\t#ElementProperties\n", rendered)

    def test_type_definitions_reuse_documented_property_groups(self) -> None:
        common = generator.load_json(ROOT / "tools/ui_schema.json")
        definitions = common["definitions"]
        types = common["provenance"]["element_types"]
        self.assertEqual(len(types), 29)
        self.assertEqual(types["panel"]["definition"], "Panel")
        self.assertEqual(types["label"]["property_groups"], ["TextProperties", "ControlProperties", "LayoutProperties", "DataBindingProperties"])
        self.assertIn({"kinds": ["ref:#SpriteProperties"]}, definitions["Image"]["all_of"])
        self.assertNotIn("text", definitions["Image"]["fields"])
        self.assertEqual(definitions["Animation"]["all_of"][1], {"kinds": ["ref:#AnimationProperties"]})
        self.assertIn("compatibility_properties", types["image"])

    def test_collection_uses_common_contract_and_retains_source_audit(self) -> None:
        common = generator.load_json(ROOT / "tools/ui_schema.json")
        catalog = generator.load_json(ROOT / "tools/schema_catalog.json")
        documents = [
            {"namespace": "new", "never_observed_before": {"type": "panel"}},
            {"$global|default": {"nullable": None}},
            {"ui_defs": ["ui/custom.json"]},
        ]
        entries = [PackFile("resource", "vanilla", f"ui/test_{i}.json", f"test:{i}", json.dumps(doc).encode(), False) for i, doc in enumerate(documents)]
        with patch.object(generator, "iter_pack_files", return_value=iter(entries)):
            ir = generator.collect(ROOT, catalog, {"corrections": []})
        record = next(r for r in ir["schemas"] if r["family"] == "ui")
        self.assertEqual(record["files"], 3)
        self.assertEqual(ir["audit"]["families"]["ui"], 3)
        fingerprint = hashlib.sha256()
        for entry in entries:
            fingerprint.update(f"resource/vanilla/{entry.logical}\0".encode())
            fingerprint.update(hashlib.sha256(entry.payload).digest())
        self.assertEqual(ir["audit"]["input_sha256"], fingerprint.hexdigest())
        self.assertEqual(record["shape"], generator.Shape.from_dict(common["shape"]).as_dict())
        self.assertEqual(record["definitions"], common["definitions"])
        self.assertEqual(record["provenance"], common["provenance"])
        self.assertNotIn("never_observed_before", json.dumps(record))

    def test_generation_uses_only_ir(self) -> None:
        ir = generator.load_json(ROOT / "schema_ir.json")
        record = next(r for r in ir["schemas"] if r["family"] == "ui")
        with patch.object(generator, "load_json", side_effect=AssertionError("generation must not read source declarations")):
            source = generator.emit_schema(copy.deepcopy(record))
        self.assertEqual(source, (ROOT / "bedrock/resource/ui/schema.cue").read_text(encoding="utf-8"))
        self.assertEqual(set(record["shape"]["fields"]), {"namespace", "ui_defs"})
        self.assertIn("[key=string]: #Element", source)


class UiContractTests(unittest.TestCase):
    cue = str(os.environ.get("CUE") or shutil.which("cue") or shutil.which("cue.exe") or ROOT / "cue/cue.exe")

    def run_cue(self, source: str, command: str = "vet") -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="cue-test-ui-", dir=ROOT) as directory:
            fixture = Path(directory) / "fixture.cue"
            fixture.write_text(source, encoding="utf-8")
            arguments = [self.cue, command] + (["-c=true"] if command == "vet" else [])
            return subprocess.run(arguments + [str(fixture)], cwd=ROOT, text=True, capture_output=True)

    def vet(self, document: dict, valid: bool) -> None:
        source = 'package fixture\nimport ui "github.com/inku25253/mcue/bedrock/resource/ui"\nx: ui.#Schema & ' + json.dumps(document, ensure_ascii=False)
        result = self.run_cue(source)
        if valid:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, "invalid UI unexpectedly passed")

    def test_arbitrary_names_inheritance_recursive_controls_and_expressions(self) -> None:
        self.vet({
            "namespace": "author.namespace.with.any.text",
            "新規要素": {
                "type": "panel", "visible": "(not $hidden)", "offset": ["$x", "50%-2px"],
                "controls": [{"child@missing.template": {
                    "size": ["fill", 20], "alpha": "@animations.fade",
                    "controls": [{"grandchild@$template": {"type": "label", "text": "#text"}}],
                }}],
                "$configuration|default": {"nested": [True, None, {"key": 1}]},
                "$nothing": None, "future_property": [1, {"x": True}],
            },
            "patch/root/child": {"enabled": "$enabled"},
            "$dynamic_name@$template": {"type": "$type"},
        }, True)

    def test_system_files_and_variables(self) -> None:
        for document in [
            {"ui_defs": ["ui/custom.json", "ui/other.json"]},
            {"$object": {"x": None}, "$null|default": None, "$array": [1, "x", False]},
            {"namespace": "", "ui_defs": []},
            {"e": {"variables": {"requires": "$condition", "$x": [1, 2]}}},
            {"e": {"variables": [{"requires": "($a > 1)", "$x|default": None}]}},
            {"e": {"variables": "$variables", "controls": "$controls", "bindings": "$bindings"}},
        ]:
            with self.subTest(document=document):
                self.vet(document, True)

    def test_animation_binding_mapping_property_bag_and_modifications(self) -> None:
        self.vet({
            "fade": {"anim_type": "alpha", "easing": "in_out_quad", "duration": 0.2, "from": 0, "to": 1, "next": "absent.next"},
            "move": {"anim_type": "offset", "from": [0, "100%"], "to": "$end", "initial_uv": [0, 0], "fps": 20},
            "e@unknown.base": {
                "bindings": [{"binding_type": "view", "binding_condition": "visibility_changed", "source_property_name": "(#state = 1)", "target_property_name": "#visible"}],
                "button_mappings": [{"from_button_id": "button.menu_ok", "to_button_id": "$action", "mapping_type": "pressed", "scope": "view"}],
                "property_bag": {"#visible": "$shown", "#block_position": [1, 2, 3], "#custom": {"x": None}},
                "modifications": [
                    {"array_name": "controls", "operation": "insert_front", "value": [{"new@$template": {"size": [10, 20], "controls": [{"deep": {"visible": False}}]}}]},
                    {"array_name": "bindings", "operation": "replace", "where": {"binding_name": "#old"}, "value": {"binding_name": "#new"}},
                    {"array_name": "button_mappings", "operation": "remove", "where": {"mapping_type": "pressed"}},
                    {"control_name": "old", "operation": "move_before", "value": [{"new@template": {}}]},
                    {"array_name": "custom_array", "operation": "swap", "where": {"id": 1}, "target": {"id": 2}},
                ],
            },
        }, True)

    def test_malformed_document_shapes_are_rejected(self) -> None:
        for document in [
            {"element": 1}, {"element": None}, {"element": []},
            {"namespace": 1}, {"ui_defs": "ui/custom.json"}, {"ui_defs": [1]},
            {"parent": {"controls": {"child": {}}}},
            {"parent": {"controls": [1]}},
            {"parent": {"controls": [{"child": 1}]}},
            {"parent": {"controls": [{"child@$template": {"visible": 1}}]}},
            {"$dynamic@$template": 1},
            {"e": {"variables": [1]}}, {"e": {"variables": {"requires": 1}}},
        ]:
            with self.subTest(document=document):
                self.vet(document, False)

    def test_known_property_types_enums_and_vector_lengths_are_rejected(self) -> None:
        invalid = [
            ("type", "invalid"), ("anim_type", "invalid"), ("easing", "invalid"),
            ("visible", "true"), ("layer", 1.5), ("duration", False), ("fps", 1.5),
            ("anchor_from", "invalid"), ("orientation", "diagonal"), ("renderer", "invalid"),
            ("font_size", "gigantic"), ("tiled", "z"), ("draggable", "diagonal"),
            ("size", [1]), ("size", [1, 2, 3]), ("size", [True, 2]),
            ("offset", [0]), ("uv", [0, 0, 0]), ("initial_uv", [0]),
            ("grid_dimensions", [1, 1.5]), ("grid_position", [1, 2, 3]),
            ("nineslice_size", [1, 2, 3]), ("locked_color", [1, 2]),
            ("color", [1, 2, 3, 4, 5]), ("color", "invalid"), ("color1", [1, 2, 3]),
            ("from", [0]), ("to", [0, 0, 0, 0, 0]),
            ("max_size", [1]), ("size", ["invalid"]),
            ("bindings", {}), ("bindings", [1]), ("bindings", [{"binding_type": "invalid"}]),
            ("bindings", [{"binding_condition": "invalid"}]), ("bindings", [{"binding_name": 1}]),
            ("button_mappings", ["button.menu_ok"]), ("button_mappings", [{"mapping_type": "invalid"}]),
            ("sounds", [{"sound_volume": False}]), ("property_bag", []),
            ("property_bag", {"#block_position": [1, 2]}), ("property_bag", {"#visible": 1}),
            ("modifications", {}), ("modifications", [1]), ("modifications", [None]),
            ("modifications", [False]), ("modifications", [[]]), ("modifications", [{"operation": "invalid"}]),
            ("modifications", [{"array_name": 1}]), ("modifications", [{"where": []}]),
            ("modifications", [{"array_name": "controls", "value": [{"child": {"size": [1]}}]}]),
            ("modifications", [{"array_name": "bindings", "value": [{"binding_name": 1}]}]),
        ]
        for prop, value in invalid:
            with self.subTest(property=prop, value=value):
                self.vet({"new_name@unknown.base": {prop: value}}, False)

    def test_compatibility_is_limited_to_documented_properties(self) -> None:
        self.vet({"e": {
            "type": "image",
            "color": [0, 0, 0, 1], "max_size": [], "size": ["100%, 100%"],
            "priority": 2,
        }, "custom": {"type": "custom", "renderer": "qr_code_renderer"},
            "label": {"type": "label", "font_size": "medium"},
            "wheel": {"type": "selection_wheel", "state_controls": [{"control_name": "default_state"}]},
            "input": {"type": "tooltip_trigger", "button_mappings": [{"scope": "global", "input_mode_condition": "gamepad"}]},
        }, True)
        self.vet({"e": {"color": "black"}}, True)
        self.vet({"e": {"locked_color": "black"}}, False)
        self.vet({"e": {"offset": []}}, False)
        self.vet({"e": {"offset": ["100%, 100%"]}}, False)

    def test_each_element_type_and_public_definition(self) -> None:
        common = generator.load_json(ROOT / "tools/ui_schema.json")
        lines = ['package fixture', 'import ui "github.com/inku25253/mcue/bedrock/resource/ui"']
        for index, (kind, info) in enumerate(common["provenance"]["element_types"].items()):
            document = {"type": kind, "size": [10, 20], "future_property": {"x": None}}
            lines.append(f'element{index}: ui.#Element & {json.dumps(document)}')
            lines.append(f'direct{index}: ui.#{info["definition"]} & {json.dumps(document)}')
        result = self.run_cue("\n".join(lines))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_type_specific_properties(self) -> None:
        for element in [
            {"type": "label", "text": "Hello", "font_size": "normal"},
            {"type": "image", "texture": "textures/ui/White", "uv": [0, 0]},
            {"type": "grid", "grid_dimensions": [2, 3], "collection_name": "items"},
            {"type": "button", "default_control": "default", "focus_enabled": True},
            {"type": "slider", "slider_steps": 10, "slider_direction": "horizontal"},
            {"type": "screen", "absorbs_input": True, "button_mappings": []},
            {"type": "factory", "factory": {"name": "items", "control_ids": {"item": "entry@templates.item"}}},
            {"type": "panel", "focus_container": True},
            {"type": "panel", "$fit_to_width": True},
        ]:
            with self.subTest(element=element):
                self.vet({"e": element}, True)
        for element in [
            {"type": "label", "texture": "textures/ui/White"},
            {"type": "image", "text": "Hello"},
            {"type": "panel", "orientation": "vertical"},
            {"type": "panel", "slider_steps": 10},
            {"type": "button", "grid_dimensions": [2, 3]},
            {"type": "label", "text": 123},
            {"type": "image", "texture": False},
            {"type": "grid", "grid_dimensions": [2]},
            {"type": "slider", "slider_steps": 1.5},
            {"type": "label", "color": [1, 2]},
            {"type": "panel", "duration": 1},
            {"type": "image", "focus_container": True},
        ]:
            with self.subTest(element=element):
                self.vet({"e": element}, False)

    def test_type_dispatch_applies_to_recursive_and_modified_controls(self) -> None:
        self.vet({"root": {"type": "panel", "controls": [{"label": {"type": "label", "text": "ok"}}]}}, True)
        self.vet({"root": {"type": "panel", "controls": [{"label": {"type": "label", "texture": "textures/ui/White"}}]}}, False)
        self.vet({"root": {"modifications": [{"array_name": "controls", "value": [{"image": {"type": "image", "text": "invalid"}}]}]}}, False)

    def test_inherited_or_variable_type_does_not_infer_a_concrete_type(self) -> None:
        for element in [
            {"text": "override", "texture": "textures/ui/White"},
            {"type": "$element_type", "text": "override", "texture": "textures/ui/White"},
            {"type": "($use_image ? 'image' : 'label')", "font_size": "large"},
        ]:
            self.vet({"inherited@unknown.template": element}, True)
        self.vet({"e": {"type": "$element_type", "text": 123}}, False)
        self.vet({"e": {"type": "$element_type", "texture": False}}, False)

    def test_animation_is_separate_from_control_types(self) -> None:
        self.vet({"fade": {"anim_type": "alpha", "duration": 0.2, "from": 0, "to": 1}}, True)
        self.vet({"animation": {"anim_type": "aseprite_flip_book", "orientation": "vertical"}}, True)
        self.vet({"fade": {"anim_type": "alpha", "text": "invalid"}}, False)
        self.vet({"fade": {"anim_type": "alpha", "type": "label"}}, False)
        self.vet({"fade": {"anim_type": "alpha", "type": "$type"}}, False)
        result = self.run_cue('package fixture\nimport ui "github.com/inku25253/mcue/bedrock/resource/ui"\nx: ui.#Animation & {anim_type: "wait", duration: 1}\ny: ui.#Label & {text: "Hello"}\n', "export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["y"], {"text": "Hello"})

    def test_export_does_not_insert_defaults_or_resolve_references(self) -> None:
        document = {"namespace": "custom", "inherited@missing.template": {}, "animation": {"next": "absent.animation"}, "e": {"modifications": [{"operation": "remove", "control_name": "old"}]}}
        result = self.run_cue('package fixture\nimport ui "github.com/inku25253/mcue/bedrock/resource/ui"\nui.#Schema & ' + json.dumps(document), "export")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), document)


if __name__ == "__main__":
    unittest.main()
