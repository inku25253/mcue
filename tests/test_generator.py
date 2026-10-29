from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import generate_schemas as generator


class GeneratorTests(unittest.TestCase):
    def test_comment_removal_preserves_urls_and_comment_markers_in_strings(self) -> None:
        source = '{/* lead */"url":"https://example.test/a/*b*/","value":1// tail\n}'
        self.assertEqual(json.loads(generator.strip_json_comments(source))["url"], "https://example.test/a/*b*/")

    def test_shape_preserves_type_unions_and_null(self) -> None:
        shape = generator.Shape()
        for value in (1, 1.5, "one", None, [1], {"x": True}):
            shape.add(value)
        self.assertEqual(shape.kinds, {"int", "number", "string", "null", "list", "object"})

    def test_required_correction_is_applied(self) -> None:
        shape = generator.Shape()
        shape.add({"header": {"name": "x"}})
        corrections = {"corrections": [{"family": "manifest", "path": ["header", "name"], "required": True}]}
        generator.apply_corrections("manifest", shape, corrections)
        self.assertTrue(shape.fields["header"].fields["name"].required)

    def test_recursive_correction_applies_to_nested_event_responses(self) -> None:
        shape = generator.Shape()
        shape.add({
            "events": {
                "test:direct": {"set_property": {"test:direct": True}},
                "test:nested": {
                    "sequence": [
                        {"first_valid": [{"set_property": {"test:nested": "query.value"}}]}
                    ]
                },
            }
        })
        corrections = {"corrections": [{
            "family": "entity",
            "path": ["events", "**", "set_property"],
            "dynamic_map": True,
            "contract": "entity_set_property",
            "create": False,
        }]}
        generator.apply_corrections("entity", shape, corrections)
        direct = shape.fields["events"].fields["test:direct"].fields["set_property"]
        nested = (
            shape.fields["events"].fields["test:nested"].fields["sequence"].element
            .fields["first_valid"].element.fields["set_property"]
        )
        for node in (direct, nested):
            self.assertTrue(node.dynamic_map)
            self.assertEqual(node.contract, "entity_set_property")

    def test_contract_correction_round_trips_through_ir(self) -> None:
        shape = generator.Shape()
        shape.add({"properties": {"example:value": {"type": "bool", "default": False}}})
        corrections = {"corrections": [{
            "family": "entity",
            "path": ["properties"],
            "dynamic_map": True,
            "contract": "entity_property",
        }]}
        generator.apply_corrections("entity", shape, corrections)
        properties = shape.fields["properties"]
        self.assertTrue(properties.dynamic_map)
        self.assertEqual(properties.contract, "entity_property")
        self.assertEqual(generator.Shape.from_dict(shape.as_dict()).fields["properties"].contract, "entity_property")

    def test_version_keys_do_not_use_game_folder_versions(self) -> None:
        self.assertEqual(generator.version_key("1.20.10"), "1.20.10")
        self.assertEqual(generator.version_key([1, 1, 0]), "1.1.0")
        self.assertEqual(generator.package_version("1.20.10"), "v1_20_10")

    def test_duplicate_classification_fails(self) -> None:
        catalog = {"excluded_packs": {"behavior": {}}, "families": [
            {"scope": "behavior", "family": "a", "paths": ["items/*.json"]},
            {"scope": "behavior", "family": "b", "paths": ["items/x.json"]},
        ], "fallback_exclusion": "excluded"}
        with self.assertRaises(generator.SchemaError):
            generator.classify(catalog, "behavior", "pack", "items/x.json")

    def test_internal_feature_root_is_excluded(self) -> None:
        rule = {
            "excluded_roots": {
                "minecraft:sculk_patch_feature": "internal feature type unsupported in custom content"
            }
        }
        self.assertEqual(
            generator.excluded_root_reason(rule, ["minecraft:sculk_patch_feature"]),
            "internal feature type unsupported in custom content",
        )
        self.assertIsNone(generator.excluded_root_reason(rule, ["minecraft:ore_feature"]))

    def test_generation_is_deterministic_and_collision_is_fatal(self) -> None:
        ir = json.loads((ROOT / "schema_ir.json").read_text(encoding="utf-8"))
        files = generator.generated_files(ir)
        self.assertEqual(files, generator.generated_files(copy.deepcopy(ir)))
        self.assertIn(Path("behavior/manifest/v2/schema.cue"), files)
        self.assertIn(Path("behavior/manifest/schema.cue"), files)
        self.assertIn(Path("resource/manifest/v2/schema.cue"), files)
        self.assertIn(Path("resource/manifest/schema.cue"), files)
        self.assertNotIn(Path("manifest/v2/schema.cue"), files)
        duplicate = copy.deepcopy(ir)
        duplicate["schemas"].append(copy.deepcopy(duplicate["schemas"][0]))
        with self.assertRaises(generator.SchemaError):
            generator.generated_files(duplicate)

    def test_family_schema_dispatches_by_format_version(self) -> None:
        ir = json.loads((ROOT / "schema_ir.json").read_text(encoding="utf-8"))
        source = generator.generated_files(ir)[Path("behavior/entity/schema.cue")]
        self.assertIn('FormatVersion="format_version": (', source)
        self.assertIn('if FormatVersion == "1.26.0" {', source)
        self.assertIn("v1_26_0.#Schema", source)

    def test_entity_components_share_one_definition(self) -> None:
        shape = generator.Shape()
        shape.add({
            "minecraft:entity": {
                "components": {"base:only": {"value": 1}},
                "component_groups": {
                    "group": {"group:only": {"enabled": True}}
                },
            }
        })
        definitions = generator.unify_entity_components(shape)
        shared = definitions["EntityComponents"]
        self.assertEqual(set(shared.fields), {"base:only", "group:only"})
        entity = shape.fields["minecraft:entity"]
        self.assertEqual(entity.fields["components"].kinds, {"ref:#EntityComponents"})
        group_value = next(iter(entity.fields["component_groups"].fields.values()))
        self.assertEqual(group_value.kinds, {"ref:#EntityComponents"})

    def test_bottom_constraint_preserves_field_names(self) -> None:
        record = {"scope": "resource", "family": "fixture", "version": None, "docs": "fixture", "roots": [], "shape": {"kinds": ["object"], "fields": {"bottom": {"kinds": ["string"]}, "forbidden": {"kinds": ["bottom"]}}}}
        emitted = generator.emit_schema(record)
        self.assertIn('"bottom"?: string', emitted)
        self.assertIn('"forbidden"?: _|_', emitted)


class CueContractTests(unittest.TestCase):
    cue = Path(
        os.environ.get("CUE")
        or shutil.which("cue")
        or shutil.which("cue.exe")
        or ROOT / "cue" / "cue.exe"
    )

    def vet(self, source: str, valid: bool) -> None:
        with tempfile.TemporaryDirectory(prefix="cue-test-", dir=ROOT) as directory:
            path = Path(directory) / "fixture.cue"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run([str(self.cue), "vet", "-c=true", str(path)], cwd=ROOT, text=True, capture_output=True)
        if valid and result.returncode:
            self.fail(result.stdout + result.stderr)
        if not valid and result.returncode == 0:
            self.fail("fixture unexpectedly passed CUE validation")

    def test_entity_events_null_and_unknown_component(self) -> None:
        self.vet('''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/entity"
x: s.#Schema & {format_version: "1.26.0", "minecraft:entity": {description: {identifier: "test:entity"}, events: null, components: {"test:custom": {x: true}}}}
''', True)

    def test_entity_event_set_property_is_a_typed_dynamic_map(self) -> None:
        source = (ROOT / "bedrock" / "behavior" / "entity" / "v1_26_0" / "schema.cue").read_text(encoding="utf-8")
        set_property_blocks = source.split('"set_property"?: {')[1:]
        self.assertTrue(set_property_blocks)
        for block in set_property_blocks:
            self.assertIn("[string]: (bool | number | string)", block.split("}", 1)[0])

        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/entity"
x: s.#Schema & {format_version: "1.26.0", "minecraft:entity": {description: {identifier: "test:entity"}, events: {"test:event": %s}}}
'''
        self.vet(base % '{set_property: {"test:flag": true, "test:count": 2, "test:expression": "query.variant"}}', True)
        self.vet(base % '{sequence: [{first_valid: [{set_property: {"test:count": 2.5}}]}]}', True)
        self.vet(base % '{set_property: {"test:invalid": {value: 1}}}', False)

    def test_generated_entity_uses_shared_component_definition(self) -> None:
        source = (ROOT / "bedrock" / "behavior" / "entity" / "v1_26_0" / "schema.cue").read_text(encoding="utf-8")
        self.assertIn("#EntityComponents:", source)
        self.assertEqual(source.count("#EntityComponents"), 3)
        self.assertIn('"components"?: #EntityComponents', source)
        self.assertIn("[string]: #EntityComponents", source)

    def test_entity_description_properties_are_dynamic(self) -> None:
        source = (ROOT / "bedrock" / "behavior" / "entity" / "v1_26_0" / "schema.cue").read_text(encoding="utf-8")
        properties = source.split('"properties"?: {', 1)[1].split('"spawn_category"?:', 1)[0]
        self.assertIn("[string]: ({", properties)
        self.assertNotIn('"minecraft:', properties)
        valid = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/entity"
x: s.#Schema & {format_version: "1.26.0", "minecraft:entity": {description: {identifier: "test:entity", properties: {"test:value": %s}}}}
'''
        self.vet(valid % '{type: "bool", default: false}', True)
        self.vet(valid % '{type: "int", range: [0, 10], default: "math.random(0, 10)"}', True)
        self.vet(valid % '{type: "float", range: [0.0, 1.0], default: 0.5}', True)
        self.vet(valid % '{type: "enum", values: ["idle", "active"], default: "idle", client_sync: true}', True)
        self.vet(valid % '{type: "bool", range: [0, 1], default: false}', False)
        self.vet(valid % '{type: "int", range: [0, 10], default: false}', False)
        self.vet(valid % '{type: "float", default: 0.5}', False)
        self.vet(valid % '{type: "enum", values: ["idle"], default: true}', False)
        for version in ("1.26.40", "1.26.50", "1.26.60"):
            modern = valid.replace('"1.26.0"', f'"{version}"')
            self.vet(modern % '{type: "float", range: [0, 1], default: 0.5}', True)
            self.vet(modern % '{type: "float", default: true}', False)

    def test_spawn_herd_object_and_array(self) -> None:
        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/spawn_rule"
x: s.#Schema & {format_version: "1.17.0", "minecraft:spawn_rules": {description: {identifier: "test:entity"}, conditions: [{"minecraft:herd": %s}]}}
'''
        self.vet(base % '{min_size: 1, max_size: 2}', True)
        self.vet(base % '[{min_size: 1, max_size: 2}]', True)

    def test_material_reduction_legacy_and_modern_input(self) -> None:
        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/recipe"
x: s.#Schema & {format_version: "1.20.10", "minecraft:recipe_material_reduction": {description: {identifier: "test:r"}, input: %s}}
'''
        self.vet(base % '"minecraft:iron_block"', True)
        self.vet(base % '{item: "minecraft:iron_block"}', True)

    def test_render_controller_part_visibility_forms(self) -> None:
        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/resource/render_controller"
x: s.#Schema & {format_version: "1.8.0", render_controllers: {"controller.render.test": {part_visibility: %s}}}
'''
        self.vet(base % '{body: "query.is_visible"}', True)
        self.vet(base % '[{body: "query.is_visible"}]', True)

    def test_manifest_dependency_versions(self) -> None:
        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/manifest"
x: s.#Schema & {format_version: 2, header: {name: "n", description: "d", uuid: "u", version: [1,0,0]}, modules: [{type: "data", uuid: "m", version: [1,0,0]}], dependencies: [{uuid: "d", version: %s}]}
'''
        self.vet(base % '[1,0,0]', True)
        self.vet(base % '"1.0.0"', True)

    def test_geometry_dispatches_legacy_and_versioned_shapes(self) -> None:
        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/resource/geometry"
x: s.#Schema & %s
'''
        self.vet(base % '{"geometry.custom": {}}', True)
        self.vet(base % '{format_version: "1.12.0", "minecraft:geometry": []}', True)
        self.vet(base % '{format_version: "1.12.0", "minecraft:geometry": "invalid"}', False)

    def test_feature_requires_its_official_contract(self) -> None:
        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/feature"
x: s.#Schema & %s
'''
        aggregate = '{format_version: "1.13.0", "minecraft:aggregate_feature": %s}'
        self.vet(base % (aggregate % '{description: {identifier: "test:aggregate"}, features: ["test:first"]}'), True)
        self.vet(base % (aggregate % '{features: ["test:first"]}'), False)
        self.vet(base % (aggregate % '{description: {identifier: "test:aggregate"}}'), False)
        sequence = '{format_version: "1.13.0", "minecraft:sequence_feature": {description: {identifier: "test:sequence"}, features: ["test:first", "test:second"]}}'
        self.vet(base % sequence, True)
        multiple = '{format_version: "1.13.0", "minecraft:aggregate_feature": {description: {identifier: "test:aggregate"}, features: ["test:first"]}, "minecraft:sequence_feature": {description: {identifier: "test:sequence"}, features: ["test:second"]}}'
        self.vet(base % multiple, False)
        height_filter = '{format_version: "1.26.30", "minecraft:height_difference_filter_feature": {description: {identifier: "test:height_filter"}, places_feature: "test:placed", min_required_downward_height_diff: 5, search_radius: 5}}'
        self.vet(base % height_filter, True)
        horizontal_decoration = '{format_version: "1.26.30", "minecraft:horizontal_tree_decoration_feature": {description: {identifier: "test:horizontal_decoration"}, allow_adjacent: false, bark_side_only: true}}'
        self.vet(base % horizontal_decoration, True)
        sculk = '{format_version: "1.16.0", "minecraft:sculk_patch_feature": {description: {identifier: "test:sculk"}}}'
        self.vet(base % sculk, False)

    def test_block_components_are_typed_and_open(self) -> None:
        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/block"
x: s.#Schema & {format_version: "1.26.0", "minecraft:block": {description: {identifier: "test:block"}, components: {"minecraft:destroy_time": %s, "test:custom": {future: true}}}}
'''
        self.vet(base % '2.5', True)
        self.vet(base % '"slow"', False)
        material = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/block"
x: s.#Schema & {format_version: "1.26.0", "minecraft:block": {description: {identifier: "test:block"}, components: {"minecraft:material_instances": {custom_face: {texture: 42}}}}}
'''
        self.vet(material, False)

    def test_negative_contracts(self) -> None:
        recipe = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/recipe"
x: s.#Schema & %s
'''
        self.vet(recipe % '{format_version: "1.20.10"}', False)
        self.vet(recipe % '{format_version: "1.20.10", "minecraft:recipe_furnace": {description: {identifier: "x:r"}}, "minecraft:recipe_shapeless": {description: {identifier: "x:s"}}}', False)
        entity = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/entity"
x: s.#Schema & %s
'''
        self.vet(entity % '{format_version: "9.9.9", "minecraft:entity": {description: {identifier: "x:e"}}}', False)
        self.vet(entity % '{format_version: "1.26.0"}', False)
        self.vet(entity % '{format_version: "1.26.0", "minecraft:entity": {description: {identifier: 42}}}', False)

    def test_modern_block_collision_and_experimental_components(self) -> None:
        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/block"
x: s.#Schema & {format_version: "1.26.60", "minecraft:block": {description: {identifier: "test:block"}, components: %s}}
'''
        self.vet(base % '{"minecraft:collision_box": [{origin: [-8, 0, -8], size: [16, 24, 16]}], "test:custom": {enabled: true}}', True)
        self.vet(base % '{"minecraft:collision_box": [{min: [0,0,0], max: [16,24,16]}], "minecraft:neighbor_change": {neighbor_directions: ["north", "up"]}}', True)
        self.vet(base % '{"minecraft:collision_box": false}', True)
        self.vet(base % '{"minecraft:collision_box": {origin: [-8,0,-8], size: [16,16,16]}}', False)
        self.vet(base % '{"minecraft:collision_box": [{min: [0,0], max: [16,24,16]}]}', False)
        self.vet(base % '{"minecraft:neighbor_change": {neighbor_directions: ["sideways"]}}', False)
        legacy = base.replace('"1.26.60"', '"1.26.0"')
        self.vet(legacy % '{"minecraft:collision_box": {origin: [-8,0,-8], size: [16,16,16]}}', True)

    def test_versioned_loot_table_and_legacy_import(self) -> None:
        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/loot_table"
x: s.#Schema & %s
'''
        self.vet(base % '{pools: []}', True)
        self.vet(base % '{format_version: "1.26.60", pools: []}', True)
        self.vet(base % '{format_version: "1.26.60", pools: [{rolls: "many"}]}', False)
        self.vet(base % '{format_version: "9.9.9", pools: []}', False)

    def test_new_entity_constraints_and_shared_components(self) -> None:
        base = '''package fixture
import s "github.com/inku25253/mcue/bedrock/behavior/entity"
x: s.#Schema & {format_version: "%s", "minecraft:entity": {description: {identifier: "test:entity"}, components: %s}}
'''
        self.vet(base % ('1.26.60', '{"minecraft:movement.jump": {jump_delay: {min: 0.2, max: 0.5}}, "minecraft:rideable": {seats: []}, "minecraft:trail": {radius: 16, force_replace_matched_blocks: true}}'), True)
        self.vet(base % ('1.26.60', '{"minecraft:movement.jump": {jump_delay: [0.2, 0.5]}}'), False)
        self.vet(base % ('1.26.60', '{"minecraft:rideable": {seats: {position: [0,0,0]}}}'), False)
        self.vet(base % ('1.26.60', '{"minecraft:trail": {radius: 17}}'), False)
        self.vet(base % ('1.26.60', '{"minecraft:buoyant": {movement_type: "sideways"}}'), False)
        self.vet(base % ('1.26.60', '{"minecraft:transformation": {delay: {value: -1}}}'), False)
        self.vet(base % ('1.26.60', '{"minecraft:transformation": {delay: {block_chance: 1.1}}}'), False)
        self.vet(base % ('1.26.60', '{"minecraft:transformation": {begin_transform_sound: "event"}}'), False)
        self.vet(base % ('1.26.60', '{"minecraft:healable": {items: ["apple"]}}'), False)
        self.vet(base % ('1.26.60', '{"minecraft:healable": {items: [{item: "apple", effects: {name: "speed"}}]}}'), False)
        self.vet(base % ('1.26.60', '{"minecraft:attack": {damage: [1, 2]}}'), False)
        self.vet(base % ('1.26.0', '{"minecraft:attack": {damage: [1, 2]}}'), True)
        for version in ('1.26.50', '1.26.60'):
            self.vet(base % (version, '{"minecraft:timer": {time: {min: 1, max: 2}}}'), True)
            self.vet(base % (version, '{"minecraft:timer": {time: 2}}'), version == '1.26.50')
        grouped = base.replace('components: %s', 'component_groups: {custom: %s}')
        self.vet(grouped % ('1.26.60', '{"minecraft:trail": {radius: -2}}'), False)
        self.vet(base % ('beta', '{"test:custom": {enabled: true}}'), True)

if __name__ == "__main__":
    unittest.main()
