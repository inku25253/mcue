package tetris

import (
	"github.com/inku25253/mcue/bedrock/resource/client_entity"
	"github.com/inku25253/mcue/bedrock/resource/geometry"
	"github.com/inku25253/mcue/bedrock/resource/manifest"
	"github.com/inku25253/mcue/bedrock/resource/render_controller"
	"github.com/inku25253/mcue/bedrock/resource/ui"
)

_rp: "development_resource_packs/tetris"

// Vanilla concrete per tetris:color (I, O, T, S, Z, J, L). Color 0 is hidden.
_colors: ["white", "light_blue", "yellow", "purple", "lime", "red", "blue", "orange"]

pack: "\(_rp)/manifest.json": manifest.#Schema & {
	format_version: 2
	header: {
		name:        "Tetris"
		description: "mcue sample: Tetris (resources)"
		uuid:        _uuid.rp
		version: [1, 0, 0]
		min_engine_version: [1, 26, 0]
	}
	modules: [{type: "resources", uuid: _uuid.rp_res, version: [1, 0, 0]}]
}

pack: "\(_rp)/entity/cell.entity.json": client_entity.#Schema & {
	format_version: "1.10.0"
	"minecraft:client_entity": description: {
		identifier: "tetris:cell"
		materials: default: "entity"
		textures: {for i, c in _colors {"c\(i)": "textures/blocks/concrete_\(c)"}}
		geometry: default: "geometry.tetris.cell"
		render_controllers: ["controller.render.tetris.cell"]
	}
}

_face: {uv: [0, 0], uv_size: [16, 16]}

pack: "\(_rp)/models/entity/cell.geo.json": geometry.#Schema & {
	format_version: "1.12.0"
	"minecraft:geometry": [{
		description: {
			identifier:            "geometry.tetris.cell"
			texture_width:         16
			texture_height:        16
			visible_bounds_width:  2
			visible_bounds_height: 2
			visible_bounds_offset: [0, 0.5, 0]
		}
		// 15px cube: the 1px gap draws the grid.
		bones: [{
			name: "cell"
			pivot: [0, 0, 0]
			cubes: [{
				origin: [-7.5, 0.5, -7.5]
				size: [15, 15, 15]
				uv: {north: _face, south: _face, east: _face, west: _face, up: _face, down: _face}
			}]
		}]
	}]
}

pack: "\(_rp)/render_controllers/cell.render_controllers.json": render_controller.#Schema & {
	format_version: "1.8.0"
	render_controllers: "controller.render.tetris.cell": {
		arrays: textures: "Array.colors": [for i, _ in _colors {"Texture.c\(i)"}]
		geometry: "Geometry.default"
		materials: [{"*": "Material.default"}]
		textures: ["Array.colors[q.property('tetris:color')]"]
		part_visibility: [{"*": "q.property('tetris:color') > 0"}]
		ignore_lighting: true
	}
}

// main.js sends the score panel as a title starting with "tetris:".
_title:    "#hud_title_text_string"
_isTetris: "(('%.7s' * \(_title)) = 'tetris:')"

pack: "\(_rp)/ui/hud_screen.json": ui.#Schema & {
	namespace: "hud"
	// Hide the vanilla title for those titles...
	hud_title_text: bindings: [
		{binding_name: _title},
		{binding_type: "view", source_property_name: "(not \(_isTetris))", target_property_name: "#visible"},
	]
	// ...and show them in a side panel instead.
	root_panel: modifications: [{
		array_name: "controls"
		operation:  "insert_back"
		value: [{"tetris_panel@hud.tetris_panel": {}}]
	}]
	tetris_panel: {
		type:    "image"
		texture: "textures/ui/Black"
		alpha:   0.6
		size: ["100%c + 16px", "100%c + 16px"]
		anchor_from: "right_middle"
		anchor_to:   "right_middle"
		offset: [-24, 0]
		bindings: [
			{binding_name: _title},
			{binding_type: "view", source_property_name: _isTetris, target_property_name: "#visible"},
		]
		controls: [{stats: {
			type:   "label"
			text:   "#text"
			shadow: true
			bindings: [
				{binding_name: _title},
				{binding_type: "view", source_property_name: "(\(_title) - 'tetris:')", target_property_name: "#text"},
			]
		}}]
	}
}
