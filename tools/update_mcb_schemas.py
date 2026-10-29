"""Refresh the pinned, offline MCB schema dependency closure (standard library only)."""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BDS_COMMIT = "079f8bb8b45f38bafd30d01e392cb93c4357ce94"
BDS_URL = f"https://github.com/bedrock-apis/bds-docs/tree/{BDS_COMMIT}"
ROOT_TITLES = {
    "particle_effect": ["particle_effect"],
    "minecraft:voxel_shape": ["VoxelShapeFile"],
    "minecraft:item": ["Item Document", "Item Document (Beta)"],
    "tiers": ["Trade Table"],
    "minecraft:crafting_items_catalog": ["Crafting Catalog Document"],
    "minecraft:camera_entity": ["Camera Document"],
}
CAMERA_SOURCE = "https://github.com/LiteLDev/LeviLamina/blob/ee049f1dc6062cc027c397556af234fa6b18353e/src/mc/deps/shared_types/v1_21_100/camera/CameraDocument.h"


def schema_id(value: str) -> str:
    return posixpath.normpath("/" + urllib.parse.unquote(value).replace("\\", "/").lstrip("/")).lower()


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for nested in value.values():
            yield from references(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from references(nested)


def main() -> None:
    url = f"https://codeload.github.com/bedrock-apis/bds-docs/zip/{BDS_COMMIT}"
    with urllib.request.urlopen(url, timeout=120) as response:
        archive_bytes = response.read()
    schemas = {}
    hashes = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in sorted(archive.namelist()):
            marker = "/metadata/json_schemas/"
            if marker not in name or not name.endswith(".json"):
                continue
            raw = archive.read(name)
            document = json.loads(raw)
            if "$id" in document:
                key = schema_id(document["$id"])
                if key in schemas:
                    raise ValueError(f"duplicate schema ID: {key}")
                schemas[key] = document
                hashes[key] = hashlib.sha256(raw).hexdigest()

    # BDS exports the component container but omits the client-only envelope.
    # Field identity/order is corroborated by CameraDocument.h, the existing
    # readable camera documents, and exact consumption of death.json's MCB.
    camera_id = "/mcue/camera/1.26.10/Camera_Document.json"
    schemas[schema_id(camera_id)] = {
        "$id": camera_id, "title": "Camera Document", "x-format-version": "1.26.10",
        "type": "object", "required": ["description", "components"],
        "properties": {
            "description": {
                "type": "object", "x-ordinal-index": 0, "required": ["identifier"],
                "properties": {"identifier": {"type": "string", "x-ordinal-index": 0}},
            },
            "components": {
                "$ref": "/server/camera/1.21.100/CameraDefinitions.json", "x-ordinal-index": 1,
            },
        },
        "$comment": f"mcue compatibility envelope; evidence: {CAMERA_SOURCE}",
    }
    wanted_titles = {t.lower() for titles in ROOT_TITLES.values() for t in titles}
    pending = [key for key, doc in schemas.items() if doc.get("title", "").lower() in wanted_titles]
    included = {}
    while pending:
        key = pending.pop()
        if key in included:
            continue
        document = schemas[key]
        included[key] = document
        for ref in references(document):
            path = ref.split("#", 1)[0]
            if path:
                target = schema_id(posixpath.join(posixpath.dirname(key), path))
                if target not in schemas:
                    raise ValueError(f"missing reference {ref} in {key}")
                pending.append(target)
    bundle = {
        "bundle_version": 1,
        "source": {
            "repository": BDS_URL, "commit": BDS_COMMIT, "engine_version": "1.26.60",
            "build_version": "1.26.60.6", "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "schema_sha256": {key: hashes[key] for key in sorted(included) if key in hashes},
            "camera_envelope_evidence": CAMERA_SOURCE,
            "decoding_reference": "https://github.com/LPaicen/brarchive-extractor/tree/v0.2.2",
        },
        "roots": ROOT_TITLES,
        "schemas": included,
    }
    target = HERE / "mcb_schemas.json"
    target.write_text(json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"saved {len(included)} pinned schemas to {target}")


if __name__ == "__main__":
    main()
