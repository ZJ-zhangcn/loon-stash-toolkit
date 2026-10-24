"""校验 tools/lint_override.py 能抓到已知的非法语法。

每个用例对应一次真实踩过的坑，防止检查器悄悄失效。
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.lint_override import check  # noqa: E402


class LintOverrideTests(unittest.TestCase):
    def _check(self, body: str) -> list[str]:
        with tempfile.NamedTemporaryFile("w", suffix=".stoverride", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(body)
            path = handle.name
        try:
            return check(path)
        finally:
            Path(path).unlink()

    def test_rejects_response_header_add_in_body_rewrite(self) -> None:
        errors = self._check(
            "name: t\n"
            "http:\n"
            "  body-rewrite:\n"
            "    - '^https://x/ response-header-add \"grpc-status\" \"0\"'\n"
        )
        self.assertTrue(any("body-rewrite" in error for error in errors), errors)

    def test_rejects_response_header_add_in_header_rewrite(self) -> None:
        errors = self._check(
            "name: t\n"
            "http:\n"
            "  header-rewrite:\n"
            "    - '^https://x/ response-header-add \"grpc-status\" \"0\"'\n"
        )
        self.assertTrue(any("header-rewrite" in error for error in errors), errors)

    def test_rejects_surge_style_header_prefix(self) -> None:
        errors = self._check(
            "name: t\n"
            "http:\n"
            "  header-rewrite:\n"
            "    - 'http-response ^https://x/ header-add \"grpc-status\" \"0\"'\n"
        )
        self.assertTrue(errors)

    def test_accepts_documented_header_rewrite(self) -> None:
        self.assertEqual(
            self._check(
                "name: t\n"
                "http:\n"
                "  header-rewrite:\n"
                "    - '^https://x/ response-add \"grpc-status\" \"0\"'\n"
            ),
            [],
        )

    def test_rejects_unknown_provider_field(self) -> None:
        errors = self._check(
            "name: t\n"
            "script-providers:\n"
            "  p:\n"
            "    url: https://x/s.js\n"
            "    foo: bar\n"
        )
        self.assertTrue(any("未知字段" in error for error in errors), errors)

    def test_rejects_script_with_missing_provider(self) -> None:
        errors = self._check(
            "name: t\n"
            "http:\n"
            "  script:\n"
            "    - name: nope\n"
            "      match: ^https://x/\n"
            "      type: request\n"
        )
        self.assertTrue(any("不存在" in error for error in errors), errors)

    def test_rejects_illegal_url_rewrite_action(self) -> None:
        errors = self._check(
            "name: t\n"
            "http:\n"
            "  url-rewrite:\n"
            "    - '^https://x/ redirect'\n"
        )
        self.assertTrue(errors)

    def test_rejects_unknown_rule_type(self) -> None:
        errors = self._check(
            "name: t\n"
            "rules:\n"
            "  - MAGIC-RULE,b.com,DIRECT\n"
        )
        self.assertTrue(any("未知规则类型" in error for error in errors), errors)

    def test_accepts_official_rule_types(self) -> None:
        self.assertEqual(
            self._check(
                "name: t\n"
                "rules:\n"
                "  - DOMAIN,a.com,DIRECT\n"
                "  - IP-CIDR,1.2.3.0/24,REJECT\n"
                "  - URL-REGEX,^https://x/,DIRECT\n"
            ),
            [],
        )

    def test_repository_overrides_are_clean(self) -> None:
        failures = {
            path.name: check(str(path))
            for path in sorted((ROOT / "stash").glob("*.stoverride"))
            if check(str(path))
        }
        self.assertEqual(failures, {})


if __name__ == "__main__":
    unittest.main()
