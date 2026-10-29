"""Read Bedrock pack archives and schema-directed MCB documents offline.

MCB compatibility rules are informed by brarchive-extractor v0.2.2 (MIT).
See THIRD_PARTY_NOTICES.md and the pinned bundle's provenance. These rules
describe binary storage; they are not a general JSON Schema validator.
"""

from __future__ import annotations

import hashlib
import json
import math
import posixpath
import re
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote

from update_mcb_schemas import schema_id

ARCHIVE_MAGIC = 0x267052A0B125277D
MCB_MAGIC = b"\x7fMCB"
DEFAULT_SCHEMAS = Path(__file__).with_name("mcb_schemas.json")
MAX_ITEMS = 1_000_000


class SourceError(RuntimeError):
    pass


def read_archive(raw: bytes, source: str = "archive") -> Iterable[tuple[str, bytes]]:
    if len(raw) < 16:
        raise SourceError(f"{source}: truncated archive header")
    magic, count, version = struct.unpack_from("<QII", raw)
    base = 16 + count * 256
    if magic != ARCHIVE_MAGIC or version != 1 or base > len(raw):
        raise SourceError(f"{source}: invalid archive magic, version, or entry table")
    names = set()
    for index in range(count):
        at = 16 + index * 256
        length = raw[at]
        if not 1 <= length <= 247:
            raise SourceError(f"{source}: invalid entry name length")
        try:
            name = raw[at + 1:at + 1 + length].decode("utf-8")
        except UnicodeError as exc:
            raise SourceError(f"{source}: invalid entry UTF-8") from exc
        # Entries contain files directly inside the archive's logical directory.
        if name in (".", "..") or any(c in name for c in "/\\:\x00"):
            raise SourceError(f"{source}: unsafe archive entry {name!r}")
        if name.casefold() in names:
            raise SourceError(f"{source}: duplicate archive entry {name!r}")
        names.add(name.casefold())
        offset, size = struct.unpack_from("<II", raw, at + 248)
        if base + offset + size > len(raw):
            raise SourceError(f"{source}#/{name}: payload outside archive")
        yield name, raw[base + offset:base + offset + size]


@dataclass(frozen=True)
class PackFile:
    scope: str
    pack: str
    logical: str
    source: str
    payload: bytes
    archived: bool


def iter_pack_files(data: Path, audit: dict[str, Any]) -> Iterable[PackFile]:
    """Return unique logical JSON documents, including MCB stored as .json."""
    for scope, directory in (("behavior", "behavior_packs"), ("resource", "resource_packs")):
        root = data / directory
        if not root.is_dir():
            raise SourceError(f"missing data directory: {root}")
        for pack in sorted(p for p in root.iterdir() if p.is_dir()):
            files: dict[str, PackFile] = {}

            def add(logical: str, payload: bytes, source: str, archived: bool) -> None:
                key = logical.casefold()
                if key in files:
                    if files[key].payload != payload:
                        raise SourceError(f"conflicting pack documents: {files[key].source} and {source}")
                    audit["identical_duplicates"] = audit.get("identical_duplicates", 0) + 1
                    return
                files[key] = PackFile(scope, pack.name, logical, source, payload, archived)

            for path in sorted(pack.rglob("*.json")):
                if "__brarchive" in path.relative_to(pack).parts:
                    continue
                logical = path.relative_to(pack).as_posix()
                add(logical, path.read_bytes(), f"{directory}/{pack.name}/{logical}", False)
            for path in sorted((pack / "__brarchive").rglob("*.brarchive")):
                audit["archives"] = audit.get("archives", 0) + 1
                logical_dir = PurePosixPath(path.relative_to(pack / "__brarchive").as_posix()).with_suffix("")
                source = f"{directory}/{pack.name}/{path.relative_to(pack).as_posix()}"
                for name, payload in read_archive(path.read_bytes(), source):
                    if name.lower().endswith(".json"):
                        add((logical_dir / name).as_posix(), payload, f"{source}#/{name}", True)
            yield from (files[key] for key in sorted(files))


class Reader:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.raw):
            raise SourceError(f"truncated MCB at offset 0x{self.offset:x}")
        start = self.offset
        self.offset += size
        return self.raw[start:self.offset]

    def number(self, code: str) -> int | float:
        return struct.unpack("<" + code, self.take(struct.calcsize("<" + code)))[0]

    def boolean(self) -> bool:
        value = self.number("B")
        if value not in (0, 1):
            raise SourceError(f"invalid boolean {value} at offset 0x{self.offset - 1:x}")
        return bool(value)

    def varuint(self) -> int:
        result = 0
        for index in range(5):
            byte = self.number("B")
            if index == 4 and byte > 15:
                break
            result |= (byte & 127) << (7 * index)
            if byte < 128:
                return result
        raise SourceError(f"invalid VarUInt32 at offset 0x{self.offset:x}")

    def string(self) -> str:
        try:
            return self.take(self.varuint()).decode("utf-8")
        except UnicodeError as exc:
            raise SourceError(f"invalid MCB UTF-8 at offset 0x{self.offset:x}") from exc


def fnv1a(value: str) -> int:
    result = 0x811C9DC5
    for byte in value.encode("utf-8"):
        result = ((result ^ byte) * 0x01000193) & 0xFFFFFFFF
    return result


def numeric_version(value: str) -> tuple[int, ...] | None:
    return tuple(map(int, value.split("."))) if re.fullmatch(r"\d+(?:\.\d+)*", value) else None


class McbDecoder:
    def __init__(self, bundle: dict[str, Any]):
        self.bundle = bundle
        self.schemas = {schema_id(key): value for key, value in bundle["schemas"].items()}
        self.roots = {}
        for payload, titles in bundle["roots"].items():
            self.roots[payload] = sorted(
                (key for key, value in self.schemas.items() if value.get("title", "").lower() in {t.lower() for t in titles}),
            )
        self.used: set[str] = set()

    @classmethod
    def load(cls, path: Path = DEFAULT_SCHEMAS) -> "McbDecoder":
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError) as exc:
            raise SourceError(f"cannot load MCB schemas {path}: {exc}") from exc

    def select_root(self, payload: str, version: str) -> str:
        candidates = self.roots.get(payload, [])
        target = numeric_version(version)
        exact = [key for key in candidates if self.schemas[key].get("x-format-version") == version]
        if exact:
            return exact[0]
        if target is not None:
            compatible = [(numeric_version(self.schemas[key].get("x-format-version", "")), key) for key in candidates]
            compatible = [(v, key) for v, key in compatible if v is not None and v <= target]
            if compatible:
                return max(compatible)[1]
        raise SourceError(f"no compatible MCB schema for {payload!r} format {version!r}")

    def resolve(self, ref: str, current: str) -> tuple[dict[str, Any], str]:
        path, _, fragment = ref.partition("#")
        key = schema_id(posixpath.join(posixpath.dirname(current), path)) if path else current
        if key not in self.schemas:
            raise SourceError(f"missing MCB schema {ref!r} from {current}")
        node = self.schemas[key]
        if fragment:
            if not fragment.startswith("/"):
                raise SourceError(f"unsupported schema fragment: {ref}")
            try:
                for part in fragment[1:].split("/"):
                    part = unquote(part).replace("~1", "/").replace("~0", "~")
                    node = node[int(part)] if isinstance(node, list) else node[part]
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise SourceError(f"missing schema fragment: {ref}") from exc
        if not isinstance(node, dict):
            raise SourceError(f"invalid schema node: {ref}")
        self.used.add(key)
        return node, key

    def decode(self, raw: bytes) -> dict[str, Any]:
        self.reader = Reader(raw)
        if self.reader.take(4) != MCB_MAGIC:
            raise SourceError("invalid MCB magic")
        parts = tuple(self.reader.number("H") for _ in range(3))
        pre, build, payload = self.reader.string(), self.reader.string(), self.reader.string()
        version = "beta" if parts == (9999, 9999, 9999) and pre == "beta" and not build else ".".join(map(str, parts)) + ("-" + pre if pre else "") + ("+" + build if build else "")
        key = self.select_root(payload, version)
        self.used.add(key)
        value = self.node(self.schemas[key], key, "$", 0)
        if self.reader.offset != len(raw):
            raise SourceError(f"MCB trailing data: consumed {self.reader.offset} of {len(raw)} bytes")
        result = {payload: value}
        if payload != "tiers":
            result = {"format_version": version, **result}
        return result

    def count(self, fixed: int | None = None, code: str | None = None) -> int:
        value = fixed if fixed is not None else self.reader.number(code) if code else self.reader.varuint()
        if not 0 <= value <= MAX_ITEMS:
            raise SourceError(f"MCB container size {value} exceeds limit")
        return value

    def resolved_type(self, node: dict[str, Any], key: str) -> str | None:
        seen = set()
        while "$ref" in node:
            marker = (key, node["$ref"])
            if marker in seen:
                raise SourceError(f"cyclic MCB reference: {marker}")
            seen.add(marker)
            node, key = self.resolve(node["$ref"], key)
        return node.get("type")

    def node(self, node: dict[str, Any], key: str, path: str, depth: int) -> Any:
        if depth > 256:
            raise SourceError(f"MCB schema nesting exceeds limit at {path}")
        decode = lambda value, location=path: self.node(value, key, location, depth + 1)
        if "$ref" in node:
            resolved, target = self.resolve(node["$ref"], key)
            merged = {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
            return self.node(merged, target, path, depth + 1)
        if "oneOf" in node:
            variants = node["oneOf"]
            title = node.get("title", "").lower()
            if title == "tradeitemlist":
                items = [decode(variants[0], f"{path}.choice[{i}]") for i in range(self.count())]
                return items[0] if len(items) == 1 else {"choice": items}
            if any("x-ordinal-index" in v for v in variants) or title in {"particle_curve", "particle_appearance_tinting color_data"}:
                tag = self.reader.number("B")
                branches = [v for i, v in enumerate(variants) if v.get("x-ordinal-index", i) == tag]
                if len(branches) != 1:
                    raise SourceError(f"unknown MCB variant tag {tag} at {path}")
                return decode(branches[0])
            preferred = {
                "molang string": "string", "crafting catalog item": "string", "vectorevents": "array",
                "color_expr": "array", "particle_motion_collision_event_vector": "array", "vec3": "array",
                "item descriptor": "object", "minecraft:icon v1.21.80": "object", "trade quantity": "object",
                "minecraft:hand_equipped": "boolean", "minecraft:max_stack_size": "integer",
            }.get(title)
            branches = [v for v in variants if preferred and self.resolved_type(v, key) == preferred]
            if len(branches) == 1:
                return decode(branches[0])
            if len(variants) == 1:
                return decode(variants[0])
            raise SourceError(f"unsupported MCB oneOf {title!r} at {path} ({key})")
        kind = node.get("type", "object" if "properties" in node or "additionalProperties" in node else None)
        if kind == "string":
            value = self.reader.string()
            if "enum" in node and value not in node["enum"]:
                raise SourceError(f"invalid MCB enum {value!r} at {path}")
            return value
        if kind == "boolean":
            return self.reader.boolean()
        if kind in ("number", "integer"):
            code = {"uint8": "B", "int8": "b", "uint16": "H", "int16": "h", "uint32": "I", "int32": "i", "uint64": "Q", "int64": "q", "float": "f", "double": "d"}.get(node.get("x-underlying-type"))
            if code is None:
                raise SourceError(f"missing numeric MCB storage type at {path} ({key})")
            value = self.reader.number(code)
            if isinstance(value, float) and not math.isfinite(value):
                raise SourceError(f"non-finite MCB number at {path}")
            return value
        if kind == "array":
            items = node.get("items")
            if isinstance(items, list):
                return [decode(item, f"{path}[{i}]") for i, item in enumerate(items)]
            if not isinstance(items, dict):
                raise SourceError(f"missing MCB array schema at {path}")
            fixed = node.get("minItems") if node.get("minItems") == node.get("maxItems") else None
            return [decode(items, f"{path}[{i}]") for i in range(self.count(fixed))]
        if kind != "object":
            raise SourceError(f"unsupported MCB type {kind!r} at {path} ({key})")
        properties = node.get("properties", {})
        result = {}
        if properties and all(":" in name and "x-ordinal-index" not in prop for name, prop in properties.items()):
            by_id = {fnv1a(name): (name, prop) for name, prop in properties.items()}
            if len(by_id) != len(properties):
                raise SourceError(f"MCB component hash collision at {path}")
            for _ in range(self.count(code="I")):
                component = self.reader.number("I")
                if component not in by_id:
                    raise SourceError(f"unknown MCB component 0x{component:08x} at {path} ({key})")
                name, prop = by_id[component]
                if name in result:
                    raise SourceError(f"duplicate MCB component {name!r} at {path}")
                result[name] = decode(prop, f"{path}.{name}")
            return result
        if not properties and "additionalProperties" in node:
            value_schema = node["additionalProperties"]
            if not isinstance(value_schema, dict):
                raise SourceError(f"unsupported MCB map at {path}")
            for _ in range(self.count()):
                key_type = node.get("x-key-underlying-type", "string")
                if key_type == "string":
                    name = self.reader.string()
                elif key_type in ("float", "int32"):
                    value = self.reader.number("f" if key_type == "float" else "i")
                    if not math.isfinite(value):
                        raise SourceError(f"non-finite MCB map key at {path}")
                    name = format(value, ".15g") if key_type == "float" else str(value)
                else:
                    raise SourceError(f"unsupported MCB map key {key_type} at {path}")
                if name in result:
                    raise SourceError(f"duplicate MCB map key {name!r} at {path}")
                result[name] = decode(value_schema, f"{path}.{name}")
            return result
        ordinals = [prop.get("x-ordinal-index") for prop in properties.values()]
        if any(not isinstance(v, int) for v in ordinals) or len(set(ordinals)) != len(ordinals):
            raise SourceError(f"missing or duplicate MCB field ordinals at {path} ({key})")
        required = set(node.get("required", []))
        for name, prop in sorted(properties.items(), key=lambda pair: pair[1]["x-ordinal-index"]):
            if name in required or "default" in prop or self.reader.boolean():
                result[name] = decode(prop, f"{path}.{name}")
        return result


def bundle_identity(path: Path = DEFAULT_SCHEMAS) -> dict[str, Any]:
    raw = path.read_bytes()
    bundle = json.loads(raw)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "source": bundle["source"]}
