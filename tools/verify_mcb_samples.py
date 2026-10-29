"""Compare restored retail MCB with pinned Mojang JSON samples.

This optional integration check downloads the text-only official release into
memory, verifies its published SHA-256, and compares overlapping vanilla files.
Only fields explicitly written in the original JSON are compared; compiled
defaults may add fields. JSON spelling normalizations are handled below.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import copy
import re
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

from bedrock_source import MCB_MAGIC, McbDecoder, iter_pack_files, numeric_version
from generate_schemas import DEFAULT_DATA, strip_json_comments

SAMPLES_URL = "https://github.com/Mojang/bedrock-samples/releases/download/v1.26.60.5/bedrock-samples-v1.26.60.5-min.zip"
SAMPLES_SHA256 = "bdc08469a284052b513784e49191a0590cc4c1aea019c40ea4159a4f51c021e5"


def difference(source, restored, path: str = "$") -> str | None:
    if isinstance(source, bool):
        return None if type(restored) is bool and source == restored else f"{path}: boolean mismatch"
    if isinstance(source, (int, float)):
        # Molang literals and single-valued ranges have canonical storage forms.
        if isinstance(restored, str):
            try:
                restored = float(restored)
            except ValueError:
                pass
        if isinstance(restored, dict) and "min" in restored and "max" in restored:
            return difference(source, restored["min"], path + ".min") or difference(source, restored["max"], path + ".max")
        if isinstance(restored, (int, float)) and not isinstance(restored, bool) and math.isclose(source, restored, rel_tol=1e-6, abs_tol=1e-7):
            return None
    if isinstance(source, str):
        if source == restored:
            return None
        if isinstance(restored, list) and len(restored) == 1:
            return difference(source, restored[0], path + "[0]")
        if isinstance(restored, dict) and restored.get("name") == source:
            return None
        if isinstance(restored, list) and re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", source):
            color = source[1:]
            alpha = int(color[:2], 16) / 255 if len(color) == 8 else 1
            color = color[-6:]
            return difference([int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [alpha], restored, path)
    if isinstance(source, dict):
        if set(source) == {"value"} and not isinstance(restored, dict):
            return difference(source["value"], restored, path + ".value")
        if set(source) == {"choice"} and len(source["choice"]) == 1 and isinstance(restored, dict) and "choice" not in restored:
            return difference(source["choice"][0], restored, path + ".choice[0]")
        if isinstance(restored, list) and set(source) <= {"x", "y", "z"}:
            return difference([source.get(k, 0) for k in ("x", "y", "z")], restored, path)
        if isinstance(restored, list) and len(restored) == 1:
            return difference(source, restored[0], path + "[0]")
        if isinstance(restored, dict):
            for key, value in source.items():
                restored_key = key
                if key not in restored:
                    try:
                        numeric = float(key)
                        candidates = [candidate for candidate in restored if math.isclose(numeric, float(candidate), rel_tol=1e-6, abs_tol=1e-7)]
                    except (ValueError, TypeError):
                        candidates = []
                    if len(candidates) == 1:
                        restored_key = candidates[0]
                if restored_key not in restored:
                    return f"{path}.{key}: missing explicitly specified field"
                mismatch = difference(value, restored[restored_key], f"{path}.{key}")
                if mismatch:
                    return mismatch
            return None
    if isinstance(source, list) and isinstance(restored, list) and len(source) == len(restored):
        for index, (left, right) in enumerate(zip(source, restored)):
            mismatch = difference(left, right, f"{path}[{index}]")
            if mismatch:
                return mismatch
        return None
    if source is None and restored is None:
        return None
    return f"{path}: {source!r} != {restored!r}"


def verify(data: Path, samples: bytes) -> dict[str, int]:
    if hashlib.sha256(samples).hexdigest() != SAMPLES_SHA256:
        raise ValueError("official sample archive SHA-256 differs from the pinned release")
    decoder = McbDecoder.load()
    matched = Counter()
    failures = []
    known = json.loads(Path(__file__).with_name("sample_discrepancies.json").read_text(encoding="utf-8"))["differences"]
    observed_known = set()
    with zipfile.ZipFile(io.BytesIO(samples)) as archive:
        # Releases may have a single enclosing directory.
        names = {name[name.index(marker):]: name for name in archive.namelist() for marker in ("behavior_pack/", "resource_pack/") if marker in name and name.endswith(".json")}
        effective = {}
        priorities = {}
        for entry in iter_pack_files(data, {}):
            priority = (0,) if entry.pack == "vanilla" else numeric_version(entry.pack.removeprefix("vanilla_")) if entry.pack.startswith("vanilla_") else None
            if priority is None:
                continue
            identity = (entry.scope, entry.logical)
            if identity not in priorities or priority > priorities[identity]:
                priorities[identity] = priority
                effective[identity] = entry
        for entry in effective.values():
            if not entry.payload.startswith(MCB_MAGIC):
                continue
            logical = ("behavior_pack/" if entry.scope == "behavior" else "resource_pack/") + entry.logical
            if logical not in names:
                continue
            source = json.loads(strip_json_comments(archive.read(names[logical]).decode("utf-8-sig")))
            restored = decoder.decode(entry.payload)
            if source.get("format_version") != restored.get("format_version"):
                continue  # A different data-contract version is not a reference pair.
            payload = next(key for key in restored if key != "format_version")
            mismatch = difference(source, restored)
            if mismatch and logical in known:
                expected = known[logical]
                compiled_hash = hashlib.sha256(entry.payload).hexdigest()
                source_value, restored_value = source, restored
                for part in expected["path"]:
                    source_value, restored_value = source_value[part], restored_value[part]
                if compiled_hash != expected["mcb_sha256"] or source_value != expected["sample_value"] or restored_value != expected["compiled_value"]:
                    raise ValueError(f"recorded upstream discrepancy changed: {logical}")
                adjusted = copy.deepcopy(source)
                parent = adjusted
                for part in expected["path"][:-1]:
                    parent = parent[part]
                parent[expected["path"][-1]] = expected["compiled_value"]
                mismatch = difference(adjusted, restored)
                if not mismatch:
                    observed_known.add(logical)
            if mismatch:
                failures.append(f"{logical}: {mismatch}")
            else:
                matched[payload] += 1
    if failures:
        raise ValueError(f"{len(failures)} official sample comparisons failed:\n" + "\n".join(failures[:20]))
    if not matched:
        raise ValueError("no same-version vanilla MCB/sample pairs found")
    if observed_known != set(known):
        raise ValueError(f"recorded upstream discrepancy coverage changed: {sorted(observed_known)}")
    print(f"verified {len(observed_known)} explicitly recorded installed/sample differences")
    return dict(sorted(matched.items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--samples", type=Path, help="pinned official min.zip; downloads into memory when omitted")
    args = parser.parse_args()
    if args.samples:
        raw = args.samples.read_bytes()
    else:
        with urllib.request.urlopen(SAMPLES_URL, timeout=120) as response:
            raw = response.read()
    counts = verify(args.data, raw)
    print(f"compared {sum(counts.values())} same-version official JSON samples: {json.dumps(counts)}")


if __name__ == "__main__":
    main()
