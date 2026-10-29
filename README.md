# Minecraft Bedrock CUE schemas

Minecraft Bedrock Add-On の作者向け JSON を、**JSON 種別 / `format_version`** ごとに検証する CUE スキーマです。単一の superset ではなく、各データ契約が独立した `#Schema` を公開します。

2026-10-29時点の安定版 **1.26.60.5** の同梱データに更新済みです。180パッケージを公開し、旧形式と既存の実験的機能を保持しています。CUE v0.17.1で検証しています。

```cue
package myaddon

import entity "github.com/inku25253/mcue/bedrock/behavior/entity"

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
import entity "github.com/inku25253/mcue/bedrock/behavior/entity"
import recipe "github.com/inku25253/mcue/bedrock/behavior/recipe"
import loot "github.com/inku25253/mcue/bedrock/behavior/loot_table"
import manifest "github.com/inku25253/mcue/bedrock/behavior/manifest"
import render "github.com/inku25253/mcue/bedrock/resource/render_controller"
```

Behavior Pack は `bedrock/behavior`、Resource Pack は `bedrock/resource` 以下へ分離されます。各種別の `#Schema` は JSON 内の `format_version` を判定し、対応する版のデータ契約を参照します。版情報のない loot table、trading、sound、texture index、UI なども検証できます。loot table は任意の `format_version: "1.26.60"` にも対応します。既知フィールドの型、必須 wrapper、format literal、recipe root の相互排他を検証し、将来のフィールドとカスタム component は許容します。

## JSON UI

`bedrock/resource/ui` は任意の要素名と、再帰的な `controls` を `#Element` で検証します。具体的な `type` がある場合は `#Panel`・`#Label`・`#Image`・`#Button`・`#Grid` など29種類の定義へ振り分けます。Wikiの「Allowed Properties」に沿った24のプロパティ群を組み合わせ、既知の適用外プロパティを拒否します。たとえば `type: "label"` での `texture`、`type: "image"` での `text` はエラーです。未知のプロパティは引き続き許容します。継承名 (`name@namespace.template`、`name@$variable`) や階層パスによる部分上書きも受理します。

```cue
import ui "github.com/inku25253/mcue/bedrock/resource/ui"

screen: ui.#Schema & {
	namespace: "custom"
	"my_panel@common.empty_panel": {
		size: ["100%", 40]
		controls: [{"title": {type: "label", text: "$title"}}]
		"$title|default": "Hello"
	}
}
```

宣言元は [tools/ui_schema.json](tools/ui_schema.json) です。[Bedrock Wiki の入門](https://wiki.bedrock.dev/json-ui/json-ui-intro)と[詳細リファレンス](https://wiki.bedrock.dev/json-ui/json-ui-documentation)のレイアウト・描画・入力・要素固有プロパティ・アニメーション・変数・bindings・property bag・button mappings・modificationsを定義しています。`namespace` は任意の文字列、`ui_defs` は文字列配列として別に検証し、`$` 変数は object・null を含むJSON値を受理します。modificationsの既知の配列名には、直接指定と同じ値の検証を適用します。

明示された列挙値・ベクトル長・既知プロパティの型を検証し、変数・binding・アニメーション参照と括弧で始まる式を許容します。`type` の省略時や変数・式による指定時は、継承先の種別を推測せず、全既知プロパティの型だけを検証します。`#Label` などの定義は直接参照でき、その場合も `type` は自動挿入しません。factory設定オブジェクトは `#FactoryDefinition`、`type: "factory"` の要素は `#Factory` で検証します。

アニメーションは `#Animation` に分離し、`anim_type` があればアニメーションのプロパティを検証します。`type` と `anim_type` の同時指定は拒否します。既定値の挿入、参照先の存在確認、式の評価、ゲーム内の挙動や `anim_type` ごとの `from`・`to` の意味は検証しません。同梱形式との相違は型・プロパティごとの互換定義に限定し、出典・観測値・文書ハッシュを記録しています。Wiki未掲載の5種類の要素も互換定義として保持し、対応するプロパティ群の選定は互換性のための解釈として明示しています。空の `max_size` や特定の単一文字列による `size` といった同梱例の例外も、他のベクトルには適用しません。同梱UI全218文書を検証しています。

## 検証

```powershell
.\cue\cue.exe vet .\bedrock\... .\example\...
python -m unittest discover -s tests -v
python tools\generate_schemas.py check
python tools\generate_schemas.py vet-data
```

## 再収集と生成

IR はインストール済み Bedrock データと公式資料を根拠とする [シード](tools/schema_seeds.json)・[補正规則](tools/schema_corrections.json)から収集し、CUE は IR だけから決定的に生成します。UIは [共通定義](tools/ui_schema.json)をIRへ格納し、観測した要素名は列挙しません。UIも文書数と入力ハッシュの監査対象です。通常JSONに加え、`__brarchive` 内のJSONとMCBバイナリを読み取ります。シードは同梱データに例がない新形式を補います。`entity/vbeta` は取得元から消失した旧契約として[互換レコード](tools/schema_compatibility.json)に保持しています。

```powershell
python tools\generate_schemas.py collect
python tools\generate_schemas.py generate
```

データ元の既定値は `C:\XboxGames\Minecraft for Windows\Content\data` です。`--data` または `MC_BEDROCK_DATA` で変更できます。解析失敗、競合する重複文書、分類重複、生成パス衝突はエラーになります。作者向けでないJSONとカタログ外パスは [カタログ](tools/schema_catalog.json)の理由付き除外として監査件数に残ります。同一内容の重複は1文書として扱います。

MCBの復元には[BDS 1.26.60.6由来の固定スキーマ](tools/mcb_schemas.json)を使用します。必要な213スキーマと参照先、出典コミット、元ファイルのSHA-256を保存しており、通常の収集・生成・検証はネットワーク接続を必要としません。`collect` と `vet-data` の `--mcb-schemas` で別のスキーマ束を指定できます。スキーマ束の再取得は `python tools/update_mcb_schemas.py` で行います。

MCB復元は、フィールド順・格納型・component IDに従い、末尾まで完全に読み切った場合に成功します。未知の形式を推測して処理することはありません。元の空白・コメントや明示されていた既定値の有無は復元できません。カメラのルートはBDSエクスポートに欠けているため、公開型情報・既存JSON・実バイナリに基づく補助定義を使用しています。出典とライセンスは [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)を参照してください。

## 更新内容と検証結果

- 対象13,249文書を検証。除外1,127文書、同一内容の重複496件を監査しています。
- MCB全507件を復元：particle 214、voxel shape 218、item 26、item catalog 26、trading 22、camera 1。
- 新たに確認された1.26.40・1.26.50・1.26.60等の契約を追加し、既存161パッケージをすべて保持しています。
- 1.26.60のblock collision boxはbooleanまたは配列を検証します。entityの新版のrange・seat・healable・transformation・trail等も補正しています。実験的componentを使用する際のゲーム側の実験設定は、CUEの検証対象に含みません。
- `timer.time` は変更履歴が1.26.50での厳密化を記載していますが、同梱1.26.50形式には数値・配列が残り、BDSの厳密なスキーマは1.26.60です。1.26.50ではそれらとobjectを受理し、1.26.60ではobjectを要求します。根拠は補正规則に記録しています。

公式JSONとの照合は、同一の `format_version` を持つ有効なvanillaのMCBと[公式サンプル v1.26.60.5](https://github.com/Mojang/bedrock-samples/releases/tag/v1.26.60.5)を比較します。数値幅・色・既定値等の正規化を考慮し、406文書中403文書で一致を確認しました。残る3文書は、公式JSONと同梱MCBで `emitter_local_space.velocity` が異なります。同梱バイナリの値を保持し、[差異の記録](tools/sample_discrepancies.json)にファイルハッシュと実バイトを保存しています。

```powershell
# 公式のmin.zipをメモリ上へ取得し、公開SHA-256を検証して照合
python tools\verify_mcb_samples.py
# ダウンロード済みの同じZIPを使用する場合
python tools\verify_mcb_samples.py --samples path\to\bedrock-samples-v1.26.60.5-min.zip
```

スキーマは観測した文書と明示的な補正に基づきます。未観測の既知フィールドまで公式仕様を完全に網羅することは保証しません。MCB対応は現在のカタログに含まれる上記6種類が対象です。

形式の選定根拠は Microsoft Learn の [Version Disambiguation](https://learn.microsoft.com/en-us/minecraft/creator/reference/content/versiondisambiguation?view=minecraft-bedrock-stable)、[Comprehensive Pack Contents](https://learn.microsoft.com/en-us/minecraft/creator/documents/comprehensivepackcontents?view=minecraft-bedrock-stable)、[26.60公式変更履歴](https://feedback.minecraft.net/hc/en-us/articles/49852828563853-Minecraft-Bedrock-Edition-26-60-Changelog)です。
