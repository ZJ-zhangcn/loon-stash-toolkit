from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.convert import convert_text, parse_loon, parse_stash  # noqa: E402


class ConversionTests(unittest.TestCase):
    def test_all_loon_plugins_convert_to_parseable_stash(self) -> None:
        for path in sorted((ROOT / "plugins").glob("*.lpx")):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                converted = convert_text("loon-to-stash", source, path.stem)
                document = parse_stash(converted)
                self.assertTrue(document.metadata["name"])

    def test_all_stash_overrides_convert_to_parseable_loon(self) -> None:
        for path in sorted((ROOT / "stash").glob("*.stoverride")):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                converted = convert_text("stash-to-loon", source, path.stem)
                document = parse_loon(converted)
                self.assertTrue(document.metadata["name"])

    def test_cmb_round_trip_keeps_rule_script_and_mitm(self) -> None:
        path = ROOT / "plugins" / "cmb-startup-ad.lpx"
        converted = convert_text(
            "loon-to-stash", path.read_text(encoding="utf-8"), path.stem
        )
        document = parse_stash(converted)

        self.assertEqual(document.rules, ["URL-REGEX,^https:\\/\\/s3gw\\.cmbimg\\.cn\\/lr4504-mbappinitads-prd-1255000108\\/,REJECT"])
        self.assertEqual(document.mitm, ["webappcfg.paas.cmbchina.com", "s3gw.cmbimg.cn"])
        self.assertEqual(len(document.scripts), 1)
        self.assertTrue(document.scripts[0].require_body)
        self.assertEqual(document.scripts[0].timeout, 10)
        self.assertIn("cmb-startup-ad.js", document.scripts[0].url or "")

    def test_bilibili_stash_has_expected_rewrites(self) -> None:
        path = ROOT / "stash" / "bilibili-ios-ads.stoverride"
        document = parse_stash(path.read_text(encoding="utf-8"))

        self.assertEqual(document.metadata["name"], "Bilibili iOS 去广告")
        self.assertEqual(document.mitm, ["app.bilibili.com"])
        self.assertEqual(len(document.body_rewrite), 5)
        self.assertTrue(
            any("/x/resource/show/tab/v2" in rule for rule in document.body_rewrite)
        )


if __name__ == "__main__":
    unittest.main()
