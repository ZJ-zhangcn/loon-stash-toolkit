from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.convert import convert_text, parse_loon, parse_stash  # noqa: E402


class ConversionTests(unittest.TestCase):
    def test_loon_tag_maps_to_stash_category_and_round_trips(self) -> None:
        source = (
            "#!name=Example\n"
            "#!tag=去广告\n"
            "#!icon=https://example.com/icon.png\n\n"
            "[Rule]\n"
            "DOMAIN,example.com,REJECT\n"
        )
        converted = convert_text("loon-to-stash", source, "example")
        stash_document = parse_stash(converted)
        self.assertEqual(stash_document.metadata["category"], "去广告")
        self.assertEqual(
            stash_document.metadata["icon"], "https://example.com/icon.png"
        )

        loon_document = parse_loon(
            convert_text("stash-to-loon", converted, "example")
        )
        self.assertEqual(loon_document.metadata["tag"], "去广告")

    def test_repository_keeps_only_kelee_overrides_and_bilibili(self) -> None:
        mappings = json.loads(
            (ROOT / "sources" / "loon-plugins.json").read_text(encoding="utf-8")
        )
        expected = {item["output"] for item in mappings}
        expected.add("Bilibili_remove_ads.stoverride")
        expected.add("Kelee_All.stoverride")
        actual = {path.name for path in (ROOT / "stash").glob("*.stoverride")}

        self.assertTrue(all("kelee.one" in item["source"] for item in mappings))
        self.assertEqual(len(mappings), 21)
        self.assertEqual(actual, expected)
        for removed in (
            "AppleWeatherEnhancer.stoverride",
            "JD_Price.stoverride",
            "Weibo_remove_ads.stoverride",
        ):
            self.assertNotIn(removed, actual)
        self.assertEqual(list((ROOT / "plugins").glob("*.lpx")), [])
        self.assertEqual(list((ROOT / "scripts").glob("*.js")), [])

    def test_priority_overrides_are_first_in_the_aggregate(self) -> None:
        mappings = json.loads(
            (ROOT / "sources" / "loon-plugins.json").read_text(encoding="utf-8")
        )
        priority = [
            "BlockAdvertisers.stoverride",
            "Block_HTTPDNS.stoverride",
        ]
        self.assertEqual([item["output"] for item in mappings[:2]], priority)

        priority_rules = []
        for name in priority:
            document = parse_stash((ROOT / "stash" / name).read_text(encoding="utf-8"))
            priority_rules.extend(document.rules)
        aggregate = parse_stash(
            (ROOT / "stash" / "Kelee_All.stoverride").read_text(encoding="utf-8")
        )
        self.assertEqual(aggregate.rules[: len(priority_rules)], priority_rules)

    def test_all_kelee_overrides_have_complete_metadata(self) -> None:
        mappings = json.loads(
            (ROOT / "sources" / "loon-plugins.json").read_text(encoding="utf-8")
        )
        required = {
            "name",
            "desc",
            "author",
            "homepage",
            "icon",
            "category",
            "date",
        }
        for item in mappings:
            path = ROOT / "stash" / item["output"]
            with self.subTest(path=path.name):
                metadata = parse_stash(path.read_text(encoding="utf-8")).metadata
                self.assertEqual(metadata["name"], item["name"])
                self.assertTrue(required.issubset(metadata))
                self.assertTrue(all(metadata[key].strip() for key in required))
                self.assertTrue(metadata["icon"].startswith("https://"))

    def test_aggregate_override_preserves_all_individual_content(self) -> None:
        mappings = json.loads(
            (ROOT / "sources" / "loon-plugins.json").read_text(encoding="utf-8")
        )
        paths = [ROOT / "stash" / item["output"] for item in mappings]
        paths.append(ROOT / "stash" / "Bilibili_remove_ads.stoverride")
        aggregate_path = ROOT / "stash" / "Kelee_All.stoverride"
        self.assertTrue(aggregate_path.is_file())

        source_docs = [parse_stash(path.read_text(encoding="utf-8")) for path in paths]
        aggregate_text = aggregate_path.read_text(encoding="utf-8")
        aggregate = parse_stash(aggregate_text)
        self.assertEqual(aggregate.metadata["name"], "Kelee 覆写合集")

        for field in (
            "rules",
            "mitm",
            "url_rewrite",
            "header_rewrite",
            "body_rewrite",
            "scripts",
        ):
            with self.subTest(field=field):
                expected = [value for doc in source_docs for value in getattr(doc, field)]
                self.assertEqual(getattr(aggregate, field), expected)

        expected_providers = {
            name: provider
            for doc in source_docs
            for name, provider in doc.script_providers.items()
        }
        self.assertEqual(aggregate.script_providers, expected_providers)
        self.assertEqual(
            aggregate.warnings,
            [warning for doc in source_docs for warning in doc.warnings],
        )
        for path in paths:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.lstrip().startswith(("text:", "base64:")):
                    self.assertIn(line.strip(), aggregate_text)

    def test_all_stash_overrides_convert_to_parseable_loon(self) -> None:
        for path in sorted((ROOT / "stash").glob("*.stoverride")):
            with self.subTest(path=path.name):
                converted = convert_text(
                    "stash-to-loon",
                    path.read_text(encoding="utf-8"),
                    path.stem,
                )
                document = parse_loon(converted)
                self.assertTrue(document.metadata["name"])

    def test_bilibili_override_has_icon_and_category(self) -> None:
        path = ROOT / "stash" / "Bilibili_remove_ads.stoverride"
        document = parse_stash(path.read_text(encoding="utf-8"))
        self.assertEqual(document.metadata["category"], "去广告")
        self.assertTrue(document.metadata["icon"])

    def test_bilibili_override_block_scalars_survive_conversion(self) -> None:
        path = ROOT / "stash" / "Bilibili_remove_ads.stoverride"
        source = path.read_text(encoding="utf-8")
        document = parse_stash(source)
        self.assertTrue(
            any("response-jq" in rule and ".data.tab" in rule for rule in document.body_rewrite)
        )
        converted = convert_text("stash-to-loon", source, path.stem)
        self.assertIn("[Body Rewrite]", converted)

    def test_bilibili_override_hides_membership_shop_and_bottom_publish(self) -> None:
        path = ROOT / "stash" / "Bilibili_remove_ads.stoverride"
        document = parse_stash(path.read_text(encoding="utf-8"))

        mine_rule = next(
            rule
            for rule in document.body_rewrite
            if "account" in rule and ".data |= (" in rule
        )
        self.assertNotIn('"title": "会员购"', mine_rule)
        self.assertNotIn('"uri": "bilibili://mall/home"', mine_rule)

        bottom_rule = next(rule for rule in document.body_rewrite if ".id != 670" in rule)
        self.assertIn('(.name != "发布")', bottom_rule)
        self.assertIn('(.name != "会员购")', bottom_rule)
        self.assertIn('(.tab_id != "publish")', bottom_rule)
        self.assertIn('(.uri != "bilibili://mall/home")', bottom_rule)


if __name__ == "__main__":
    unittest.main()
