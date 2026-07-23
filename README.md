# Minecraft Bedrock CUE schemas

Minecraft Bedrock Add-On の作者向け JSON を、**JSON 種別 / `format_version`** ごとに検証する CUE スキーマです。単一の superset ではなく、各データ契約が独立した `#Schema` を公開します。

```cue
package myaddon

import entity "example.com/mcue/bedrock/behavior/entity"

entity: entity.#Schema & {
	format_version: "1.26.0"
	"minecraft:entity": {
		description: identifier: "example:fire_beetle"
		components: "minecraft:health": value: 20
	}
}
```

代表的な import は次のとおりです。

```cue
import entity "example.com/mcue/bedrock/behavior/entity"
import recipe "example.com/mcue/bedrock/behavior/recipe"
import loot "example.com/mcue/bedrock/behavior/loot_table"
import manifest "example.com/mcue/bedrock/behavior/manifest"
import render "example.com/mcue/bedrock/resource/render_controller"
```

Behavior Pack は `bedrock/behavior`、Resource Pack は `bedrock/resource` 以下へ分離されます。各種別の `#Schema` は JSON 内の `format_version` を判定し、対応する版のデータ契約を参照します。`format_version` のない loot table、trading、sound、texture index、UI なども各 scope 内の種別単位パッケージです。既知フィールドの型、必須 wrapper、format literal、recipe root の相互排他を検証し、将来のフィールドとカスタム component は許容します。

## 検証

```powershell
.\cue\cue.exe vet -c=false .\example\...
python -m unittest discover -s tests -v
python tools\generate_schemas.py check
python tools\generate_schemas.py vet-data
```

## 再収集と生成

IR はインストール済み Bedrock データと公式形式を表す [schema_seeds.json](tools/schema_seeds.json) から収集し、CUE は IR だけから決定的に生成します。seed はローカル配布に例がない block 等を補い、Block schema には既知 component の具体的な型が含まれます。

```powershell
python tools\generate_schemas.py collect
python tools\generate_schemas.py generate
```

データ元の既定値は `C:\XboxGames\Minecraft for Windows\Content\data` です。`--data` または `MC_BEDROCK_DATA` で変更できます。カタログ外 JSON、解析失敗、分類重複、生成パス衝突はエラーになります。作者向けでない JSON は [schema_catalog.json](tools/schema_catalog.json) の pack 除外または明示的 fallback 理由として監査件数に残ります。

破壊的 API 変更からの移行方法は [MIGRATION.md](MIGRATION.md) を参照してください。形式の選定根拠は Microsoft Learn の [Version Disambiguation](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/versiondisambiguation?view=minecraft-bedrock-stable) と [Comprehensive Pack Contents](https://learn.microsoft.com/en-us/minecraft/creator/documents/comprehensivepackcontents?view=minecraft-bedrock-stable) です。
