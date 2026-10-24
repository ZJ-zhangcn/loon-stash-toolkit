from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.convert import (  # noqa: E402
    BODY_REWRITE_MAX_CHARS,
    _flatten_jq,
    _split_oversized_del,
    convert_text,
    parse_loon,
    parse_stash,
)


class ConversionTests(unittest.TestCase):
    def test_loon_redirect_action_maps_to_stash_url_rewrite(self) -> None:
        source = r"""#!name=Redirect
[Rewrite]
request if ${url} ~= /^https?:\/\/(www.)?(g|google)\.cn/i then redirect(307, "https://www.google.com")
"""
        converted = convert_text("loon-to-stash", source, "redirect")
        document = parse_stash(converted)
        self.assertEqual(
            document.url_rewrite,
            [r"^https?:\/\/(www.)?(g|google)\.cn https://www.google.com 307"],
        )

    def test_loon_redirect_capture_variable_maps_to_stash_groups(self) -> None:
        source = r'''#!name=Redirect
[Rewrite]
request if ${url} ~= /^https:\/\/example\.com\/go\/(first)\/(second)$/i as urlMatch then redirect(307, "${urlMatch.2}")
'''
        converted = convert_text("loon-to-stash", source, "redirect")
        document = parse_stash(converted)

        self.assertEqual(
            document.url_rewrite,
            [r"^https:\/\/example\.com\/go\/(first)\/(second)$ $2 307"],
        )

    def test_loon_response_text_mock_replaces_the_entire_body(self) -> None:
        source = r'''#!name=Mock
[Rewrite]
response if ${url} ~= /^https:\/\/api\.example\/ads$/i then response.body.mock("text", "")
'''
        warnings: list[str] = []
        converted = convert_text("loon-to-stash", source, "mock", warnings)
        document = parse_stash(converted)

        self.assertEqual(len(document.mocks), 1)
        self.assertEqual(document.mocks[0]["match"], r"^https:\/\/api\.example\/ads$")
        self.assertEqual(document.mocks[0]["text"], "")
        self.assertEqual(int(document.mocks[0]["status-code"]), 200)
        self.assertEqual(document.body_rewrite, [])
        self.assertEqual(warnings, [])

    def test_modern_loon_rewrites_convert_to_stash_reject_and_jq_rules(self) -> None:
        source = r"""#!name=Example
#!tag=去广告
[Rewrite]
request if ${url} ~= /^https:\/\/ads\.example\/v\d+/i then reject_dict(200)
request if ${url} ~= /^https:\/\/ads\.example\/legacy/i then reject(404)
response if ${url} ~= /^https:\/\/api\.example\/feed$/i then response.json.delete(["data.ad", "data.items[0]", "result.quark-countdown-2025", "data.920bestwishes"])
response if ${url} ~= /^https:\/\/api\.example\/search$/i then response.json.replace(["data.queries", "data.hint_word", "data.count"], ["[]", "{}", 0])
response if ${url} ~= /^https:\/\/api\.example\/list$/i then response.json.jq(".data.items |= map(select(.kind == \"news\"))")
^https?:\/\/images\.example\/ad\.gif reject-img
"""
        warnings: list[str] = []
        converted = convert_text("loon-to-stash", source, "example", warnings)
        document = parse_stash(converted)

        self.assertEqual(
            document.url_rewrite,
            [
                r"^https:\/\/ads\.example\/v\d+ - reject-dict",
                r"^https:\/\/ads\.example\/legacy - reject",
                r"^https?:\/\/images\.example\/ad\.gif - reject-img",
            ],
        )
        self.assertEqual(
            document.body_rewrite,
            [
                r'^https:\/\/api\.example\/feed$ response-jq del(.data.ad, .data.items[0], .result."quark-countdown-2025", .data."920bestwishes")',
                r"^https:\/\/api\.example\/search$ response-jq (.data.queries = []) | (.data.hint_word = {}) | (.data.count = 0)",
                r'^https:\/\/api\.example\/list$ response-jq .data.items |= map(select(.kind == "news"))',
            ],
        )
        self.assertEqual(warnings, [])

    def test_modern_loon_scripts_use_argument_values_and_mirror_urls(self) -> None:
        source = r"""#!name=Example
[Argument]
 blockUpload=switch, false, true, tag=Upload
 captionLang=select, "zh-Hans", "en", tag=Caption
[Script]
 response if ${url} ~= /^https:\/\/api\.example\/v1/i then script("https://kelee.one/Resource/JavaScript/example.js", {${blockUpload}, ${captionLang}}) with tag="Example response", requires_body=true, binary_body_mode=true
 request if ${url} ~= /^https:\/\/api\.example\/init/i then script("https://kelee.one/Resource/JavaScript/example-request.js", {${captionLang}}) with tag="Example request", requires_body=true
[MitM]
hostname = api.example.com
"""
        warnings: list[str] = []
        converted = convert_text(
            "loon-to-stash",
            source,
            "example",
            warnings,
            argument_values={"blockUpload": True},
        )
        document = parse_stash(converted)

        self.assertEqual(len(document.scripts), 2)
        self.assertEqual(document.scripts[0].match, r"^https:\/\/api\.example\/v1")
        self.assertEqual(document.scripts[0].type, "response")
        self.assertTrue(document.scripts[0].require_body)
        self.assertTrue(document.scripts[0].binary_mode)
        self.assertEqual(
            document.scripts[0].url,
            "https://git.repcz.link/kelee.one/Resource/JavaScript/example.js",
        )
        self.assertEqual(
            json.loads(document.scripts[0].argument),
            {"blockUpload": True, "captionLang": "zh-Hans"},
        )
        self.assertEqual(
            json.loads(document.scripts[1].argument), {"captionLang": "zh-Hans"}
        )
        self.assertEqual(document.mitm, ["api.example.com"])
        self.assertEqual(warnings, [])

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

    def test_repository_contains_all_mapped_overrides_and_bilibili(self) -> None:
        mappings = json.loads(
            (ROOT / "sources" / "loon-plugins.json").read_text(encoding="utf-8")
        )
        expected = {item["output"] for item in mappings}
        expected.add("Bilibili_remove_ads.stoverride")
        expected.add("Kelee_All.stoverride")
        actual = {path.name for path in (ROOT / "stash").glob("*.stoverride")}

        self.assertEqual(len(mappings), 32)
        self.assertEqual(
            sum("kelee.one" in item["source"] for item in mappings), 31
        )
        meituan = next(
            item
            for item in mappings
            if item["output"] == "Meituan-MeituanWaimai.stoverride"
        )
        self.assertEqual(
            meituan["source"],
            "https://github.com/fmz200/wool_scripts/raw/main/Loon/plugin/split/partM/Meituan-MeituanWaimai.lpx",
        )
        self.assertEqual(actual, expected)
        for removed in (
            "AppleWeatherEnhancer.stoverride",
            "JD_Price.stoverride",
            "Weibo_remove_ads.stoverride",
        ):
            self.assertNotIn(removed, actual)
        self.assertEqual(list((ROOT / "plugins").glob("*.lpx")), [])
        self.assertEqual(
            {path.name for path in (ROOT / "scripts").glob("*.js")},
            {"cmb-startup-ad.js", "freshippo-splash.js"},
        )

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

    def test_all_mapped_overrides_have_complete_metadata(self) -> None:
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
        }
        for item in mappings:
            path = ROOT / "stash" / item["output"]
            with self.subTest(path=path.name):
                metadata = parse_stash(path.read_text(encoding="utf-8")).metadata
                self.assertEqual(metadata["name"], item["name"])
                item_required = required
                if "kelee.one" in item["source"]:
                    item_required = required | {"date"}
                self.assertTrue(item_required.issubset(metadata))
                self.assertTrue(all(metadata[key].strip() for key in item_required))
                self.assertTrue(metadata["icon"].startswith("https://"))

    def test_aggregate_has_no_whitespace_only_lines(self) -> None:
        aggregate = (ROOT / "stash" / "Kelee_All.stoverride").read_text(
            encoding="utf-8"
        )
        self.assertFalse(
            [line for line in aggregate.splitlines() if line and not line.strip()]
        )

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
        self.assertEqual(aggregate.metadata["name"], "Loon 覆写合集")

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
                actual = getattr(aggregate, field)
                if field == "body_rewrite":
                    # YAML folded scalars may preserve extra indentation as newlines.
                    actual = [" ".join(value.split()) for value in actual]
                    expected = [" ".join(value.split()) for value in expected]
                if field in ("header_rewrite", "body_rewrite"):
                    # 同一文件内 header 操作可能落在两个 rewrite 段，跨文件合并后顺序不可依赖
                    self.assertEqual(sorted(actual), sorted(expected))
                    continue
                self.assertEqual(actual, expected)

        # provider 名在各文件间可能重名（同一脚本被多条规则引用），按 URL 校验内容完整
        expected_urls = sorted(
            provider["url"]
            for doc in source_docs
            for provider in doc.script_providers.values()
            if "url" in provider
        )
        actual_urls = sorted(
            provider["url"]
            for provider in aggregate.script_providers.values()
            if "url" in provider
        )
        self.assertEqual(actual_urls, expected_urls)
        self.assertEqual(
            aggregate.warnings,
            [warning for doc in source_docs for warning in doc.warnings],
        )
        aggregate_mocks = {(m["match"], m.get("text"), m.get("base64")) for m in aggregate.mocks}
        for path in paths:
            for mock in parse_stash(path.read_text(encoding="utf-8")).mocks:
                key = (mock["match"], mock.get("text"), mock.get("base64"))
                self.assertIn(key, aggregate_mocks)

    def test_youtube_override_uses_previous_argument_values(self) -> None:
        document = parse_stash(
            (ROOT / "stash" / "YouTube_remove_ads.stoverride").read_text(
                encoding="utf-8"
            )
        )
        expected = {
            "blockUpload": True,
            "blockShorts": True,
            "blockImmersive": True,
            "captionLang": "zh-Hans",
            "debug": False,
        }
        self.assertEqual(len(document.scripts), 3)
        self.assertEqual(json.loads(document.scripts[0].argument or "{}"), expected)
        self.assertEqual(
            json.loads(document.scripts[1].argument or "{}"),
            {"captionLang": "zh-Hans"},
        )
        self.assertEqual(json.loads(document.scripts[2].argument or "{}"), expected)

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


    def test_script_provider_payload_block_scalar_round_trip(self) -> None:
        body = "let a = 1;\n  indented();\n*star\n\t\ttabbed\n"
        text = (
            "name: t\n"
            "http:\n"
            "  script:\n"
            "    - match: '^https://a/' \n"
            "      name: s1\n"
            "      type: response\n"
            "script-providers:\n"
            '  s1:\n'
            '    url: https://example.com/a.js\n'
            "    interval: 86400\n"
            "    payload: |-\n"
            + "".join(f"      {line}\n" for line in body.splitlines())
        )
        document = parse_stash(text)
        self.assertEqual(document.script_providers["s1"]["payload"], body.rstrip("\n"))
        self.assertEqual(document.script_providers["s1"]["interval"], "86400")


class OversizedBodyRewriteTests(unittest.TestCase):
    """超长 del() 必须切分：Stash 遇到 27 KB 单行会让整层覆写脚本全部失效。"""

    def test_splits_long_del_and_keeps_every_path(self) -> None:
        paths = [f".result.key_{index:04d}_padding" for index in range(800)]
        entry = f"^https://example\\.com\\/api\\? response-jq del({', '.join(paths)})"
        self.assertGreater(len(entry), BODY_REWRITE_MAX_CHARS)

        chunks = _split_oversized_del(entry)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), BODY_REWRITE_MAX_CHARS)
            self.assertTrue(chunk.startswith("^https://example\\.com\\/api\\? response-jq del("))
        recovered = [
            path.strip()
            for chunk in chunks
            for path in chunk.split("response-jq del(", 1)[1][:-1].split(", ")
        ]
        self.assertEqual(recovered, paths)

    def test_leaves_nested_paths_alone(self) -> None:
        # 同时删父级和子级时拆分可能改变语义，必须整体保留
        paths = [".result"] + [f".result.child_{index:04d}_pad" for index in range(800)]
        entry = f"^https://example\\.com\\/ response-jq del({', '.join(paths)})"
        self.assertGreater(len(entry), BODY_REWRITE_MAX_CHARS)
        self.assertEqual(_split_oversized_del(entry), [entry])

    def test_leaves_short_and_non_del_programs_alone(self) -> None:
        short = "^https://example\\.com\\/ response-jq del(.a, .b)"
        self.assertEqual(_split_oversized_del(short), [short])
        long_other = "^https://example\\.com\\/ response-jq " + ".a = 1 | " * 900
        self.assertEqual(_split_oversized_del(long_other), [long_other])


class JqCommentTests(unittest.TestCase):
    """压平多行 jq 前必须剥掉 # 注释，否则第一条注释会吃掉后面整个程序。"""

    def test_strips_inline_comments_before_flattening(self) -> None:
        source = 'IN(\n  "a",   # 说明一\n  "b",   # 说明二\n  "c"\n)'
        flat = _flatten_jq(source)
        self.assertNotIn("#", flat)
        self.assertEqual(flat, 'IN( "a", "b", "c" )')

    def test_keeps_hash_inside_strings(self) -> None:
        source = '.x = "a#b"  # 注释'
        self.assertEqual(_flatten_jq(source), '.x = "a#b"')

    def test_escaped_quote_does_not_end_string(self) -> None:
        source = '.x = "a\\"#b"  # 注释'
        self.assertIn('"a\\"#b"', _flatten_jq(source))


class RuleTypeTests(unittest.TestCase):
    """未知规则类型不能自动转换：跳过并留 warning，由人工确认。"""

    def test_unknown_rule_type_is_skipped_with_warning(self) -> None:
        source = (
            "#!name=测试\n#!desc=x\n#!author=a\n#!homepage=https://x\n"
            "#!icon=https://x/i.png\n#!category=测试\n"
            "[Rule]\n"
            "DOMAIN, a.com, DIRECT\n"
            "MAGIC-RULE, b.com, DIRECT\n"
            "IP-CIDR, 1.2.3.0/24, REJECT\n"
        )
        document = parse_loon(source)
        self.assertEqual(
            document.rules, ["DOMAIN,a.com,DIRECT", "IP-CIDR,1.2.3.0/24,REJECT"]
        )
        self.assertTrue(any("MAGIC-RULE" in w for w in document.warnings), document.warnings)

    def test_known_rule_types_survive(self) -> None:
        for rule_type in ("DOMAIN", "DOMAIN-SUFFIX", "IP-CIDR6", "URL-REGEX", "AND"):
            document = parse_loon(
                "#!name=测试\n#!desc=x\n#!author=a\n#!homepage=https://x\n"
                f"#!icon=https://x/i.png\n#!category=测试\n[Rule]\n{rule_type}, v, DIRECT\n"
            )
            self.assertEqual(document.warnings, [], rule_type)
            self.assertEqual(len(document.rules), 1, rule_type)


    def test_rewrite_line_inside_rule_section_becomes_url_rewrite(self) -> None:
        """斗鱼插件把重定向行写在了 [Rule] 段里，且 Loon 的状态码位置与 Stash 相反。"""
        document = parse_loon(
            "#!name=测试\n#!desc=x\n#!author=a\n#!homepage=https://x\n"
            "#!icon=https://x/i.png\n#!category=测试\n"
            "[Rule]\n"
            "^(http:\\/\\/)(\\d+)\\/(host)(.*) 307 $1$4$5\n"
            "DOMAIN, a.com, DIRECT\n"
        )
        self.assertEqual(
            document.url_rewrite, ["^(http:\\/\\/)(\\d+)\\/(host)(.*) $1$4$5 307"]
        )
        self.assertEqual(document.rules, ["DOMAIN,a.com,DIRECT"])
        self.assertEqual(document.warnings, [])

    def test_loon_spellings_are_normalised(self) -> None:
        document = parse_loon(
            "#!name=测试\n#!desc=x\n#!author=a\n#!homepage=https://x\n"
            "#!icon=https://x/i.png\n#!category=测试\n"
            "[Rule]\n"
            'URL-REGEX, "^http:\\/\\/a\\.com", REJECT\n'
            "AND, ((DEST-PORT, 18000), (PROTOCOL, STUN)), REJECT\n"
        )
        self.assertEqual(
            document.rules,
            [
                "URL-REGEX,^http:\\/\\/a\\.com,REJECT",
                "AND,((DST-PORT,18000),(PROTOCOL,STUN)),REJECT",
            ],
        )


if __name__ == "__main__":
    unittest.main()