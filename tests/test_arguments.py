"""arguments 参数块：生成、模板替换、回写 Loon、lint 拒绝悬空引用。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.convert import parse_loon, parse_stash, to_loon, to_stash  # noqa: E402
from tools.lint_override import check  # noqa: E402

SCHEMA = {
    "logLevel": {"type": "input", "title": "日志级别", "default": "off"},
    "purifyComment": {"type": "switch", "title": "过滤置顶评论广告", "default": True},
    "displayUpList": {
        "type": "select",
        "title": "动态·最常访问",
        "default": "show",
        "options": ["show", "hide"],
    },
}

SOURCE = (
    "#!name=Args\n"
    "[Argument]\n"
    "logLevel = off\n"
    "purifyComment = true\n"
    "[Script]\n"
    "http-response ^https://api.example.com/x "
    "script-path=https://example.com/a.js, requires-body=1, "
    'argument={"logLevel":"off","purifyComment":true}\n'
)


def build_stash() -> str:
    document = parse_loon(SOURCE)
    document.arguments_schema = SCHEMA
    return to_stash(document, "args")


@contextmanager
def checked(body: str):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".stoverride", delete=False, encoding="utf-8"
    )
    handle.write(body)
    handle.close()
    try:
        yield check(handle.name)
    finally:
        Path(handle.name).unlink()


class ArgumentsTests(unittest.TestCase):
    def test_schema_block_is_emitted(self) -> None:
        stash = build_stash()
        self.assertIn("arguments-version: 1", stash)
        self.assertIn("  displayUpList:", stash)
        self.assertIn('      - value: "show"', stash)
        self.assertIn("    default: true", stash)

    def test_script_argument_uses_args_template(self) -> None:
        stash = build_stash()
        self.assertIn("${args.logLevel|json}", stash)
        self.assertIn("${args.purifyComment|json}", stash)

    def test_roundtrip_writes_defaults_back_to_loon(self) -> None:
        loon = to_loon(parse_stash(build_stash()))
        self.assertIn('argument={"logLevel":"off","purifyComment":true}', loon)

    def test_lint_accepts_declared_arguments(self) -> None:
        with checked(build_stash()) as errors:
            self.assertEqual(errors, [])

    def test_lint_rejects_undeclared_argument(self) -> None:
        stash = build_stash().replace("  logLevel:\n", "  renamed:\n", 1)
        with checked(stash) as errors:
            self.assertTrue(any("未声明" in error for error in errors), errors)

    def test_lint_rejects_select_without_options(self) -> None:
        stash = (
            "name: t\n"
            "arguments-version: 1\n"
            "arguments:\n"
            "  mode:\n"
            "    type: select\n"
            "    default: a\n"
        )
        with checked(stash) as errors:
            self.assertTrue(any("options" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()