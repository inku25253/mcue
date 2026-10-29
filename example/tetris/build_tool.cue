// テトリスのサンプルアドオン（entity + JSON UI + Script API）。
//
// ビルド（example/tetris で実行）:
//   ..\..\cue\cue.exe cmd build
//     -> dist/development_behavior_packs/tetris, dist/development_resource_packs/tetris
// 開発フォルダへ直接出力する場合:
//   ..\..\cue\cue.exe cmd -t out="$env:APPDATA\Minecraft Bedrock\Users\Shared\games\com.mojang" build
//
// 遊び方: チートを有効にしたワールドで両パックを適用し、/scriptevent tetris:start を実行する。
//   A/D 移動、W 回転、S ソフトドロップ、Space ハードドロップ、Shift 終了。
package tetris

import (
	"encoding/json"
	pathpkg "path"
	"tool/file"
)

out: *"dist" | string @tag(out)

_files: {
	for k, v in pack {(k): json.Indent(json.Marshal(v), "", "  ") + "\n"}
	for k, v in scripts {"\(_bp)/\(k)": v}
}

command: build: {
	for k, v in _files {
		(k): {
			dir: file.MkdirAll & {path: pathpkg.Dir("\(out)/\(k)", pathpkg.Unix)}
			write: file.Create & {
				$after:   dir
				filename: "\(out)/\(k)"
				contents: v
			}
		}
	}
}
