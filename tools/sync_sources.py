from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .convert import parse_loon, to_stash

ROOT = Path(__file__).resolve().parents[1]
MAPPING_PATH = ROOT / "sources" / "loon-plugins.json"
KELEE_SOURCE = "https://kelee.one/"
KELEE_MIRROR = "https://git.repcz.link/kelee.one/"
REQUIRED_METADATA = ("name", "desc", "author", "homepage", "icon")


def source_fetch_url(source_url: str) -> str:
    if source_url.startswith(KELEE_SOURCE):
        return KELEE_MIRROR + source_url[len(KELEE_SOURCE) :]
    return source_url


def fetch_source(source_url: str) -> str:
    fetch_url = source_fetch_url(source_url)
    request = Request(fetch_url, headers={"User-Agent": "loon-stash-toolkit-source-sync/1.0"})
    try:
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"failed to fetch {source_url} via {fetch_url}: {exc}") from exc


def inline_jq_files(source_text: str, source_url: str) -> str:
    source_host = urlsplit(source_url).hostname
    pattern = re.compile(r'response[.]json[.]jq_file[(]"(https://[^"]+)"[)]')

    def replace(match: re.Match[str]) -> str:
        jq_url = match.group(1)
        parsed = urlsplit(jq_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != source_host
            or parsed.netloc.lower() != (parsed.hostname or "")
        ):
            raise ValueError(f"Loon jq_file must use the plugin source same-origin: {jq_url}")
        jq_source = fetch_source(jq_url)
        jq_source = "\n".join(
            line for line in jq_source.splitlines() if not line.lstrip().startswith("#")
        )
        return f"response.json.jq({json.dumps(jq_source, ensure_ascii=False)})"

    return pattern.sub(replace, source_text)


def convert_source(item: dict[str, Any], source_text: str) -> str:
    document = parse_loon(
        inline_jq_files(source_text, item["source"]),
        argument_values=item.get("arguments"),
    )
    if document.warnings:
        raise ValueError(f"{item['source']} conversion warnings: {document.warnings}")
    if document.metadata.get("name") != item["name"]:
        raise ValueError(
            f"source name changed for {item['source']}: {document.metadata.get('name')!r}"
        )

    if item.get("category"):
        document.metadata["category"] = item["category"]

    required: list[str] = list(REQUIRED_METADATA)
    if not (document.metadata.get("category") or document.metadata.get("tag")):
        required.append("category")
    if "kelee.one" in item["source"]:
        required.append("date")
    missing = [key for key in required if not document.metadata.get(key, "").strip()]
    if missing:
        raise ValueError(f"{item['source']} is missing metadata: {', '.join(missing)}")

    output = to_stash(document, Path(item["output"]).stem)
    if document.warnings:
        raise ValueError(f"{item['source']} conversion warnings: {document.warnings}")
    return output


def sync_sources(root: Path = ROOT, dry_run: bool = False) -> list[str]:
    mappings = json.loads((root / "sources" / "loon-plugins.json").read_text(encoding="utf-8"))
    generated: list[tuple[Path, str]] = []
    for item in mappings:
        output_name = item["output"]
        if Path(output_name).name != output_name or not output_name.endswith(".stoverride"):
            raise ValueError(f"invalid output filename in source mapping: {output_name!r}")
        source_text = fetch_source(item["source"])
        generated.append(
            (root / "stash" / output_name, convert_source(item, source_text))
        )

    changed = [
        (path, output)
        for path, output in generated
        if not path.is_file() or path.read_text(encoding="utf-8") != output
    ]
    for path, output in changed:
        print(f"{'would update' if dry_run else 'updated'} {path.relative_to(root)}")
        if not dry_run:
            path.write_text(output, encoding="utf-8", newline="\n")
    if not changed:
        print("No generated overrides changed.")
    return [str(path.relative_to(root)) for path, _ in changed]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync mapped Loon plugins to Stash overrides.")
    parser.add_argument("--dry-run", action="store_true", help="fetch and convert without writing files")
    args = parser.parse_args()
    sync_sources(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
