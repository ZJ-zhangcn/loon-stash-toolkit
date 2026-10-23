#!/usr/bin/env python3
"""Convert supported Loon plugin and Stash override syntax.

The converter intentionally supports the request/response rewrite subset used
by this repository. Unsupported Stash features are reported instead of being
silently emitted with a different behavior.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


LOON_SCRIPT_SECTIONS = {
    "[RULE]": "rules",
    "[URL REWRITE]": "url_rewrite",
    "[HEADER REWRITE]": "header_rewrite",
    "[BODY REWRITE]": "body_rewrite",
}

STASH_LIST_KEYS = {
    "mitm": "mitm",
    "url-rewrite": "url_rewrite",
    "header-rewrite": "header_rewrite",
    "body-rewrite": "body_rewrite",
}

METADATA_KEYS = (
    "name",
    "desc",
    "author",
    "homepage",
    "icon",
    "category",
    "date",
    "version",
)


@dataclass
class ScriptRule:
    match: str
    url: str | None = None
    type: str = "response"
    require_body: bool | None = None
    binary_mode: bool | None = None
    timeout: int | None = None
    argument: str | None = None
    name: str | None = None
    tag: str | None = None
    argument_names: list[str] = field(default_factory=list)


@dataclass
class Document:
    metadata: dict[str, str] = field(default_factory=dict)
    rules: list[str] = field(default_factory=list)
    mitm: list[str] = field(default_factory=list)
    url_rewrite: list[str] = field(default_factory=list)
    header_rewrite: list[str] = field(default_factory=list)
    body_rewrite: list[str] = field(default_factory=list)
    scripts: list[ScriptRule] = field(default_factory=list)
    mocks: list[dict[str, Any]] = field(default_factory=list)
    script_providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_ignorable(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid double-quoted YAML scalar: {value}") from exc
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError(f"invalid single-quoted YAML scalar: {value}")
        return value[1:-1].replace("''", "'")
    return re.split(r"\s+#", value, maxsplit=1)[0].rstrip()


def _quote(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _parse_mapping(line: str) -> tuple[int, str, str] | None:
    match = re.match(
        r"^( *)(?:\"([^\"]+)\"|'([^']+)'|([A-Za-z0-9_.-]+)):\s*(.*)$",
        line,
    )
    if not match:
        return None
    key = match.group(2) or match.group(3) or match.group(4)
    return len(match.group(1)), key, match.group(5)


def _consume_block_scalar(
    lines: list[str], start: int, parent_indent: int, marker: str
) -> tuple[int, str]:
    collected: list[str] = []
    content_indent: int | None = None
    index = start
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            collected.append("")
            index += 1
            continue
        if _indent_of(line) <= parent_indent:
            break
        if content_indent is None:
            # YAML 块标量的内容缩进由第一个非空行决定；固定 +2 会在更深缩进时吃掉首字符
            content_indent = _indent_of(line)
        collected.append(line[content_indent:])
        index += 1

    if marker.startswith(">"):
        result = " ".join(part.strip() for part in collected if part.strip())
    else:
        result = "\n".join(collected)
    if not marker.endswith("-"):
        result += "\n"
    return index, result.rstrip("\n") if marker.endswith("-") else result


def _consume_string_list(
    lines: list[str], start: int, item_indent: int
) -> tuple[int, list[str]]:
    values: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if _is_ignorable(line):
            index += 1
            continue
        indent = _indent_of(line)
        if indent < item_indent:
            break
        if indent != item_indent or not line.lstrip().startswith("-"):
            raise ValueError(f"expected a list item at indent {item_indent}: {line}")
        raw = line.lstrip()[1:].strip()
        if raw in {">", ">-", ">+", "|", "|-", "|+"}:
            index, value = _consume_block_scalar(lines, index + 1, item_indent, raw)
            values.append(value)
            continue
        values.append(_parse_scalar(raw))
        index += 1
    return index, values


def _consume_mapping_items(
    lines: list[str], start: int, item_indent: int
) -> tuple[int, list[dict[str, str]]]:
    items: list[dict[str, str]] = []
    index = start
    current: dict[str, str] | None = None
    while index < len(lines):
        line = lines[index]
        if _is_ignorable(line):
            index += 1
            continue
        indent = _indent_of(line)
        if indent < item_indent:
            break

        stripped = line.lstrip()
        if indent == item_indent and stripped.startswith("-"):
            if current is not None:
                items.append(current)
            current = {}
            remainder = stripped[1:].strip()
            if not remainder:
                index += 1
                continue
            parsed = _parse_mapping(remainder)
            if parsed is None:
                raise ValueError(f"invalid script item: {line}")
            _, key, value = parsed
            current[key] = _parse_scalar(value)
            index += 1
            continue

        if current is None or indent <= item_indent:
            raise ValueError(f"invalid nested mapping item: {line}")
        parsed = _parse_mapping(line)
        if parsed is None:
            raise ValueError(f"invalid nested mapping value: {line}")
        _, key, value = parsed
        current[key] = _parse_scalar(value)
        index += 1

    if current is not None:
        items.append(current)
    return index, items


def _consume_http(
    lines: list[str], start: int, document: Document
) -> int:
    index = start
    while index < len(lines):
        line = lines[index]
        if _is_ignorable(line):
            index += 1
            continue
        indent = _indent_of(line)
        if indent < 2:
            break
        parsed = _parse_mapping(line)
        if parsed is None or indent != 2:
            raise ValueError(f"invalid http mapping: {line}")
        _, key, value = parsed
        if value:
            raise ValueError(f"unsupported inline http value for {key}: {value}")

        if key in STASH_LIST_KEYS:
            index, values = _consume_string_list(lines, index + 1, 4)
            setattr(document, STASH_LIST_KEYS[key], values)
            continue
        if key == "script":
            index, items = _consume_mapping_items(lines, index + 1, 4)
            for item in items:
                if "match" not in item:
                    raise ValueError("Stash script item is missing match")
                document.scripts.append(
                    ScriptRule(
                        match=item["match"],
                        type=item.get("type", "response"),
                        require_body=(
                            _parse_bool(item["require-body"])
                            if "require-body" in item
                            else None
                        ),
                        binary_mode=(
                            _parse_bool(item["binary-mode"])
                            if "binary-mode" in item
                            else None
                        ),
                        timeout=(
                            int(item["timeout"]) if "timeout" in item else None
                        ),
                        argument=item.get("argument"),
                        name=item.get("name"),
                    )
                )
            continue
        if key == "mock":
            index, items = _consume_mapping_items(lines, index + 1, 4)
            for item in items:
                if "match" not in item:
                    raise ValueError("Stash mock item is missing match")
                document.mocks.append(item)
            continue

        document.warnings.append(f"ignored unsupported http section: {key}")
        index = _skip_nested_block(lines, index + 1, 2)
    return index


def _consume_script_providers(
    lines: list[str], start: int
) -> tuple[int, dict[str, dict[str, Any]]]:
    providers: dict[str, dict[str, Any]] = {}
    index = start
    while index < len(lines):
        line = lines[index]
        if _is_ignorable(line):
            index += 1
            continue
        indent = _indent_of(line)
        if indent < 2:
            break
        if indent != 2:
            raise ValueError(f"invalid script provider name indentation: {line}")
        parsed = _parse_mapping(line)
        if parsed is None:
            raise ValueError(f"invalid script provider: {line}")
        _, name, value = parsed
        if value:
            raise ValueError(f"invalid inline script provider value: {line}")

        provider: dict[str, Any] = {}
        index += 1
        while index < len(lines):
            inner = lines[index]
            if _is_ignorable(inner):
                index += 1
                continue
            inner_indent = _indent_of(inner)
            if inner_indent <= 2:
                break
            parsed_inner = _parse_mapping(inner)
            if parsed_inner is None or inner_indent != 4:
                raise ValueError(f"invalid script provider property: {inner}")
            _, key, inner_value = parsed_inner
            if inner_value.strip() in {"|", "|-", ">", ">-"}:
                index, provider[key] = _consume_block_scalar(
                    lines, index + 1, inner_indent, inner_value.strip()
                )
                continue
            provider[key] = _parse_scalar(inner_value)
            index += 1
        providers[name] = provider
    return index, providers


def _skip_nested_block(lines: list[str], start: int, parent_indent: int) -> int:
    index = start
    while index < len(lines):
        line = lines[index]
        if _is_ignorable(line):
            index += 1
            continue
        if _indent_of(line) <= parent_indent:
            break
        index += 1
    return index


def parse_stash(text: str) -> Document:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    document = Document()
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_ignorable(line):
            index += 1
            continue
        indent = _indent_of(line)
        if indent != 0:
            raise ValueError(f"unexpected top-level indentation: {line}")
        parsed = _parse_mapping(line)
        if parsed is None:
            raise ValueError(f"invalid top-level mapping: {line}")
        _, key, raw_value = parsed

        if key in METADATA_KEYS:
            if raw_value in {"|", "|-", ">", ">-"}:
                index, value = _consume_block_scalar(
                    lines, index + 1, indent, raw_value
                )
            else:
                value = _parse_scalar(raw_value)
                index += 1
            document.metadata[key] = value
            continue
        if key == "rules":
            if raw_value:
                raise ValueError("inline rules are not supported")
            index, document.rules = _consume_string_list(lines, index + 1, 2)
            continue
        if key == "http":
            if raw_value:
                raise ValueError("inline http mappings are not supported")
            index = _consume_http(lines, index + 1, document)
            continue
        if key == "script-providers":
            if raw_value:
                raise ValueError("inline script-providers are not supported")
            index, document.script_providers = _consume_script_providers(
                lines, index + 1
            )
            continue

        document.warnings.append(f"ignored unsupported top-level key: {key}")
        index = _skip_nested_block(lines, index + 1, 0)

    for script in document.scripts:
        if not script.name:
            document.warnings.append(
                f"Stash script without name cannot resolve a provider: {script.match}"
            )
            continue
        provider = document.script_providers.get(script.name)
        if not provider or not provider.get("url"):
            document.warnings.append(
                f"Stash script has no provider URL: {script.name}"
            )
            continue
        script.url = str(provider["url"])
    return document


def _normalize_loon_regex(pattern: str, flags: str = "") -> str:
    """Stash 的 match 走 NSRegularExpression：斜杠必须转义。

    `(?i)` 可以保留（其它覆写已在使用），但不能让裸 `/` 进到 match 里。
    """
    # Stash 的 match 不接受内联 (?i)，可用的 Bilibili 覆写全部不带该 flag
    pattern = pattern.replace(r"\/", "/").replace("(?i)", "").replace("/", r"\/")
    return pattern


def _parse_loon_url_condition(
    line: str,
) -> tuple[str, str, str, str | None] | None:
    match = re.match(
        r"^(request|response)\s+if\s+\$\{url\}\s+~=\s+/(.*)/([a-z]*)(?:\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*))?\s+then\s+(.+)$",
        line,
    )
    if not match:
        return None
    directive, pattern, flags, match_name, action = match.groups()
    return directive, _normalize_loon_regex(pattern, flags), action, match_name


def _loon_reject_action(action: str) -> str:
    if action in {"reject", "reject-200", "reject-dict", "reject-array", "reject-img"}:
        return action
    match = re.fullmatch(r"(reject|reject_dict|reject_array|reject_img)\((\d+)\)", action)
    if not match:
        raise ValueError(f"unsupported Loon reject action: {action}")
    kind, status = match.groups()
    if kind == "reject":
        if status == "404":
            return "reject"
        if status == "200":
            return "reject-200"
        raise ValueError(f"unsupported Loon reject status: {status}")
    return kind.replace("_", "-")


def _jq_path(path: str) -> str:
    value = path.strip()
    if value.startswith("$."):
        value = value[1:]
    elif not value.startswith("."):
        value = f".{value}"
    if not re.fullmatch(
        r"\.[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+|\[\d+\])*",
        value,
    ):
        raise ValueError(f"unsupported Loon JSON path: {path}")
    return re.sub(
        r"\.([A-Za-z0-9_$-]+)",
        lambda match: f'."{match.group(1)}"' if ("-" in match.group(1) or match.group(1)[0].isdigit()) else match.group(0),
        value,
    )


def _jq_literal(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
    else:
        parsed = value
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


JQ_MIRROR_PREFIX = "https://git.repcz.link/kelee.one/"


def _fetch_jq_program(url: str) -> str:
    """取远端 .jq 并压成单行：Stash response-jq 只能内联表达式。"""
    mirror = url.replace("https://kelee.one/", JQ_MIRROR_PREFIX)
    try:
        request = urllib.request.Request(mirror, headers={"User-Agent": "Mozilla/5.0"})
        source = urllib.request.urlopen(request, timeout=30).read().decode("utf-8")
    except Exception as exc:
        raise ValueError(f"cannot fetch jq program {url}: {exc}") from exc
    kept = []
    for line in source.split("\n"):
        if line.lstrip().startswith("#"):
            continue
        kept.append(re.sub(r"\s+#\s.*$", "", line))
    return " ".join(part.strip() for part in kept if part.strip())


def _strip_jq_comments(expression: str) -> str:
    """去掉 jq 的 # 行注释，字符串内的 # 保留。

    jq 注释吃到行尾，而 Stash 的 response-jq 必须是单行；先把注释剥掉再压平，
    否则第一个 # 会把后面的整个程序注释掉。
    """
    out: list[str] = []
    quoted = escaped = False
    index = 0
    while index < len(expression):
        char = expression[index]
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\" and quoted:
            out.append(char)
            escaped = True
        elif char == '"':
            quoted = not quoted
            out.append(char)
        elif char == "#" and not quoted:
            while index < len(expression) and expression[index] != "\n":
                index += 1
            out.append("\n")
            continue
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _flatten_jq(expression: str) -> str:
    """Stash 的 response-jq 是单行正则字段，多行表达式会被截断。"""
    cleaned = _strip_jq_comments(expression)
    return " ".join(part.strip() for part in cleaned.split("\n") if part.strip())


def _loon_response_jq(action: str, fetch: bool = True) -> str:
    jq_file = "response.json.jq_file("
    if action.startswith(jq_file) and action.endswith(")"):
        url = json.loads(action[len(jq_file) : -1])
        if not fetch:
            raise ValueError(f"jq_file needs --inline-jq to fetch: {url}")
        return _fetch_jq_program(url)
    for method in ("jq", "delete", "replace"):
        prefix = f"response.json.{method}("
        if not action.startswith(prefix) or not action.endswith(")"):
            continue
        args_text = action[len(prefix) : -1]
        if method == "jq":
            expression = json.loads(args_text)
            if not isinstance(expression, str):
                raise ValueError("Loon response.json.jq expects a string")
            return _flatten_jq(expression)
        args = json.loads(args_text if method == "delete" else f"[{args_text}]")
        if method == "delete":
            paths = args if isinstance(args, list) else [args]
            return f"del({', '.join(_jq_path(path) for path in paths)})"
        if not isinstance(args, list) or len(args) != 2:
            raise ValueError("Loon response.json.replace expects path and value")
        paths = args[0] if isinstance(args[0], list) else [args[0]]
        values = args[1] if isinstance(args[1], list) else [args[1]]
        if len(paths) != len(values):
            raise ValueError("Loon response.json.replace path/value counts differ")
        return " | ".join(
            f"({_jq_path(path)} = {_jq_literal(value)})"
            for path, value in zip(paths, values)
        )
    raise ValueError(f"unsupported Loon response rewrite: {action}")


def _split_top_level_commas(text: str) -> list[str]:
    """按顶层逗号切分 jq 参数，保留引号内的逗号。"""
    parts: list[str] = []
    buffer: list[str] = []
    quoted = escaped = False
    for char in text:
        if escaped:
            buffer.append(char)
            escaped = False
            continue
        if char == "\\" and quoted:
            buffer.append(char)
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            parts.append("".join(buffer).strip())
            buffer = []
            continue
        buffer.append(char)
    tail = "".join(buffer).strip()
    if tail:
        parts.append(tail)
    return parts


# Stash 解析超长 rewrite 单行会失败，并让整层覆写的脚本全部失效。
# 实测 7.2 KB 的单行正常，27 KB 的单行必然出问题；取 8 KB 作硬上限。
BODY_REWRITE_MAX_CHARS = 8000


def _split_oversized_del(entry: str, limit: int = BODY_REWRITE_MAX_CHARS) -> list[str]:
    """把超长的 del(a, b, c) 拆成多条等价的 body-rewrite。

    只处理扁平的 del() 路径列表；含嵌套路径或非 del 的 jq 程序保持原样，
    因为拆分可能改变语义。
    """
    if len(entry) <= limit:
        return [entry]
    match = re.fullmatch(r"(?P<prefix>\S+) response-jq del\((?P<paths>.+)\)", entry, re.S)
    if not match:
        return [entry]
    prefix = match["prefix"]
    paths = _split_top_level_commas(match["paths"])
    if len(paths) < 2:
        return [entry]
    # 嵌套路径拆分后可能丢掉父级删除的副作用，保持原样
    flat = {path for path in paths}
    if any(path.startswith(other + ".") for path in paths for other in flat if other != path):
        return [entry]

    overhead = len(prefix) + len(" response-jq del()")
    chunks: list[str] = []
    current: list[str] = []
    size = overhead
    for path in paths:
        added = len(path) + 2  # ", "
        if current and size + added > limit:
            chunks.append(f"{prefix} response-jq del({', '.join(current)})")
            current, size = [], overhead
        current.append(path)
        size += added
    if current:
        chunks.append(f"{prefix} response-jq del({', '.join(current)})")
    return chunks


def _split_loon_call(text: str) -> list[str]:
    """按顶层逗号切分 Loon 调用参数，保留双引号内的逗号与转义。"""
    parts: list[str] = []
    buffer: list[str] = []
    quoted = escaped = False
    for char in text:
        if escaped:
            buffer.append(char)
            escaped = False
            continue
        if char == "\\" and quoted:
            buffer.append(char)
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            buffer.append(char)
            continue
        if char == "," and not quoted:
            parts.append("".join(buffer).strip())
            buffer = []
            continue
        buffer.append(char)
    parts.append("".join(buffer).strip())
    return [
        json.loads(part) if len(part) >= 2 and part[0] == part[-1] == '"' else part
        for part in parts
    ]


def _parse_loon_rewrite(line: str) -> tuple[str, Any]:
    condition = _parse_loon_url_condition(line)
    if condition:
        directive, pattern, action, match_name = condition
        if directive == "request":
            if action.startswith("redirect(") and action.endswith(")"):
                arguments = next(
                    csv.reader([action[len("redirect(") : -1]], skipinitialspace=True)
                )
                if len(arguments) != 2 or arguments[0] not in {"302", "307"}:
                    raise ValueError(f"unsupported Loon redirect action: {action}")
                target = arguments[1].strip()
                if match_name:
                    target = re.sub(
                        rf"\$\{{{re.escape(match_name)}\.([1-9]\d*)\}}",
                        r"$\1",
                        target,
                    )
                if "${" in target:
                    raise ValueError(f"unsupported Loon redirect interpolation: {target}")
                if not target.startswith(("http://", "https://", "$")):
                    raise ValueError(f"unsupported Loon redirect target: {target}")
                return "url_rewrite", f"{pattern} {target} {arguments[0]}"
            return "url_rewrite", f"{pattern} - {_loon_reject_action(action)}"
        if action.startswith("response.body.mock(") and action.endswith(")"):
            arguments = _split_loon_call(action[len("response.body.mock(") : -1])
            if len(arguments) < 2 or arguments[0] != "text":
                raise ValueError(f"unsupported Loon body mock action: {action}")
            payload = arguments[1]
            if len(arguments) >= 3 and arguments[2] != "200":
                raise ValueError(f"unsupported Loon mock status: {action}")
            raw = len(arguments) >= 4 and arguments[3].lower() == "true"
            mock: dict[str, Any] = {"match": pattern, "status-code": 200}
            if raw:
                # Loon 的二进制 mock 已经是 base64；Stash 用 base64 字段承载原始字节
                mock["base64"] = payload
                mock["headers"] = {"Content-Type": "application/grpc", "grpc-status": "0"}
            else:
                try:
                    parsed = json.loads(payload) if payload else ""
                except json.JSONDecodeError as exc:
                    raise ValueError(f"unsupported Loon mock body: {action}") from exc
                mock["text"] = "" if parsed == "" else json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
                mock["headers"] = {"Content-Type": "application/json"}
            return "mock", mock
        header_add = re.fullmatch(r'response[.]header[.]add\("([^"]+)",\s*"([^"]*)"\)', action)
        if header_add:
            # Stash header-rewrite 语法（官方文档）: "<match> response-add <name> <value>"
            return (
                "header_rewrite",
                f"{pattern} response-add {_quote(header_add.group(1))} {_quote(header_add.group(2))}",
            )
        return "body_rewrite", f"{pattern} response-jq {_loon_response_jq(action)}"

    parts = line.rsplit(None, 1)
    if len(parts) == 2:
        pattern, action = parts
        if action in {"reject", "reject-200", "reject-dict", "reject-array", "reject-img"}:
            return (
                "url_rewrite",
                f"{_normalize_loon_regex(pattern)} - {action}",
            )
    raise ValueError(f"unsupported Loon Rewrite line: {line}")


def _argument_value(value: str) -> Any:
    normalized = value.strip()
    if normalized.lower() in {"true", "false"}:
        return normalized.lower() == "true"
    try:
        return json.loads(normalized)
    except json.JSONDecodeError:
        return normalized


def _parse_loon_argument(line: str) -> tuple[str, Any]:
    if "=" not in line:
        raise ValueError(f"invalid Loon argument: {line}")
    name, definition = line.split("=", 1)
    fields = next(csv.reader([definition], skipinitialspace=True))
    if len(fields) < 2:
        raise ValueError(f"Loon argument has no default value: {line}")
    return name.strip(), _argument_value(fields[1])


def _parse_loon_options(text: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for part in re.split(r",\s*(?=[A-Za-z][A-Za-z0-9_-]*=)", text):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        value = value.strip()
        if value.startswith('"'):
            value = json.loads(value)
        options[key.strip().lower()] = value
    return options


def _mirror_kelee_url(url: str) -> str:
    return re.sub(r"^https://kelee\.one/", "https://git.repcz.link/kelee.one/", url)


def _parse_loon_script(
    line: str,
    argument_defaults: dict[str, Any] | None = None,
    argument_values: dict[str, Any] | None = None,
) -> ScriptRule:
    condition = _parse_loon_url_condition(line)
    if condition:
        directive, pattern, action, _match_name = condition
        script_call = re.fullmatch(
            r'script\(("(?:\\.|[^"\\])*")(?:,\s*\{(.*?)\})?\)\s+with\s+(.+)',
            action,
        )
        if not script_call:
            raise ValueError(f"unsupported Loon script action: {action}")
        raw_url, raw_arguments, raw_options = script_call.groups()
        names = re.findall(r"\$\{([A-Za-z0-9_-]+)\}", raw_arguments or "")
        if raw_arguments and not names:
            raise ValueError(f"unsupported Loon script arguments: {raw_arguments}")
        defaults = argument_defaults or {}
        overrides = argument_values or {}
        values: dict[str, Any] = {}
        for name in names:
            if name in overrides:
                values[name] = overrides[name]
            elif name in defaults:
                values[name] = defaults[name]
            else:
                raise ValueError(f"Loon script argument has no default: {name}")
        options = _parse_loon_options(raw_options)
        if "enable" in options:
            enabled = options["enable"]
            if re.fullmatch(r"\$\{([A-Za-z0-9_-]+)\}", enabled):
                name = enabled[2:-1]
                if name in overrides:
                    enabled = overrides[name]
                elif name in defaults:
                    enabled = defaults[name]
                else:
                    raise ValueError(f"Loon enable has no default: {name}")
            if not _parse_bool(enabled):
                return ScriptRule(match=pattern, url="", type=directive)
        return ScriptRule(
            match=pattern,
            url=_mirror_kelee_url(json.loads(raw_url)),
            type=directive,
            require_body=(
                _parse_bool(options["requires_body"])
                if "requires_body" in options
                else None
            ),
            binary_mode=(
                _parse_bool(options["binary_body_mode"])
                if "binary_body_mode" in options
                else None
            ),
            timeout=int(options["timeout"]) if "timeout" in options else None,
            argument=(
                json.dumps(values, ensure_ascii=False, separators=(",", ":"))
                if values
                else None
            ),
            tag=options.get("tag"),
        )

    parts = line.split(None, 2)
    if len(parts) < 3:
        raise ValueError(f"invalid Loon script line: {line}")
    directive, match, options_text = parts
    if directive not in {"http-request", "http-response"}:
        raise ValueError(f"unsupported Loon script directive: {directive}")

    options = _parse_loon_options(options_text)
    timeout = int(options["timeout"]) if "timeout" in options else None
    return ScriptRule(
        match=match,
        url=_mirror_kelee_url(options.get("script-path", "")) or None,
        type="request" if directive == "http-request" else "response",
        require_body=(
            _parse_bool(options["requires-body"])
            if "requires-body" in options
            else None
        ),
        binary_mode=(
            _parse_bool(options["binary-body-mode"])
            if "binary-body-mode" in options
            else None
        ),
        timeout=timeout,
        argument=options.get("argument"),
        tag=options.get("tag"),
    )


def _normalize_loon_rule(line: str) -> str:
    """Loon 允许 `DOMAIN, x, PROXY` 这种带空格的写法，Stash 只认紧凑格式。"""
    rule = re.sub(r"\s*,\s*", ",", line.strip())
    # Loon 的 PROXY 是「默认策略」，Stash 没有同名出站，落地为 DIRECT
    return re.sub(r",PROXY$", ",DIRECT", rule)


def parse_loon(
    text: str, argument_values: dict[str, Any] | None = None
) -> Document:
    document = Document()
    section: str | None = None
    argument_defaults: dict[str, Any] = {}
    argument_values = argument_values or {}
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for original in lines:
        line = original.strip()
        if not line or line.startswith("#") and not line.startswith("#!"):
            continue
        if line.startswith("#!"):
            if "=" not in line:
                document.warnings.append(f"ignored invalid metadata line: {line}")
                continue
            key, value = line[2:].split("=", 1)
            document.metadata[key.strip().lower()] = value.strip()
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.upper()
            continue

        if section == "[ARGUMENT]":
            try:
                name, value = _parse_loon_argument(line)
                argument_defaults[name] = value
            except (ValueError, json.JSONDecodeError) as exc:
                document.warnings.append(str(exc))
            continue
        if section == "[REWRITE]":
            try:
                key, value = _parse_loon_rewrite(line)
                if key == "mock":
                    document.mocks.append(value)
                else:
                    getattr(document, key).append(value)
            except (ValueError, json.JSONDecodeError) as exc:
                document.warnings.append(str(exc))
            continue
        if section in LOON_SCRIPT_SECTIONS:
            if section == "[RULE]":
                line = _normalize_loon_rule(line)
            getattr(document, LOON_SCRIPT_SECTIONS[section]).append(line)
            continue
        if section == "[SCRIPT]":
            try:
                document.scripts.append(
                    _parse_loon_script(line, argument_defaults, argument_values)
                )
            except (ValueError, json.JSONDecodeError) as exc:
                document.warnings.append(str(exc))
            continue
        if section == "[MITM]":
            if line.lower().startswith("hostname"):
                _, value = line.split("=", 1)
                document.mitm.extend(
                    host.strip() for host in value.split(",") if host.strip()
                )
            else:
                document.warnings.append(f"ignored unsupported MITM line: {line}")
            continue
        if section is None:
            document.warnings.append(f"ignored content outside a section: {line}")
            continue
        document.warnings.append(f"ignored unsupported Loon section line: {line}")
    return document


def _script_name(stem: str, index: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    if not slug:
        slug = "plugin"
    return f"loon_{slug}_{index:02d}"


def to_stash(document: Document, stem: str = "plugin") -> str:
    output: list[str] = []
    metadata = dict(document.metadata)
    if "name" not in metadata:
        metadata["name"] = stem

    output.append(f"name: {_quote(metadata['name'])}")
    if metadata.get("desc"):
        output.append("desc: |-")
        # Loon 的 #!desc 用字面量 \n 分段，块标量里要还原成真换行
        desc = metadata["desc"].replace("\\n", "\n")
        output.extend(f"  {line}" for line in desc.splitlines())
    for key in ("author", "homepage", "icon"):
        if metadata.get(key):
            output.append(f"{key}: {_quote(metadata[key])}")
    category = metadata.get("category") or metadata.get("tag")
    if category:
        output.append(f"category: {_quote(category)}")
    for key in ("date", "version"):
        if metadata.get(key):
            output.append(f"{key}: {_quote(metadata[key])}")

    if document.rules:
        output.append("rules:")
        output.extend(f"  - {_quote(rule)}" for rule in document.rules)

    valid_scripts = [script for script in document.scripts if script.url]
    for script in document.scripts:
        if not script.url:
            document.warnings.append(
                f"skipped Loon script without script-path: {script.match}"
            )

    has_http = any(
        (
            document.mitm,
            document.url_rewrite,
            document.header_rewrite,
            document.body_rewrite,
            valid_scripts,
            document.mocks,
        )
    )
    if has_http:
        output.append("http:")
        for key, values in (
            ("mitm", document.mitm),
            ("url-rewrite", document.url_rewrite),
            ("header-rewrite", document.header_rewrite),
            (
                "body-rewrite",
                [
                    chunk
                    for entry in document.body_rewrite
                    for chunk in _split_oversized_del(entry)
                ],
            ),
        ):
            if values:
                output.append(f"  {key}:")
                output.extend(f"    - {_quote(value)}" for value in values)

        if document.mocks:
            output.append("  mock:")
            for item in document.mocks:
                output.append(f"    - match: {_quote(str(item['match']))}")
                if "base64" in item:
                    output.append(f"      base64: {_quote(str(item['base64']))}")
                else:
                    output.append(f"      text: {_quote(str(item.get('text', '')))}")
                output.append(f"      status-code: {int(item.get('status-code', 200))}")
                headers = item.get("headers")
                if isinstance(headers, dict):
                    output.append("      headers:")
                    for name, value in headers.items():
                        output.append(f"        {name}: {_quote(str(value))}")

        providers: dict[str, dict[str, Any]] = {}
        # 同一个脚本 URL 只登记一个 provider：Stash 按 provider 名缓存脚本内容，
        # 同名不同 URL 或同 URL 多名都会让缓存与 argument 错配，整个 script 数组失效。
        provider_by_url: dict[str, str] = {}
        if valid_scripts:
            output.append("  script:")
        for index, script in enumerate(valid_scripts, start=1):
            name = provider_by_url.get(script.url) or _script_name(stem, index)
            provider_by_url.setdefault(script.url, name)
            output.append(f"    - match: {_quote(script.match)}")
            output.append(f"      name: {_quote(name)}")
            output.append(f"      type: {_quote(script.type)}")
            if script.require_body is not None:
                output.append(
                    f"      require-body: {str(script.require_body).lower()}"
                )
            if script.binary_mode is not None:
                output.append(f"      binary-mode: {str(script.binary_mode).lower()}")
            if script.timeout is not None:
                output.append(f"      timeout: {script.timeout}")
            if script.argument:
                output.append(f"      argument: {_quote(script.argument)}")
            providers[name] = {"url": script.url, "interval": 86400}

        if providers:
            output.append("script-providers:")
            for name, provider in providers.items():
                output.append(f"  {_quote(name)}:")
                output.append(f"    url: {_quote(provider['url'])}")
                output.append(f"    interval: {provider['interval']}")

    return "\n".join(output).rstrip() + "\n"


def to_loon(document: Document) -> str:
    output: list[str] = []
    metadata = document.metadata
    output.append(f"#!name={metadata.get('name', 'Converted plugin')}")
    if metadata.get("desc"):
        desc = " ".join(part.strip() for part in metadata["desc"].splitlines())
        output.append(f"#!desc={desc}")
    for key in ("author", "homepage", "icon"):
        if metadata.get(key):
            output.append(f"#!{key}={metadata[key]}")
    tag = metadata.get("tag") or metadata.get("category")
    if tag:
        output.append(f"#!tag={tag}")
    for key in ("date", "version"):
        if metadata.get(key):
            output.append(f"#!{key}={metadata[key]}")

    sections: list[tuple[str, Iterable[str]]] = [
        ("[Rule]", document.rules),
        ("[URL Rewrite]", document.url_rewrite),
        ("[Header Rewrite]", document.header_rewrite),
        ("[Body Rewrite]", document.body_rewrite),
    ]
    for title, values in sections:
        values = list(values)
        if not values:
            continue
        output.extend(["", title])
        output.extend(values)

    valid_scripts = [script for script in document.scripts if script.url]
    if valid_scripts:
        output.extend(["", "[Script]"])
        for script in valid_scripts:
            directive = (
                "http-request" if script.type.lower() == "request" else "http-response"
            )
            line = f"{directive} {script.match} script-path={script.url}"
            if script.require_body is not None:
                line += f", requires-body={str(script.require_body).lower()}"
            if script.binary_mode is not None:
                line += f", binary-body-mode={str(script.binary_mode).lower()}"
            if script.timeout is not None:
                line += f", timeout={script.timeout}"
            if script.argument:
                line += f", argument={script.argument}"
            if script.tag:
                line += f", tag={script.tag}"
            output.append(line)

    if document.mitm:
        output.extend(["", "[MITM]", f"hostname = {', '.join(document.mitm)}"])

    return "\n".join(output).rstrip() + "\n"


def convert_text(
    direction: str,
    text: str,
    stem: str,
    warnings: list[str] | None = None,
    *,
    argument_values: dict[str, Any] | None = None,
) -> str:
    if direction == "loon-to-stash":
        document = parse_loon(text, argument_values=argument_values)
        result = to_stash(document, stem)
    elif direction == "stash-to-loon":
        document = parse_stash(text)
        result = to_loon(document)
    else:
        raise ValueError(f"unsupported direction: {direction}")
    if warnings is not None:
        warnings.extend(document.warnings)
    return result


def convert_file(
    direction: str, input_path: Path, output_path: Path | None
) -> list[str]:
    warnings: list[str] = []
    text = input_path.read_text(encoding="utf-8")
    result = convert_text(direction, text, input_path.stem, warnings)
    if output_path is None:
        sys.stdout.write(result)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result, encoding="utf-8", newline="\n")
    return warnings


def _default_extension(direction: str) -> str:
    return ".stoverride" if direction == "loon-to-stash" else ".lpx"


def _input_extension(direction: str) -> str:
    return ".stoverride" if direction == "stash-to-loon" else ".lpx"


def _batch_convert(direction: str, input_dir: Path, output_dir: Path) -> list[str]:
    warnings: list[str] = []
    extension = _input_extension(direction)
    files = sorted(path for path in input_dir.iterdir() if path.suffix == extension)
    if not files:
        raise ValueError(f"no {extension} files found in {input_dir}")
    for input_path in files:
        output_path = output_dir / f"{input_path.stem}{_default_extension(direction)}"
        warnings.extend(convert_file(direction, input_path, output_path))
        print(f"{input_path} -> {output_path}")
    return warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert supported Loon .lpx and Stash .stoverride files."
    )
    parser.add_argument(
        "direction",
        choices=("loon-to-stash", "stash-to-loon"),
        help="conversion direction",
    )
    parser.add_argument("input", nargs="?", type=Path, help="single input file")
    parser.add_argument("-o", "--output", type=Path, help="output file; omit for stdout")
    parser.add_argument("--input-dir", type=Path, help="batch input directory")
    parser.add_argument("--output-dir", type=Path, help="batch output directory")
    parser.add_argument(
        "--quiet", action="store_true", help="do not print conversion warnings"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.input) == bool(args.input_dir):
        print("specify either input or --input-dir", file=sys.stderr)
        return 2
    if args.input_dir and not args.output_dir:
        print("--output-dir is required with --input-dir", file=sys.stderr)
        return 2
    if args.input and args.output_dir:
        print("--output-dir is only valid with --input-dir", file=sys.stderr)
        return 2
    if args.input_dir and args.output:
        print("--output is only valid with a single input file", file=sys.stderr)
        return 2

    try:
        if args.input_dir:
            warnings = _batch_convert(args.direction, args.input_dir, args.output_dir)
        else:
            warnings = convert_file(args.direction, args.input, args.output)
    except (OSError, ValueError) as exc:
        print(f"conversion failed: {exc}", file=sys.stderr)
        return 1

    if warnings and not args.quiet:
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
