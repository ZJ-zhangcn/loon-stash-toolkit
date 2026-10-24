from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .convert import parse_loon, parse_stash, to_stash

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

    # 某些 Loon 插件用 response.header.add 补 grpc-status，而 Stash 的 mock 已能直接声明
    # 响应头；两者重复时保留 mock、丢掉 header 改写，避免引入未验证的 rewrite 段。
    if item.get("drop_header_rewrite"):
        document.header_rewrite.clear()

    # 视频 CDN 不适合走 MITM：Stash 会接管整条媒体流，播放容易卡死。
    # 只保留真正需要脚本的域名，别为了一个可选优化把播放搞挂。
    if item.get("drop_mitm"):
        banned = set(item["drop_mitm"])
        document.mitm[:] = [host for host in document.mitm if host not in banned]

    for needle in item.get("drop_scripts") or []:
        document.scripts[:] = [rule for rule in document.scripts if needle not in rule.match]

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


def inherit_payloads(path: Path, output: str) -> str:
    """重写时沿用旧文件里的 payload 快照，避免每日同步把离线脚本清掉。"""
    if not path.is_file():
        return output
    try:
        previous = parse_stash(path.read_text(encoding="utf-8"))
    except (ValueError, KeyError):
        return output
    if "script-providers:" not in output:
        return output
    previous_providers = previous.script_providers

    lines = output.split("\n")
    index = lines.index("script-providers:")
    index += 1
    while index < len(lines):
        name_match = re.match(r'^  "?([\w.-]+)"?:$', lines[index])
        if not name_match:
            index += 1
            continue
        name = name_match.group(1)
        end = index + 1
        while end < len(lines) and lines[end].startswith("    "):
            end += 1
        body = (previous_providers.get(name) or {}).get("payload")
        if isinstance(body, str) and body:
            block = ["    payload: |-"]
            block.extend(f"      {line}" if line.strip() else "" for line in body.split("\n"))
            lines[end:end] = block
            end += len(block)
        index = end
    return "\n".join(lines)


def sync_sources(root: Path = ROOT, dry_run: bool = False) -> list[str]:
    mappings = json.loads((root / "sources" / "loon-plugins.json").read_text(encoding="utf-8"))
    generated: list[tuple[Path, str]] = []
    for item in mappings:
        output_name = item["output"]
        if Path(output_name).name != output_name or not output_name.endswith(".stoverride"):
            raise ValueError(f"invalid output filename in source mapping: {output_name!r}")
        source_text = fetch_source(item["source"])
        output_path = root / "stash" / output_name
        generated.append(
            (
                output_path,
                inherit_payloads(output_path, convert_source(item, source_text)),
            )
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
