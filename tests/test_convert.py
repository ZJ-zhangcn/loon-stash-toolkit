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

    def test_youtube_stash_uses_protobuf_response_cleaner(self) -> None:
        path = ROOT / "stash" / "YouTube_remove_ads.stoverride"
        document = parse_stash(path.read_text(encoding="utf-8"))
        scripts_by_name = {script.name: script for script in document.scripts}

        self.assertIn("youtube_feed_cleaner_v3", scripts_by_name)
        self.assertIn(
            "(browse|next|search)",
            scripts_by_name["youtube_feed_cleaner_v3"].match,
        )
        self.assertIn("youtube_response_v2", scripts_by_name)
        self.assertIn(
            "(player|reel\\/reel_watch_sequence",
            scripts_by_name["youtube_response_v2"].match,
        )
        self.assertIn(
            "youtube-remove-ads-feed.js?v=20260930c",
            document.script_providers["youtube_feed_cleaner_v3"]["url"],
        )
        self.assertIn(
            "YouTube_remove_ads_response.js?v=20260930d",
            document.script_providers["youtube_response_v2"]["url"],
        )
        response_script = scripts_by_name["youtube_response_v2"]
        self.assertIn('"blockUpload":true', response_script.argument or "")
        self.assertIn('"blockShorts":true', response_script.argument or "")
        self.assertNotIn("youtube_remove_ads_response", document.script_providers)
        self.assertNotIn("youtube_support_response", document.script_providers)


if __name__ == "__main__":
    unittest.main()
