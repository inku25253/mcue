package example

import entityschema "github.com/inku25253/mcue/bedrock/behavior/entity"

entity: entityschema.#Schema & {
	format_version: "1.26.0"
	"minecraft:entity": {
		description: {
			identifier:     "example:fire_beetle"
			is_spawnable:   true
			is_summonable:  true
			spawn_category: "monster"
		}
		components: {
			"minecraft:":{},
			"minecraft:health": {value: 20}
			"minecraft:movement": {value: 0.25}
			"example:future_component": {enabled: true}
		}
	}
}
