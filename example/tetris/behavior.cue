@extern(embed)

package tetris

import (
	"github.com/inku25253/mcue/bedrock/behavior/entity"
	"github.com/inku25253/mcue/bedrock/behavior/manifest"
)

_uuid: {
	bp:        "eb496723-7996-4b09-9f4a-2a26a684739f"
	bp_data:   "f08f718a-45a5-499d-a317-9bd351c7a373"
	bp_script: "a43f5cdd-2076-4f2c-b486-da3979a654d3"
	rp:        "6be78f55-66f9-49a5-9508-947be6b9b466"
	rp_res:    "be65550d-41b1-45c4-80c6-e4a763a6b722"
}

_bp: "development_behavior_packs/tetris"

// Output path -> JSON document. build_tool.cue writes them out.
pack: [string]: _

// Script files copied into the behavior pack as-is.
scripts: _ @embed(glob=scripts/*.js, type=text)

pack: "\(_bp)/manifest.json": manifest.#Schema & {
	format_version: 2
	header: {
		name:        "Tetris"
		description: "mcue sample: Tetris (behavior)"
		uuid:        _uuid.bp
		version: [1, 0, 0]
		min_engine_version: [1, 26, 0]
	}
	modules: [
		{type: "data", uuid: _uuid.bp_data, version: [1, 0, 0]},
		{type: "script", language: "javascript", entry: "scripts/main.js", uuid: _uuid.bp_script, version: [1, 0, 0]},
	]
	dependencies: [
		{module_name: "@minecraft/server", version: "2.0.0"},
		{uuid: _uuid.rp, version: [1, 0, 0]},
	]
}

// One entity per board cell; the script sets tetris:color and the render controller shows it.
pack: "\(_bp)/entities/cell.json": entity.#Schema & {
	format_version: "1.26.0"
	"minecraft:entity": {
		description: {
			identifier:    "tetris:cell"
			is_spawnable:  false
			is_summonable: true
			properties: "tetris:color": {type: "int", range: [0, 7], default: 0, client_sync: true}
		}
		components: {
			"minecraft:physics": {has_gravity: false, has_collision: false}
			"minecraft:collision_box": {width: 0.1, height: 0.1}
			"minecraft:damage_sensor": triggers: {cause: "all", deals_damage: "no"}
			"minecraft:pushable": {is_pushable: false, is_pushable_by_piston: false}
		}
	}
}
