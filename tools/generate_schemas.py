"""Catalog-driven Minecraft Bedrock JSON schema collector and CUE emitter.

Only the Python standard library is used.  Collection is deliberately separate
from generation: checked-in schema_ir.json is the reproducible generator input.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from bedrock_source import DEFAULT_SCHEMAS, MCB_MAGIC, McbDecoder, PackFile, SourceError, bundle_identity, iter_pack_files

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODULE_PATH = "github.com/inku25253/mcue"
DEFAULT_DATA = Path(r"C:\XboxGames\Minecraft for Windows\Content\data")


class SchemaError(RuntimeError):
    pass


def strip_json_comments(source: str) -> str:
    result: list[str] = []
    i = 0
    quoted = escaped = False
    while i < len(source):
        char = source[i]
        following = source[i + 1] if i + 1 < len(source) else ""
        if quoted:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            i += 1
        elif char == '"':
            quoted = True
            result.append(char)
            i += 1
        elif char == "/" and following == "/":
            i = source.find("\n", i)
            if i < 0:
                break
        elif char == "/" and following == "*":
            end = source.find("*/", i + 2)
            if end < 0:
                raise SchemaError("unterminated JSON block comment")
            i = end + 2
        else:
            result.append(char)
            i += 1
    return "".join(result)


@dataclass
class Shape:
    kinds: set[str] = field(default_factory=set)
    fields: dict[str, "Shape"] = field(default_factory=dict)
    element: "Shape | None" = None
    required: bool = False
    min_items: int | None = None
    exact_items: int | None = None
    dynamic_map: bool = False
    contract: str | None = None
    literals: list[Any] | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    pattern_fields: list[dict[str, Any]] = field(default_factory=list)
    string_pattern: str | None = None
    alternatives: list["Shape"] = field(default_factory=list)
    conditional_fields: list[dict[str, Any]] = field(default_factory=list)
    all_of: list["Shape"] = field(default_factory=list)

    def add(self, value: Any) -> None:
        if value is None:
            self.kinds.add("null")
        elif isinstance(value, bool):
            self.kinds.add("bool")
        elif isinstance(value, int):
            self.kinds.add("int")
        elif isinstance(value, float):
            self.kinds.add("number")
        elif isinstance(value, str):
            self.kinds.add("string")
        elif isinstance(value, list):
            self.kinds.add("list")
            self.element = self.element or Shape()
            for item in value:
                self.element.add(item)
        elif isinstance(value, dict):
            self.kinds.add("object")
            for name, item in value.items():
                self.fields.setdefault(name, Shape()).add(item)
        else:
            raise SchemaError(f"unsupported JSON value {type(value).__name__}")

    def merge(self, other: "Shape") -> None:
        self.kinds.update(other.kinds)
        self.required = self.required or other.required
        self.dynamic_map = self.dynamic_map or other.dynamic_map
        for pattern in other.pattern_fields:
            if pattern not in self.pattern_fields:
                self.pattern_fields.append(copy.deepcopy(pattern))
        if self.string_pattern and other.string_pattern and self.string_pattern != other.string_pattern:
            raise SchemaError("cannot merge incompatible string patterns")
        self.string_pattern = self.string_pattern or other.string_pattern
        for alternative in other.alternatives:
            if alternative.as_dict() not in [item.as_dict() for item in self.alternatives]:
                self.alternatives.append(copy.deepcopy(alternative))
        for condition in other.conditional_fields:
            if condition not in self.conditional_fields:
                self.conditional_fields.append(copy.deepcopy(condition))
        for constraint in other.all_of:
            if constraint.as_dict() not in [item.as_dict() for item in self.all_of]:
                self.all_of.append(copy.deepcopy(constraint))
        if self.contract and other.contract and self.contract != other.contract:
            raise SchemaError(f"cannot merge incompatible contracts {self.contract!r} and {other.contract!r}")
        self.contract = self.contract or other.contract
        if other.literals is not None:
            self.literals = list(dict.fromkeys((self.literals or []) + other.literals))
        for name in ("minimum", "maximum"):
            incoming = getattr(other, name)
            if incoming is not None:
                existing = getattr(self, name)
                setattr(self, name, incoming if existing is None else min(existing, incoming) if name == "minimum" else max(existing, incoming))
        self.exact_items = self.exact_items if self.exact_items is not None else other.exact_items
        self.min_items = self.min_items if self.min_items is not None else other.min_items
        for name, nested in other.fields.items():
            self.fields.setdefault(name, Shape()).merge(nested)
        if other.element:
            self.element = self.element or Shape()
            self.element.merge(other.element)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kinds": sorted(self.kinds)}
        if self.fields:
            result["fields"] = {k: self.fields[k].as_dict() for k in sorted(self.fields)}
        if self.element:
            result["element"] = self.element.as_dict()
        if self.required:
            result["required"] = True
        if self.min_items is not None:
            result["min_items"] = self.min_items
        if self.exact_items is not None:
            result["exact_items"] = self.exact_items
        if self.dynamic_map:
            result["dynamic_map"] = True
        if self.contract:
            result["contract"] = self.contract
        if self.literals is not None:
            result["literals"] = self.literals
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.pattern_fields:
            result["pattern_fields"] = copy.deepcopy(self.pattern_fields)
        if self.string_pattern is not None:
            result["string_pattern"] = self.string_pattern
        if self.alternatives:
            result["alternatives"] = [item.as_dict() for item in self.alternatives]
        if self.conditional_fields:
            result["conditional_fields"] = copy.deepcopy(self.conditional_fields)
        if self.all_of:
            result["all_of"] = [item.as_dict() for item in self.all_of]
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Shape":
        shape = cls(
            set(value.get("kinds", [])),
            required=value.get("required", False),
            min_items=value.get("min_items"),
            exact_items=value.get("exact_items"),
            dynamic_map=value.get("dynamic_map", False),
            contract=value.get("contract"),
            literals=value.get("literals"),
            minimum=value.get("minimum"),
            maximum=value.get("maximum"),
            pattern_fields=copy.deepcopy(value.get("pattern_fields", [])),
            string_pattern=value.get("string_pattern"),
            alternatives=[cls.from_dict(item) for item in value.get("alternatives", [])],
            conditional_fields=copy.deepcopy(value.get("conditional_fields", [])),
            all_of=[cls.from_dict(item) for item in value.get("all_of", [])],
        )
        shape.fields = {k: cls.from_dict(v) for k, v in value.get("fields", {}).items()}
        if "element" in value:
            shape.element = cls.from_dict(value["element"])
        return shape


def load_json(path: Path) -> Any:
    try:
        return json.loads(strip_json_comments(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError, json.JSONDecodeError, SchemaError) as exc:
        raise SchemaError(f"cannot parse {path}: {exc}") from exc


def matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path.lower(), pattern.lower()) for pattern in patterns)


def classify(catalog: dict[str, Any], scope: str, pack: str, logical: str) -> tuple[str, Any]:
    reason = catalog["excluded_packs"].get(scope, {}).get(pack)
    if reason:
        return "excluded", reason
    found = [r for r in catalog["families"] if r["scope"] == scope and matches(logical, r["paths"])]
    if len(found) > 1:
        raise SchemaError(f"duplicate classification for {scope}/{pack}/{logical}: " + ", ".join(r["family"] for r in found))
    if found:
        return "target", found[0]
    return "excluded", catalog["fallback_exclusion"]


def version_key(value: Any) -> str:
    if isinstance(value, list) and value and all(isinstance(part, int) and not isinstance(part, bool) for part in value):
        return ".".join(str(part) for part in value)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise SchemaError(f"invalid format_version {value!r}")
    return str(value)


def package_version(version: str | None) -> str | None:
    if version is None:
        return None
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", version).strip("_").lower()
    return "v" + clean


def roots_for(rule: dict[str, Any], document: Any) -> list[str]:
    if rule.get("root") == "document":
        return []
    if not isinstance(document, dict):
        raise SchemaError(f"{rule['family']} document must be an object")
    if "root" in rule:
        root = rule["root"]
        if root not in document:
            raise SchemaError(f"{rule['family']} is missing required wrapper {root!r}")
        return [root]
    prefix = rule["root_prefix"]
    roots = [key for key in document if key.startswith(prefix)]
    if len(roots) != 1:
        raise SchemaError(f"{rule['family']} requires exactly one {prefix!r} root; found {roots!r}")
    return roots


def excluded_root_reason(rule: dict[str, Any], roots: Iterable[str]) -> str | None:
    exclusions = rule.get("excluded_roots", {})
    reasons = {exclusions[root] for root in roots if root in exclusions}
    if len(reasons) > 1:
        raise SchemaError(f"roots have conflicting exclusion reasons: {sorted(reasons)!r}")
    return next(iter(reasons), None)


def shape_at(shape: Shape, path: list[str], create: bool = True) -> list[Shape]:
    current = [shape]
    wildcard_seen = False
    for part in path:
        following: list[Shape] = []
        for node in current:
            if part == "*":
                wildcard_seen = True
                if node.element:
                    following.append(node.element)
                following.extend(node.fields.values())
            elif part == "**":
                wildcard_seen = True
                descendants = [node]
                for descendant in descendants:
                    if descendant.element:
                        descendants.append(descendant.element)
                    descendants.extend(descendant.fields.values())
                following.extend(descendants)
            elif part in node.fields:
                following.append(node.fields[part])
            elif create and not wildcard_seen:
                node.kinds.add("object")
                following.append(node.fields.setdefault(part, Shape()))
        current = following
    return current


def apply_corrections(family: str, shape: Shape, corrections: dict[str, Any], version: str | None = None) -> None:
    for correction in corrections["corrections"]:
        if correction["family"] != family:
            continue
        if version in correction.get("exclude_versions", []):
            continue
        if correction.get("versions") and version not in correction["versions"]:
            continue
        nodes = shape_at(shape, correction["path"], create=correction.get("create", True))
        if not nodes:
            continue
        for node in nodes:
            if "replace_shape" in correction:
                replacement = Shape.from_dict(correction["replace_shape"])
                replacement.required = replacement.required or node.required
                node.__dict__.update(replacement.__dict__)
            if "only_kinds" in correction:
                node.kinds = set(correction["only_kinds"])
            if "element_only_kinds" in correction:
                node.element = node.element or Shape()
                node.element.kinds = set(correction["element_only_kinds"])
            for name, value in correction.get("element_fields", {}).items():
                node.element = node.element or Shape({"object"})
                node.element.fields[name] = Shape.from_dict(value)
            node.required = node.required or correction.get("required", False)
            node.kinds.update(correction.get("allow_kinds", []))
            node.min_items = correction.get("min_items", node.min_items)
            node.exact_items = correction.get("exact_items", node.exact_items)
            node.dynamic_map = node.dynamic_map or correction.get("dynamic_map", False)
            contract = correction.get("contract")
            if node.contract and contract and node.contract != contract:
                raise SchemaError(f"cannot replace contract {node.contract!r} with {contract!r}")
            node.contract = node.contract or contract
            if "list" in node.kinds and node.element is None:
                node.element = Shape({"object"})


def unify_entity_components(shape: Shape) -> dict[str, Shape]:
    """Make base and grouped entity components share one versioned definition."""
    entity = shape.fields.get("minecraft:entity")
    if not entity or "object" not in entity.kinds:
        raise SchemaError("entity schema is missing minecraft:entity object shape")

    base = entity.fields.get("components", Shape({"object"}))
    groups = entity.fields.get("component_groups", Shape({"object"}))
    shared = Shape({"object"})
    shared.merge(base)
    for group in groups.fields.values():
        shared.merge(group)
    # Requiredness belongs to the containing `components` field, not its value.
    shared.required = False

    entity.fields["components"] = Shape({"ref:#EntityComponents"}, required=base.required)
    entity.fields["component_groups"] = Shape(
        {"object"},
        fields={"value": Shape({"ref:#EntityComponents"})},
        required=groups.required,
        dynamic_map=True,
    )
    return {"EntityComponents": shared}


def load_pack_document(entry: PackFile, decoder: McbDecoder, audit: dict[str, Any]) -> Any:
    try:
        if entry.payload.startswith(MCB_MAGIC):
            document = decoder.decode(entry.payload)
            audit["mcb_decoded"] = audit.get("mcb_decoded", 0) + 1
            payload = next(key for key in document if key != "format_version")
            counts = audit.setdefault("mcb_payloads", {})
            counts[payload] = counts.get(payload, 0) + 1
            return document
        kind = "archived_json" if entry.archived else "loose_json"
        document = json.loads(strip_json_comments(entry.payload.decode("utf-8-sig")))
        audit[kind] = audit.get(kind, 0) + 1
        return document
    except (UnicodeError, ValueError, SourceError) as exc:
        raise SchemaError(f"cannot parse {entry.source}: {exc}") from exc


def collect(data: Path, catalog: dict[str, Any], corrections: dict[str, Any], seeds: dict[str, Any] | None = None, mcb_schemas: Path = DEFAULT_SCHEMAS) -> dict[str, Any]:
    schemas: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    audit = {"target": 0, "excluded": 0, "families": {}, "exclusion_reasons": {}}
    errors: list[str] = []
    decoder = McbDecoder.load(mcb_schemas)
    fingerprint = hashlib.sha256()
    for entry in iter_pack_files(data, audit):
        scope, pack, logical = entry.scope, entry.pack, entry.logical
        fingerprint.update(f"{scope}/{pack}/{logical}\0".encode("utf-8"))
        fingerprint.update(hashlib.sha256(entry.payload).digest())
        try:
            disposition, detail = classify(catalog, scope, pack, logical)
            audit[disposition] += 1
            if disposition == "excluded":
                reasons = audit["exclusion_reasons"]
                reasons[detail] = reasons.get(detail, 0) + 1
                continue
            rule = detail
            document = load_pack_document(entry, decoder, audit)
            roots = roots_for(rule, document)
            root_exclusion = excluded_root_reason(rule, roots)
            if root_exclusion is not None:
                audit["target"] -= 1
                audit["excluded"] += 1
                reasons = audit["exclusion_reasons"]
                reasons[root_exclusion] = reasons.get(root_exclusion, 0) + 1
                continue
            format_mode = rule["format"]
            raw_version = document.get("format_version") if isinstance(document, dict) else None
            if format_mode == "required" and raw_version is None:
                raise SchemaError(f"missing format_version in {entry.source}")
            version = version_key(raw_version) if raw_version is not None else None
            if format_mode == "none":
                version = None
                raw_version = None
            key = (scope, rule["family"], version)
            docs = catalog.get("sources", {}).get(rule["docs"], rule["docs"])
            record = schemas.setdefault(key, {"scope": scope, "family": rule["family"], "version": version, "format_values": [], "shape": Shape(), "roots": set(), "files": 0, "docs": docs, "stable": rule["stable"], "exclusive_root": rule.get("exclusive_root", False)})
            if raw_version is not None and raw_version not in record["format_values"]:
                record["format_values"].append(raw_version)
            # UI names are author-defined. Keep the source audit, but never infer
            # a contract from the particular names shipped in the installed pack.
            if rule["family"] != "ui":
                record["shape"].add(document)
            record["roots"].update(roots)
            record["files"] += 1
            family_counts = audit["families"]
            family_counts[rule["family"]] = family_counts.get(rule["family"], 0) + 1
        except SchemaError as exc:
            errors.append(f"{scope}/{pack}/{logical}: {exc}")
    if errors:
        preview = "\n".join(errors[:30])
        raise SchemaError(f"collection failed with {len(errors)} error(s):\n{preview}")
    audit["input_sha256"] = fingerprint.hexdigest()
    for seed in (seeds or {}).get("schemas", []):
        version = seed["version"]
        scope = seed["scope"]
        key = (scope, seed["family"], version)
        docs = (seeds or {}).get("sources", {}).get(seed["docs"], seed["docs"])
        record = schemas.setdefault(key, {"scope": scope, "family": seed["family"], "version": version, "format_values": [], "shape": Shape(), "roots": set(), "files": 0, "docs": docs, "stable": seed["stable"], "exclusive_root": seed.get("exclusive_root", False)})
        if seed["format_value"] is not None and seed["format_value"] not in record["format_values"]:
            record["format_values"].append(seed["format_value"])
        record["shape"].add(seed["document"])
        if seed["root"] != "document":
            record["roots"].add(seed["root"])
        record["seeded"] = True
    audit["seeds"] = len((seeds or {}).get("schemas", []))
    output_schemas = []
    for key in sorted(schemas, key=lambda item: (item[0], item[1], item[2] or "")):
        record = schemas[key]
        shape: Shape = record["shape"]
        if record["family"] == "loot_table" and record["version"] is not None:
            legacy = schemas.get((record["scope"], "loot_table", None))
            if legacy:
                shape.merge(legacy["shape"])
        if record["version"] is not None:
            version_shape = shape.fields.setdefault("format_version", Shape())
            version_shape.required = True
        for root in record["roots"]:
            shape.fields[root].required = True
        apply_corrections(record["family"], shape, corrections, record["version"])
        if record["family"] == "ui":
            common = load_json(HERE / "ui_schema.json")
            shape = Shape.from_dict(common["shape"])
            record["definitions"] = copy.deepcopy(common["definitions"])
            record["docs"] = common["docs"]
            record["provenance"] = copy.deepcopy(common["provenance"])
        if record["family"] == "entity":
            record["definitions"] = {
                name: definition.as_dict()
                for name, definition in unify_entity_components(shape).items()
            }
        output_schemas.append({**{k: v for k, v in record.items() if k not in ("shape", "roots")}, "roots": sorted(record["roots"]), "shape": shape.as_dict()})
    compatibility = load_json(HERE / "schema_compatibility.json")["schemas"]
    present = {(r["scope"], r["family"], r["version"]) for r in output_schemas}
    retained = [copy.deepcopy(r) for r in compatibility if (r["scope"], r["family"], r["version"]) not in present]
    output_schemas.extend(retained)
    output_schemas.sort(key=lambda r: (r["scope"], r["family"], r["version"] or ""))
    audit["compatibility_records"] = len(retained)
    identity = bundle_identity(mcb_schemas)
    return {"schema_ir_version": 1, "catalog": "tools/schema_catalog.json", "corrections": "tools/schema_corrections.json", "audit": audit, "mcb_schemas": {"sha256": identity["sha256"], "commit": identity["source"]["commit"], "build_version": identity["source"]["build_version"]}, "schemas": output_schemas}


def cue_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def emit_contract(name: str, indent: str) -> str:
    if name == "entity_set_property":
        return "(bool | number | string)"
    if name == "block_collision_12660":
        vector = "([number, number, number] | {x?: number, y?: number, z?: number, ...})"
        return "(bool | [...{origin?: " + vector + ", size?: " + vector + ", min?: " + vector + ", max?: " + vector + ", ...}])"
    if name != "entity_property":
        raise SchemaError(f"unknown CUE contract {name!r}")
    variants = (
        (
            '"client_sync"?: bool',
            '"default": (bool | string)',
            '"range"?: _|_',
            '"type": "bool"',
            '"values"?: _|_',
        ),
        (
            '"client_sync"?: bool',
            '"default": (int | string)',
            '"range": [int, int]',
            '"type": "int"',
            '"values"?: _|_',
        ),
        (
            '"client_sync"?: bool',
            '"default": (number | string)',
            '"range": [number, number]',
            '"type": "float"',
            '"values"?: _|_',
        ),
        (
            '"client_sync"?: bool',
            '"default": string',
            '"range"?: _|_',
            '"type": "enum"',
            '"values": [string, ...(string)]',
        ),
    )
    rendered = []
    for fields in variants:
        lines = ["{"]
        lines.extend(f"{indent}\t{field}" for field in fields)
        lines.extend((f"{indent}\t...", f"{indent}}}"))
        rendered.append("\n".join(lines))
    return "(" + " | ".join(rendered) + ")"


def emit_shape(shape: Shape, indent: str = "", path: tuple[str, ...] = ()) -> str:
    if shape.all_of:
        base = copy.copy(shape)
        base.all_of = []
        constraints = ([base] if base.kinds or base.literals is not None or base.alternatives else []) + shape.all_of
        return "(" + " & ".join(emit_shape(item, indent, path) for item in constraints) + ")"
    if shape.alternatives:
        base = copy.copy(shape)
        base.alternatives = []
        choices = ([base] if base.kinds or base.literals is not None else []) + shape.alternatives
        return "(" + " | ".join(emit_shape(item, indent, path) for item in choices) + ")"
    if shape.contract and not shape.dynamic_map:
        return emit_contract(shape.contract, indent)
    if shape.literals is not None:
        return "(" + " | ".join(json.dumps(value) for value in shape.literals) + ")"
    options: list[str] = []
    for kind in sorted(shape.kinds):
        if kind == "object":
            lines = ["{"]
            selectors = {name: f"Selector{i}" for i, name in enumerate(sorted({condition["field"] for condition in shape.conditional_fields}))}
            if any(name not in shape.fields for name in selectors):
                raise SchemaError("conditional selectors must be declared as fixed fields")
            if shape.dynamic_map:
                if shape.contract:
                    rendered = emit_contract(shape.contract, indent + "\t")
                else:
                    values = Shape()
                    for nested in shape.fields.values():
                        values.merge(nested)
                    rendered = emit_shape(values, indent + "\t", path + ("*",))
                lines.append(f"{indent}\t[string]: {rendered}")
            else:
                for name in sorted(shape.fields):
                    nested = shape.fields[name]
                    marker = "" if nested.required else "?"
                    rendered = emit_shape(nested, indent + "\t", path + (name,))
                    label = f"{selectors[name]}={cue_string(name)}" if name in selectors else cue_string(name)
                    lines.append(f"{indent}\t{label}{marker}: {rendered}")
            for pattern in shape.pattern_fields:
                key = "string"
                if "regex" in pattern:
                    key += " & =~" + cue_string(pattern["regex"])
                if "exclude_regex" in pattern:
                    key += " & !~" + cue_string(pattern["exclude_regex"])
                for excluded in pattern.get("exclude", []):
                    key += " & !=" + cue_string(excluded)
                rendered = emit_shape(Shape.from_dict(pattern["shape"]), indent + "\t", path + ("*",))
                lines.append(f"{indent}\t[key={key}]: {rendered}")
            for field_name, selector in selectors.items():
                lines.append(f"{indent}\tif {selector} != _|_ {{")
                for condition in shape.conditional_fields:
                    if condition["field"] != field_name:
                        continue
                    body_indent = indent + "\t\t"
                    if "equals" in condition:
                        lines.append(f"{indent}\t\tif {selector} == {json.dumps(condition['equals'])} {{")
                        body_indent += "\t"
                    if "shape" in condition:
                        rendered = emit_shape(Shape.from_dict(condition["shape"]), body_indent, path)
                        lines.append(f"{body_indent}{rendered}")
                    for name, value in sorted(condition.get("fields", {}).items()):
                        nested = Shape.from_dict(value)
                        marker = "" if nested.required else "?"
                        rendered = emit_shape(nested, body_indent, path + (name,))
                        lines.append(f"{body_indent}{cue_string(name)}{marker}: {rendered}")
                    if "equals" in condition:
                        lines.append(f"{indent}\t\t}}")
                lines.append(f"{indent}\t}}")
            lines.extend((f"{indent}\t...", f"{indent}}}"))
            rendered = "\n".join(lines)
            # An existence guard can otherwise mask a scalar/struct conflict
            # when its selector becomes bottom. Preserve the object constraint.
            options.append("({...} & " + rendered + ")" if shape.conditional_fields else rendered)
        elif kind == "list":
            element = emit_shape(shape.element or Shape(), indent + "\t", path + ("*",))
            if shape.exact_items is not None:
                options.append("[" + ", ".join(f"({element})" for _ in range(shape.exact_items)) + "]")
            elif shape.min_items:
                required = ", ".join(f"({element})" for _ in range(shape.min_items))
                options.append(f"[{required}, ...({element})]")
            else:
                options.append(f"[...({element})]")
        else:
            value = kind.removeprefix("ref:") if kind.startswith("ref:") else "_|_" if kind == "bottom" else kind
            if kind == "string" and shape.string_pattern is not None:
                value = "(" + value + " & =~" + cue_string(shape.string_pattern) + ")"
            if kind in ("int", "number"):
                if shape.minimum is not None:
                    value += f" & >={shape.minimum}"
                if shape.maximum is not None:
                    value += f" & <={shape.maximum}"
                if " & " in value:
                    value = "(" + value + ")"
            options.append(value)
    return "_" if not options else options[0] if len(options) == 1 else "(" + " | ".join(options) + ")"


def with_version_literal(shape: Shape, version: str | None) -> Shape:
    result = copy.deepcopy(shape)
    if version is not None:
        field = result.fields["format_version"]
        # Literal is emitted separately; retain the required marker.
        field.kinds = {"__VERSION_LITERAL__"}
    return result


def format_literals(record: dict[str, Any]) -> list[str]:
    return [json.dumps(value, ensure_ascii=False, separators=(", ", ": ")) for value in record["format_values"]]


def emit_record_body(record: dict[str, Any], prohibit_format_version: bool = False) -> tuple[list[str], str]:
    version = record["version"]
    shape = with_version_literal(Shape.from_dict(record["shape"]), version)
    if prohibit_format_version and "object" in shape.kinds:
        shape.fields["format_version"] = Shape({"bottom"})

    def render(value: Shape) -> str:
        text = emit_shape(value)
        if version is not None:
            literals = format_literals(record)
            literal = literals[0] if len(literals) == 1 else "(" + " | ".join(literals) + ")"
            text = text.replace("__VERSION_LITERAL__", literal)
        return text

    variants: list[str] = []
    roots = record.get("roots", [])
    if record.get("exclusive_root") and len(roots) > 1:
        for chosen in roots:
            variant = copy.deepcopy(shape)
            for root in roots:
                variant.fields[root].required = root == chosen
                if root != chosen:
                    variant.fields[root].kinds = {"bottom"}
            variants.append(render(variant))
    else:
        variants.append(render(shape))
    definitions = [
        f"#{name}: {emit_shape(Shape.from_dict(definition))}"
        for name, definition in sorted(record.get("definitions", {}).items())
    ]
    return definitions, " |\n".join(variants)


def emit_schema(record: dict[str, Any]) -> str:
    family, version = record["family"], record["version"]
    docs = record["docs"]
    package = package_version(version) or family.replace("-", "_")
    sources = record.get("provenance", {}).get("sources", [docs])
    header = ["// Code generated by tools/generate_schemas.py; DO NOT EDIT."]
    header.extend(f"// Source: {source}" for source in sources)
    header.extend((f"package {package}", ""))
    definitions, body = emit_record_body(record)
    for definition in definitions:
        header.extend((definition, ""))
    return "\n".join(header) + "#Schema: " + body + "\n"


def emit_dispatch_schema(records: list[dict[str, Any]]) -> str:
    versioned = sorted(
        (record for record in records if record["version"] is not None),
        key=lambda record: record["version"],
    )
    unversioned = [record for record in records if record["version"] is None]
    if not versioned:
        if len(unversioned) != 1:
            raise SchemaError("schema family must have exactly one unversioned record")
        return emit_schema(unversioned[0])
    if len(unversioned) > 1:
        raise SchemaError("schema family cannot have multiple unversioned records")

    first = records[0]
    package = first["family"].replace("-", "_")
    docs = sorted({record["docs"] for record in records})
    header = ["// Code generated by tools/generate_schemas.py; DO NOT EDIT."]
    header.extend(f"// Source: {source}" for source in docs)
    header.extend((f"package {package}", "", "import ("))
    for record in versioned:
        alias = package_version(record["version"])
        path = f"{MODULE_PATH}/bedrock/{record['scope']}/{record['family']}/{alias}"
        header.append(f'\t{alias} "{path}"')
    header.extend((")", ""))

    format_values = [literal for record in versioned for literal in format_literals(record)]
    dispatch = [
        "{",
        f'\tFormatVersion="format_version": ({" | ".join(format_values)})',
    ]
    for record in versioned:
        alias = package_version(record["version"])
        conditions = [f"FormatVersion == {literal}" for literal in format_literals(record)]
        condition = conditions[0] if len(conditions) == 1 else "(" + " || ".join(conditions) + ")"
        dispatch.extend((f"\tif {condition} {{", f"\t\t{alias}.#Schema", "\t}"))
    dispatch.append("}")

    definitions: list[str] = []
    variants: list[str] = []
    if unversioned:
        local_definitions, local_body = emit_record_body(unversioned[0], prohibit_format_version=True)
        definitions.extend(local_definitions)
        variants.append("*" + local_body)
    variants.append("\n".join(dispatch))
    for definition in definitions:
        header.extend((definition, ""))
    return "\n".join(header) + "#Schema: " + " |\n".join(variants) + "\n"


def generated_files(ir: dict[str, Any]) -> dict[Path, str]:
    files: dict[Path, str] = {}
    families: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in ir["schemas"]:
        families.setdefault((record["scope"], record["family"]), []).append(record)
        base = Path(record["scope"]) / record["family"]
        version_dir = package_version(record["version"])
        if version_dir is not None:
            destination = base / version_dir / "schema.cue"
            if destination in files:
                raise SchemaError(f"generated path collision: {destination}")
            files[destination] = emit_schema(record)
    for (scope, family), records in families.items():
        destination = Path(scope) / family / "schema.cue"
        if destination in files:
            raise SchemaError(f"generated path collision: {destination}")
        files[destination] = emit_dispatch_schema(records)
    return files


def write_generated(ir: dict[str, Any], output: Path) -> int:
    files = generated_files(ir)
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / ".generated-files.json"
    if manifest.exists():
        old_files = json.loads(manifest.read_text(encoding="utf-8"))
        for relative in old_files:
            stale = output / relative
            if stale.is_file() and Path(relative) not in files:
                stale.unlink()
    for relative, content in files.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    manifest.write_text(json.dumps([p.as_posix() for p in sorted(files)], indent=2) + "\n", encoding="utf-8")
    # Remove empty directories left behind by package-path migrations.
    for directory in sorted((path for path in output.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return len(files)


def check_generated(ir: dict[str, Any], output: Path) -> int:
    expected = generated_files(ir)
    differences = []
    for relative, content in expected.items():
        actual = output / relative
        if not actual.is_file() or actual.read_text(encoding="utf-8") != content:
            differences.append(relative.as_posix())
    manifest = output / ".generated-files.json"
    listed = set(json.loads(manifest.read_text(encoding="utf-8"))) if manifest.is_file() else set()
    wanted = {p.as_posix() for p in expected}
    differences.extend(sorted(listed ^ wanted))
    if differences:
        raise SchemaError("generated schemas differ: " + ", ".join(sorted(set(differences))))
    return len(expected)


def vet_data(data: Path, catalog: dict[str, Any], ir: dict[str, Any], cue: Path, families: set[str] | None = None, mcb_schemas: Path = DEFAULT_SCHEMAS) -> None:
    """Vet every targeted source document against its generated package."""
    available = {(record["scope"], record["family"], record["version"]) for record in ir["schemas"]}
    groups: dict[tuple[str, str, str | None], list[tuple[str, Any]]] = {}
    audit: dict[str, Any] = {}
    decoder = McbDecoder.load(mcb_schemas)
    for entry in iter_pack_files(data, audit):
        scope, pack, logical = entry.scope, entry.pack, entry.logical
        disposition, detail = classify(catalog, scope, pack, logical)
        if disposition == "excluded":
            continue
        rule = detail
        if families and rule["family"] not in families:
            continue
        document = load_pack_document(entry, decoder, audit)
        roots = roots_for(rule, document)
        if excluded_root_reason(rule, roots) is not None:
            continue
        raw_version = document.get("format_version") if isinstance(document, dict) else None
        if rule["format"] == "required" and raw_version is None:
            raise SchemaError(f"missing format_version in {entry.source}")
        version = None if rule["format"] == "none" or raw_version is None else version_key(raw_version)
        key = (scope, rule["family"], version)
        if key not in available:
            raise SchemaError(f"no generated schema for {key}: {entry.source}")
        groups.setdefault(key, []).append((f"{scope}/{pack}/{logical}", document))

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mcue-vet-", dir=ROOT) as temporary:
        temp = Path(temporary)
        for index, key in enumerate(sorted(groups, key=lambda k: (k[0], k[1], k[2] or ""))):
            scope, family, version = key
            package_path = f"{MODULE_PATH}/bedrock/{scope}/{family}"
            if version is not None:
                package_path += f"/{package_version(version)}"
            entries = groups[key]
            failed = False
            # Recursive UI contracts expand at each nested control. Smaller
            # batches keep memory bounded without reducing document coverage.
            batch_size = 16 if family == "ui" else 128
            for start in range(0, len(entries), batch_size):
                batch = entries[start:start + batch_size]
                fixture = temp / f"group-{index}-{start}.cue"
                documents = json.dumps([document for _, document in batch], ensure_ascii=False, separators=(",", ":"))
                fixture.write_text(f'package validation\nimport schema "{package_path}"\ndocuments: [...schema.#Schema] & {documents}\n', encoding="utf-8")
                result = subprocess.run([str(cue), "vet", "-c=true", str(fixture)], cwd=ROOT, text=True, capture_output=True)
                if result.returncode:
                    detail = (result.stdout + result.stderr)[:12000]
                    sources = "\n".join(f"documents.{i}: {source}" for i, (source, _) in enumerate(batch))
                    failures.append(f"{scope}/{family}/{version or 'unversioned'}:\n{detail}\n{sources}")
                    failed = True
                    break
            if not failed:
                print(f"vetted {len(entries):5d} {scope}/{family}/{version or 'unversioned'}", flush=True)
    if failures:
        raise SchemaError(f"cue vet failed for {len(failures)} schema group(s):\n" + "\n".join(failures[:10]))
    print(f"MCB: restored {audit.get('mcb_decoded', 0)}; payloads: {json.dumps(audit.get('mcb_payloads', {}), sort_keys=True)}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect", help="collect installed pack JSON into schema IR")
    collect_parser.add_argument("--data", type=Path, default=Path(os.environ.get("MC_BEDROCK_DATA", DEFAULT_DATA)))
    collect_parser.add_argument("--ir", type=Path, default=ROOT / "schema_ir.json")
    collect_parser.add_argument("--mcb-schemas", type=Path, default=DEFAULT_SCHEMAS)
    for name in ("generate", "check"):
        command = sub.add_parser(name)
        command.add_argument("--ir", type=Path, default=ROOT / "schema_ir.json")
        command.add_argument("--output", type=Path, default=ROOT / "bedrock")
    vet_parser = sub.add_parser("vet-data", help="vet every targeted installed JSON file")
    vet_parser.add_argument("--data", type=Path, default=Path(os.environ.get("MC_BEDROCK_DATA", DEFAULT_DATA)))
    vet_parser.add_argument("--ir", type=Path, default=ROOT / "schema_ir.json")
    vet_parser.add_argument("--cue", type=Path, default=ROOT / "cue" / "cue.exe")
    vet_parser.add_argument("--family", action="append", help="limit validation to one or more families")
    vet_parser.add_argument("--mcb-schemas", type=Path, default=DEFAULT_SCHEMAS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "collect":
            catalog = load_json(HERE / "schema_catalog.json")
            corrections = load_json(HERE / "schema_corrections.json")
            seeds = load_json(HERE / "schema_seeds.json")
            ir = collect(args.data, catalog, corrections, seeds, args.mcb_schemas)
            # Compact separators keep the checked-in IR substantially smaller;
            # sorted keys retain byte-for-byte deterministic output.
            args.ir.write_text(json.dumps(ir, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            print(f"collected {ir['audit']['target']} target and {ir['audit']['excluded']} explicitly excluded JSON files into {args.ir}")
        elif args.command in ("generate", "check"):
            ir = load_json(args.ir)
            if args.command == "generate":
                count = write_generated(ir, args.output)
                print(f"generated {count} schema packages in {args.output}")
            else:
                count = check_generated(ir, args.output)
                print(f"verified {count} generated schema packages")
        else:
            catalog = load_json(HERE / "schema_catalog.json")
            ir = load_json(args.ir)
            vet_data(args.data, catalog, ir, args.cue, set(args.family) if args.family else None, args.mcb_schemas)
    except (SchemaError, SourceError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
