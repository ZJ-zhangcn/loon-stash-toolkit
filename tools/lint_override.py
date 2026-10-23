#!/usr/bin/env python3
"""Stash 覆写静态校验器。

按官方文档校验 .stoverride 的语法，避免"推上去才发现在客户端报错"。
用法: python3 tools/lint_override.py [文件...]   不带参数则检查 stash/ 下全部
"""
from __future__ import annotations

import glob
import re
import sys

import yaml

URL_ACTIONS = {"transparent", "302", "307", "reject", "reject-200", "reject-img",
               "reject-dict", "reject-array"}
HEADER_ACTIONS = {"request-add", "request-del", "request-replace", "request-replace-regex",
                  "response-add", "response-del", "response-replace", "response-replace-regex"}
BODY_ACTIONS = {"request-replace-regex", "response-replace-regex",
                "request-jq", "response-jq",
                "request-json-replace", "response-json-replace",
                "request-json-add", "response-json-add",
                "request-json-del", "response-json-del"}
SCRIPT_FIELDS = {"name", "match", "type", "require-body", "timeout", "argument",
                 "engine", "binary-mode", "max-size"}
SCRIPT_TYPES = {"request", "response"}
SCRIPT_ENGINES = {"auto", "webkit", "jsc"}
PROVIDER_FIELDS = {"url", "interval", "headers", "payload", "path"}

# Stash 解析超长 rewrite 单行会失败，并让整层覆写的脚本全部失效。
# 实测 7.2 KB 正常、27 KB 必坏。转换器按 8 KB 切片留余量；lint 上限放到 9 KB，
# 因为 Amap 有个 8 KB 的复杂 jq 程序（非 del() 列表，拆分会改变语义）在线上正常。
MAX_REWRITE_CHARS = 9000


def _action_of(value: str, legal: set[str], prefixes: tuple[str, ...]) -> str | None:
    """返回这条 rewrite 里第一个匹配 legal 的合法动作；没有则 None。"""
    for prefix in prefixes:
        for m in re.finditer(rf"(?<![\w-]){re.escape(prefix)}([a-z-]+)", value):
            if prefix + m.group(1) in legal:
                return prefix + m.group(1)
    return None


def check(path: str) -> list[str]:
    errors: list[str] = []
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"YAML 解析失败: {exc}"]
    if not isinstance(doc, dict):
        return ["顶层不是映射"]

    providers = doc.get("script-providers") or {}
    http = doc.get("http") or {}

    if not isinstance(providers, dict):
        errors.append("script-providers 不是映射")
        providers = {}
    for name, cfg in providers.items():
        if not isinstance(cfg, dict):
            errors.append(f"provider {name!r} 不是映射")
            continue
        unknown = set(cfg) - PROVIDER_FIELDS
        if unknown:
            errors.append(f"provider {name!r} 含未知字段 {sorted(unknown)}")
        if not cfg.get("url") and not cfg.get("payload") and not cfg.get("path"):
            errors.append(f"provider {name!r} 缺少 url/payload/path")
        if "payload" in cfg and cfg["payload"] is not None and not isinstance(cfg["payload"], str):
            errors.append(f"provider {name!r} 的 payload 不是字符串")

    for section, legal, prefixes in (
        ("url-rewrite", URL_ACTIONS, ("",)),
        ("header-rewrite", HEADER_ACTIONS, ("request-", "response-")),
        ("body-rewrite", BODY_ACTIONS, ("request-", "response-")),
    ):
        values = http.get(section) or []
        if not isinstance(values, list):
            errors.append(f"http.{section} 不是数组")
            continue
        for index, value in enumerate(values, 1):
            if not isinstance(value, str):
                errors.append(f"http.{section}[{index}] 不是字符串")
                continue
            if len(value) > MAX_REWRITE_CHARS:
                errors.append(
                    f"http.{section}[{index}] {len(value)} 字符，超过 {MAX_REWRITE_CHARS} "
                    "上限（Stash 会解析失败并连累所有覆写的脚本；需拆分为多条）"
                )
            if section == "url-rewrite":
                tail = value.rsplit(None, 1)[-1]
                if tail not in URL_ACTIONS:
                    errors.append(f"http.{section}[{index}] 非法动作 {tail!r}: {value[:80]}")
                continue
            # header/body 段的动作必须紧跟在 URL 正则之后
            found = _action_of(value, legal, prefixes)
            if found is None:
                bad = re.findall(r"(?:request|response)-[a-z-]+", value)
                hint = f"，疑似非法动作 {bad}" if bad else ""
                errors.append(f"http.{section}[{index}] 找不到合法动作{hint}: {value[:90]}")
            elif section == "body-rewrite" and "header" in found:
                errors.append(f"http.{section}[{index}] 头操作不属于 body-rewrite: {found}")
            elif section == "header-rewrite" and found.endswith(("jq",)):
                errors.append(f"http.{section}[{index}] jq 不属于 header-rewrite: {found}")

    for index, script in enumerate(http.get("script") or [], 1):
        if not isinstance(script, dict):
            errors.append(f"http.script[{index}] 不是映射")
            continue
        unknown = set(script) - SCRIPT_FIELDS
        if unknown:
            errors.append(f"http.script[{index}] 含未知字段 {sorted(unknown)}")
        name = script.get("name")
        if not name:
            errors.append(f"http.script[{index}] 缺少 name")
        elif name not in providers:
            errors.append(f"http.script[{index}] 引用的 provider {name!r} 不存在")
        if script.get("type") not in SCRIPT_TYPES:
            errors.append(f"http.script[{index}] type 非法: {script.get('type')!r}")
        if "engine" in script and script["engine"] not in SCRIPT_ENGINES:
            errors.append(f"http.script[{index}] engine 非法: {script['engine']!r}")
        if "argument" in script and not isinstance(script["argument"], str):
            errors.append(f"http.script[{index}] argument 必须是字符串")
        if "match" not in script:
            errors.append(f"http.script[{index}] 缺少 match")

    mocks = http.get("mock") or []
    if not isinstance(mocks, list):
        errors.append("http.mock 不是数组")
    else:
        for index, mock in enumerate(mocks, 1):
            if not isinstance(mock, dict) or "match" not in mock:
                errors.append(f"http.mock[{index}] 缺少 match")

    return errors


def main(argv: list[str]) -> int:
    paths = argv or sorted(glob.glob("stash/*.stoverride"))
    if not paths:
        print("没有找到 .stoverride 文件", file=sys.stderr)
        return 2
    failed = 0
    for path in paths:
        errors = check(path)
        if errors:
            failed += 1
            print(f"✗ {path}")
            for error in errors:
                print(f"    {error}")
    total = len(paths)
    print(f"\n{total - failed}/{total} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
