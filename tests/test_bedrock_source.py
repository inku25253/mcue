from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from bedrock_source import ARCHIVE_MAGIC, MCB_MAGIC, McbDecoder, SourceError, fnv1a, iter_pack_files, read_archive


def varuint(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def string(value: str) -> bytes:
    raw = value.encode("utf-8")
    return varuint(len(raw)) + raw


def mcb(payload: bytes, key: str = "test:document", version=(1, 26, 60)) -> bytes:
    return MCB_MAGIC + struct.pack("<HHH", *version) + b"\x00\x00" + string(key) + payload


def archive(entries) -> bytes:
    header = struct.pack("<QII", ARCHIVE_MAGIC, len(entries), 1)
    table, content = bytearray(), bytearray()
    for name, payload in entries:
        name = name.encode()
        table += bytes([len(name)]) + name.ljust(247, b"\x00") + struct.pack("<II", len(content), len(payload))
        content += payload
    return header + table + content


def decoder(node, key="test:document", extra=None):
    return McbDecoder({
        "roots": {key: [node.get("title", "Root")]},
        "schemas": {"/root.json": {"$id": "/root.json", "title": "Root", "x-format-version": "1.26.60", **node}, **(extra or {})},
    })


class ArchiveTests(unittest.TestCase):
    def test_failed_collection_preserves_existing_ir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "resource_packs").mkdir()
            pack = root / "behavior_packs" / "vanilla" / "__brarchive"
            pack.mkdir(parents=True)
            (pack / "entities.brarchive").write_bytes(b"broken")
            ir = root / "ir.json"
            ir.write_text("existing IR", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "tools/generate_schemas.py"), "collect", "--data", str(root), "--ir", str(ir)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("truncated archive", result.stderr)
            self.assertEqual(ir.read_text(encoding="utf-8"), "existing IR")

    def test_nested_logical_paths_and_identical_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "resource_packs").mkdir()
            pack = root / "behavior_packs" / "vanilla"
            (pack / "entities").mkdir(parents=True)
            (pack / "__brarchive" / "entities").mkdir(parents=True)
            (pack / "entities" / "one.json").write_bytes(b"{}")
            (pack / "__brarchive" / "entities.brarchive").write_bytes(archive([("one.json", b"{}")]))
            (pack / "__brarchive" / "entities" / "nested.brarchive").write_bytes(archive([("two.json", b"[]")]))
            audit = {}
            entries = list(iter_pack_files(root, audit))
            self.assertEqual([e.logical for e in entries], ["entities/nested/two.json", "entities/one.json"])
            self.assertEqual(audit["identical_duplicates"], 1)
            (pack / "entities" / "one.json").write_bytes(b"[]")
            with self.assertRaisesRegex(SourceError, "conflicting"):
                list(iter_pack_files(root, {}))

    def test_archive_rejects_corruption_and_unsafe_names(self):
        valid = archive([("a.json", b"{}")])
        for bad in [b"", valid[:15], valid[:200], valid[:-1], b"badmagic" + valid[8:]]:
            with self.subTest(raw=bad[:16]), self.assertRaises(SourceError):
                list(read_archive(bad))
        for name in ["../x.json", "x/y.json", "C:x.json", "x\\y.json", "x\x00.json"]:
            with self.subTest(name=name), self.assertRaises(SourceError):
                list(read_archive(archive([(name, b"{}")])))
        with self.assertRaisesRegex(SourceError, "duplicate"):
            list(read_archive(archive([("a.json", b"{}"), ("A.json", b"[]")])))


class McbTests(unittest.TestCase):
    def test_field_order_defaults_optional_and_reference(self):
        schema = {"type": "object", "required": ["name"], "properties": {
            "optional": {"type": "boolean", "x-ordinal-index": 2},
            "number": {"$ref": "./number.json", "x-ordinal-index": 1, "default": 0},
            "name": {"type": "string", "x-ordinal-index": 0},
        }}
        d = decoder(schema, extra={"/number.json": {"type": "integer", "x-underlying-type": "int32"}})
        raw = mcb(string("名前" * 80) + struct.pack("<i", -7) + b"\x00")
        self.assertEqual(d.decode(raw)["test:document"], {"name": "名前" * 80, "number": -7})
        with self.assertRaisesRegex(SourceError, "trailing"):
            d.decode(raw + b"\x00")
        with self.assertRaisesRegex(SourceError, "boolean"):
            d.decode(raw[:-1] + b"\x02")

    def test_numeric_widths_large_integers_and_nonfinite(self):
        for underlying, code, value in [("uint8", "B", 255), ("int8", "b", -128), ("uint16", "H", 65535), ("int16", "h", -32000), ("uint32", "I", 2**32-1), ("int32", "i", -2**31), ("uint64", "Q", 2**64-1), ("int64", "q", -2**63), ("float", "f", 0.5), ("double", "d", 0.25)]:
            with self.subTest(type=underlying):
                d = decoder({"type": "number", "x-underlying-type": underlying})
                result = d.decode(mcb(struct.pack("<" + code, value)))["test:document"]
                self.assertEqual(result, value)
                self.assertEqual(json.loads(json.dumps(result)), value)
        with self.assertRaisesRegex(SourceError, "non-finite"):
            decoder({"type": "number", "x-underlying-type": "float"}).decode(mcb(struct.pack("<f", math.inf)))

    def test_arrays_maps_variants_and_normalization(self):
        number = {"type": "number", "x-underlying-type": "float"}
        vec = {"title": "vec3", "oneOf": [{"type": "array", "items": number, "minItems": 3, "maxItems": 3}, {"type": "object"}]}
        self.assertEqual(decoder(vec).decode(mcb(struct.pack("<fff", 1, 2, 3)))["test:document"], [1, 2, 3])
        tagged = {"oneOf": [{"type": "string", "x-ordinal-index": 5}, {"type": "boolean", "x-ordinal-index": 8}]}
        self.assertEqual(decoder(tagged).decode(mcb(b"\x08\x01"))["test:document"], True)
        with self.assertRaisesRegex(SourceError, "variant"):
            decoder(tagged).decode(mcb(b"\x07"))
        trade = {"title": "TradeItemList", "oneOf": [{"type": "string"}, {"type": "object"}]}
        self.assertEqual(decoder(trade).decode(mcb(b"\x02" + string("a") + string("b")))["test:document"], {"choice": ["a", "b"]})
        self.assertEqual(decoder(trade).decode(mcb(b"\x01" + string("a")))["test:document"], "a")
        mapping = {"type": "object", "additionalProperties": {"type": "boolean"}}
        self.assertEqual(decoder(mapping).decode(mcb(b"\x01" + string("k") + b"\x01"))["test:document"], {"k": True})
        with self.assertRaisesRegex(SourceError, "duplicate"):
            decoder(mapping).decode(mcb(b"\x02" + string("k") + b"\x01" + string("k") + b"\x00"))
        tuple_schema = {"type": "array", "items": [{"type": "boolean"}, {"type": "string"}]}
        self.assertEqual(decoder(tuple_schema).decode(mcb(b"\x00" + string("tail")))["test:document"], [False, "tail"])

    def test_components_reject_unknown_and_duplicate_ids(self):
        d = decoder({"type": "object", "properties": {"test:component": {"type": "boolean"}}})
        entry = struct.pack("<I", fnv1a("test:component")) + b"\x01"
        self.assertEqual(d.decode(mcb(struct.pack("<I", 1) + entry))["test:document"], {"test:component": True})
        with self.assertRaisesRegex(SourceError, "duplicate"):
            d.decode(mcb(struct.pack("<I", 2) + entry + entry))
        with self.assertRaisesRegex(SourceError, "unknown MCB component"):
            d.decode(mcb(struct.pack("<II", 1, 0)))

    def test_version_selection_beta_and_versionless_trade_wrapper(self):
        d = decoder({"type": "array", "items": {"type": "string"}}, key="tiers")
        self.assertEqual(d.decode(mcb(b"\x00", key="tiers")), {"tiers": []})
        with self.assertRaisesRegex(SourceError, "no compatible"):
            d.decode(mcb(b"\x00", key="tiers", version=(1, 26, 50)))
        beta = decoder({"type": "boolean", "x-format-version": "beta"})
        raw = MCB_MAGIC + struct.pack("<HHH", 9999, 9999, 9999) + string("beta") + string("") + string("test:document") + b"\x01"
        self.assertEqual(beta.decode(raw)["format_version"], "beta")

    def test_invalid_sizes_utf8_ordinals_and_references(self):
        for payload in [b"\x80" * 5, b"\xff\xff\xff\xff\x10", b"\x02x", b"\x01\xff"]:
            with self.subTest(payload=payload), self.assertRaises(SourceError):
                decoder({"type": "string"}).decode(mcb(payload))
        with self.assertRaisesRegex(SourceError, "limit"):
            decoder({"type": "array", "items": {"type": "boolean"}}).decode(mcb(varuint(1_000_001)))
        with self.assertRaisesRegex(SourceError, "ordinals"):
            decoder({"type": "object", "properties": {"a": {"type": "boolean"}}}).decode(mcb(b"\x01"))
        with self.assertRaisesRegex(SourceError, "missing MCB schema"):
            decoder({"$ref": "./missing.json"}).decode(mcb(b""))

    def test_real_payloads_cover_all_six_document_types(self):
        import base64
        fixtures = json.loads((ROOT / "tests/fixtures/mcb.json").read_text(encoding="utf-8"))
        d = McbDecoder.load()
        self.assertEqual(len(fixtures), 6)
        for fixture in fixtures:
            with self.subTest(source=fixture["source"]):
                self.assertEqual(d.decode(base64.b64decode(fixture["mcb_base64"])), fixture["document"])

    def test_sample_comparison_distinguishes_normalization_from_content_changes(self):
        from verify_mcb_samples import difference
        self.assertIsNone(difference({"gradient": {"0.54": [0.5, 1]}}, {"gradient": {"0.540000021457672": [0.5, 1.0]}}))
        self.assertIsNone(difference({"value": True}, True))
        self.assertIsNone(difference("#80ff0000", [1, 0, 0, 128 / 255]))
        self.assertIsNone(difference(3, {"min": 3, "max": 3}))
        self.assertIsNotNone(difference(3, {"min": 3, "max": 4}))
        self.assertIsNotNone(difference({"velocity": True}, {"velocity": False}))


if __name__ == "__main__":
    unittest.main()
