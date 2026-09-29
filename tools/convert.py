#!/usr/bin/env python3
"""Convert supported Loon plugin and Stash override syntax.

The converter intentionally supports the request/response rewrite subset used
by this repository. Unsupported Stash features are reported instead of being
silently emitted with a different behavior.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


LOON_SCRIPT_SECTIONS = {
    "[Rule]": "rules",
    "[URL Rewrite]": "url_rewrite",
    "[Header Rewrite]": "header_rewrite",
    "[Body Rewrite]": "body_rewrite",
}

STASH_LIST_KEYS = {
    "mitm": "mitm",
    "url-rewrite": "url_rewrite",
    "header-rewrite": "header_rewrite",
    "body-rewrite": "body_rewrite",
}

METADATA_KEYS = ("name", "desc", "author", "homepage", "icon", "date", "version")


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


@dataclass
class Document:
    metadata: dict[str, str] = field(default_factory=dict)
    rules: list[str] = field(default_factory=list)
    mitm: list[str] = field(default_factory=list)
    url_rewrite: list[str] = field(default_factory=list)
    header_rewrite: list[str] = field(default_factory=list)
    body_rewrite: list[str] = field(default_factory=list)
    scripts: list[ScriptRule] = field(default_factory=list)
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
    index = start
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            collected.append("")
            index += 1
            continue
        if _indent_of(line) <= parent_indent:
            break
        content_indent = parent_indent + 2
        collected.append(line[content_indent:] if len(line) >= content_indent else "")
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
                match = item.get("match", "<unknown>")
                document.warnings.append(
                    f"Stash mock is not directly representable in Loon: {match}"
                )
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


def _parse_loon_script(line: str) -> ScriptRule:
    parts = line.split(None, 2)
    if len(parts) < 3:
        raise ValueError(f"invalid Loon script line: {line}")
    directive, match, options_text = parts
    if directive not in {"http-request", "http-response"}:
        raise ValueError(f"unsupported Loon script directive: {directive}")

    options: dict[str, str] = {}
    for part in re.split(r",\s*(?=[A-Za-z][A-Za-z0-9-]*=)", options_text):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        options[key.strip().lower()] = value.strip()

    timeout = int(options["timeout"]) if "timeout" in options else None
    return ScriptRule(
        match=match,
        url=options.get("script-path"),
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


def parse_loon(text: str) -> Document:
    document = Document()
    section: str | None = None
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
            section = line
            continue

        if section in LOON_SCRIPT_SECTIONS:
            getattr(document, LOON_SCRIPT_SECTIONS[section]).append(line)
            continue
        if section == "[Script]":
            document.scripts.append(_parse_loon_script(line))
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
        output.extend(f"  {line}" for line in metadata["desc"].splitlines())
    for key in ("author", "homepage", "icon", "date", "version"):
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
        )
    )
    if has_http:
        output.append("http:")
        for key, values in (
            ("mitm", document.mitm),
            ("url-rewrite", document.url_rewrite),
            ("header-rewrite", document.header_rewrite),
            ("body-rewrite", document.body_rewrite),
        ):
            if values:
                output.append(f"  {key}:")
                output.extend(f"    - {_quote(value)}" for value in values)

        providers: dict[str, dict[str, Any]] = {}
        if valid_scripts:
            output.append("  script:")
        for index, script in enumerate(valid_scripts, start=1):
            name = _script_name(stem, index)
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
    for key in ("author", "homepage", "icon", "date", "version"):
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
    direction: str, text: str, stem: str, warnings: list[str] | None = None
) -> str:
    if direction == "loon-to-stash":
        document = parse_loon(text)
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
