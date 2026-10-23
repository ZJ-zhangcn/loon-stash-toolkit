#!/usr/bin/env python3
"""把远程脚本快照内联进 stash/*.stoverride 的 script-providers。

kelee.one 会对部分 IP 段返回 403，脚本随时可能取不到；快照后 Stash
启动先用本地 payload，更新成功再覆盖。下载失败的 URL 保持原样并告警。
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STASH = ROOT / "stash"
SKIP_FILES = {"Kelee_All.stoverride"}  # 合集由 generate_aggregate.rb 生成
MARKER = "  # payload 为脚本快照，kelee.one 取不到脚本时的兜底\n"
# ponytail: 只认 generate_aggregate.rb 的单行 url，多行 provider 结构出现时再扩展
PROVIDER = re.compile(
    r'^(?P<indent> +)(?P<name>"?[\w\-\. ]+"?):\n'
    r'(?P=indent)  url: "(?P<url>https://[^"]+\.js)"\n'
    r'(?P=indent)  interval: (?P<interval>\d+)\n'
    # 已有快照一并吃掉，重写而不是追加，保证重复运行幂等
    r'(?:(?P=indent)  payload: \|-\n(?:[ ]{6,}[^\n]*\n|\n)*)?',
    re.M,
)


def fetch(url: str) -> str | None:
    request = urllib.request.Request(url, headers={"User-Agent": "loon-stash-toolkit/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read()
    except (OSError, urllib.error.URLError) as exc:
        print(f"warn: fetch failed {url}: {exc}", file=sys.stderr)
        return None
    if len(body) < 50:
        print(f"warn: too small {url} ({len(body)}B)", file=sys.stderr)
        return None
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        print(f"warn: not utf-8 {url}", file=sys.stderr)
        return None


def inline(path: Path, cache: dict[str, str | None]) -> int:
    """整段重写 script-providers，避免与 sync_sources 的继承逻辑叠加出重复快照。"""
    text = path.read_text(encoding="utf-8")
    head, marker, tail = text.partition("script-providers:\n")
    if not marker:
        return 0

    entries = re.split(r"(?m)^(?=  \"?[\w.\- ]+\"?:$)", tail)
    changed = 0
    rebuilt: list[str] = []
    for entry in entries:
        m = re.match(
            r'^(  \"?[\w.\- ]+\"?:\n)    url: "(?P<url>https://[^"]+\.js)"\n    interval: (?P<interval>\d+)\n',
            entry,
        )
        if not m:
            rebuilt.append(entry)
            continue
        url = m.group("url")
        if url not in cache:
            cache[url] = fetch(url)
        payload = cache[url]
        block = entry[: m.end()]
        if payload is None:
            rebuilt.append(block)
            continue
        changed += 1
        body = payload.replace("\t", "\\t").replace("\b", "\\b")
        body = "\n".join(
            line.rstrip(" ") + "\\x20" * (len(line) - len(line.rstrip(" ")))
            if line.endswith(" ") else line
            for line in body.split("\n")
        )
        content = "".join(
            f"      {line}\n" if line.strip() else "\n" for line in body.splitlines()
        )
        rebuilt.append(block + "    payload: |-\n" + content)

    new_text = head + marker + "".join(rebuilt)
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
    return changed


def main() -> int:
    cache: dict[str, str | None] = {}
    for path in sorted(STASH.glob("*.stoverride")):
        if path.name in SKIP_FILES:
            continue
        if "script-providers:" not in path.read_text(encoding="utf-8"):
            continue
        count = inline(path, cache)
        print(f"{path.name}: {count} payload")
    missing = [u for u, v in cache.items() if v is None]
    if missing:
        print(f"missing {len(missing)}: " + ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
