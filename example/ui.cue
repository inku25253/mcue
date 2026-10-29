package example

import ui "github.com/inku25253/mcue/bedrock/resource/ui"

customUI: ui.#Schema & {
	namespace: "custom"
	"my_panel@common.empty_panel": {
		size: ["100%", 40]
		controls: [{
			"title": {
				type: "label"
				text: "$title"
			}
		}]
		"$title|default": "Hello"
	}
}

titleElement: ui.#Label & {
	type: "label"
	text: "Hello"
	font_size: "normal"
}
