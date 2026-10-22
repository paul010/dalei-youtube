import copy
import subprocess
import unittest
from unittest.mock import patch
from sync_youtube import CHANNEL_ID, duration_label, fetch_public_video, parse_playlist_fallback, parse_sources, update_episode_metadata, update_readme


class SyncTests(unittest.TestCase):
    def sources(self):
        entries = []
        items = []
        for i in range(7):
            video_id = f"abcdefghij{i}"
            entries.append(f'<entry><yt:videoId>{video_id}</yt:videoId><title>节目 {i}</title><published>2026-01-0{i+1}T22:00:00+00:00</published></entry>')
            items.append({"id": video_id, "title": f"当前标题 {i}", "duration": 438})
        xml = f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><yt:channelId>{CHANNEL_ID[2:]}</yt:channelId>{"".join(entries)}</feed>'
        return xml, {"channel_id": CHANNEL_ID, "entries": items}

    def shorts(self, *video_ids):
        return {"id": CHANNEL_ID, "entries": [{"id": video_id} for video_id in video_ids]}

    def test_exact_dates_current_title_and_sort(self):
        xml, playlist = self.sources()
        rows, _ = parse_sources(xml, playlist, shorts_playlist=self.shorts())
        self.assertEqual(rows[0]["date"], "2026-01-08")
        self.assertEqual(rows[0]["title"], "当前标题 6")
        self.assertEqual(rows[0]["duration"], "7:18")

    def test_rss_short_gap_backfills_only_verified_prior_long_form(self):
        xml, playlist = self.sources()
        playlist["entries"].extend([
            {"id": "oldvideo001", "title": "旧短视频", "availability": "public"},
            {"id": "oldvideo002", "title": "旧正片", "availability": "public"},
        ])
        previous = {"videos": [
            {"id": "oldvideo001", "title": "旧短视频", "publishedAt": "2025-12-02T00:00:00+00:00",
             "date": "2025-12-02", "duration": "0:32"},
            {"id": "oldvideo002", "title": "旧正片", "publishedAt": "2025-12-01T00:00:00+00:00",
             "date": "2025-12-01", "duration": "8:00"},
        ]}
        rows, _ = parse_sources(xml, playlist, shorts_playlist=self.shorts("oldvideo001"), previous_feed=previous)
        self.assertIn("oldvideo002", [video["id"] for video in rows])
        self.assertNotIn("oldvideo001", [video["id"] for video in rows])

    def test_members_excluded(self):
        xml, playlist = self.sources()
        playlist["entries"][-1]["availability"] = "subscriber_only"
        rows, _ = parse_sources(xml, playlist, shorts_playlist=self.shorts())
        self.assertEqual(len(rows), 6)
        self.assertNotIn("abcdefghij6", [v["id"] for v in rows])

    def test_wrong_channel_rejected(self):
        xml, playlist = self.sources()
        playlist["channel_id"] = "other"
        with self.assertRaises(ValueError):
            parse_sources(xml, playlist, shorts_playlist=self.shorts())

    def test_missing_video_rejected(self):
        xml, playlist = self.sources()
        playlist["entries"].pop()
        with self.assertRaises(ValueError):
            parse_sources(xml, playlist, shorts_playlist=self.shorts())

    def test_public_video_missing_from_videos_tab_is_directly_verified(self):
        xml, playlist = self.sources()
        playlist["entries"].pop()
        direct = {"id": "abcdefghij6", "title": "Public upload in RSS", "duration": 438,
                  "channel_id": CHANNEL_ID, "availability": "public", "live_status": "not_live"}
        rows, _ = parse_sources(xml, playlist, lambda video_id: direct, self.shorts())
        self.assertIn("abcdefghij6", [video["id"] for video in rows])
        self.assertEqual(next(video for video in rows if video["id"] == "abcdefghij6")["duration"], "7:18")

    def test_missing_restricted_video_is_excluded(self):
        xml, playlist = self.sources()
        playlist["entries"].pop()
        direct = {"id": "abcdefghij6", "channel_id": CHANNEL_ID,
                  "availability": "subscriber_only", "live_status": "not_live"}
        rows, _ = parse_sources(xml, playlist, lambda video_id: direct, self.shorts())
        self.assertNotIn("abcdefghij6", [video["id"] for video in rows])

    def test_missing_video_from_wrong_channel_is_rejected(self):
        xml, playlist = self.sources()
        playlist["entries"].pop()
        direct = {"id": "abcdefghij6", "channel_id": "other", "availability": "public",
                  "live_status": "not_live", "title": "Wrong channel", "duration": 32}
        with self.assertRaises(ValueError):
            parse_sources(xml, playlist, lambda video_id: direct, self.shorts())

    def test_missing_duration_rejected(self):
        xml, playlist = self.sources()
        playlist["entries"][0]["duration"] = None
        with self.assertRaises(ValueError):
            parse_sources(xml, playlist, shorts_playlist=self.shorts())

    def test_members_only_video_is_classified_and_skipped(self):
        error = subprocess.CalledProcessError(
            1, ["yt-dlp"], stderr="ERROR: Join this channel to get access to members-only content"
        )
        with patch("sync_youtube.command", side_effect=error):
            item = fetch_public_video("members0001")
        self.assertEqual(item["availability"], "subscriber_only")
        self.assertEqual(item["channel_id"], CHANNEL_ID)

    def test_duplicates_rejected(self):
        xml, playlist = self.sources()
        xml = xml.replace("abcdefghij0", "abcdefghij1")
        with self.assertRaises(ValueError):
            parse_sources(xml, playlist, shorts_playlist=self.shorts())

    def test_archive_insertion_is_idempotent(self):
        original = "## 📺 视频索引\n\n### 2025\n\n#### 12月\n| 日期 | 标题 | 时长 |\n|------|------|------|\n| 12-01 | [旧内容](episodes/2025-12/oldoldold12.md) | 1:00 |\n\n---\nFooter\n"
        xml, playlist = self.sources()
        rows, _ = parse_sources(xml, playlist, shorts_playlist=self.shorts())
        updated = update_readme(original, rows)
        self.assertEqual(update_readme(updated, rows), updated)
        self.assertIn("[旧内容](episodes/2025-12/oldoldold12.md)", updated)
        self.assertLess(updated.index("abcdefghij6.md"), updated.index("abcdefghij0.md"))
        self.assertEqual(updated.count("### 2026"), 1)
        self.assertTrue(updated.endswith("Footer\n"))

    def test_existing_index_row_refreshes_metadata_in_place(self):
        xml, playlist = self.sources()
        rows, _ = parse_sources(xml, playlist, shorts_playlist=self.shorts())
        original = "## 📺 视频索引\n\n### 2026\n\n#### 1月\n| 日期 | 标题 | 时长 |\n|------|------|------|\n| 01-07 | [人工笔记](episodes/2026-01/abcdefghij6.md) | 7:18 |\n"
        updated = update_readme(original, rows[:1])
        self.assertIn("| 01-08 | [当前标题 6](episodes/2026-01/abcdefghij6.md) | 7:18 |", updated)
        self.assertNotIn("[人工笔记]", updated)
        self.assertEqual(update_readme(updated, rows[:1]), updated)

    def test_episode_metadata_refresh_preserves_editorial_body(self):
        video = {"title": "当前标题", "date": "2026-01-08", "duration": "7:18"}
        original = (
            "# 旧标题\n\n"
            "> **发布日期（北京时间）**：2026-01-07 | **时长**：7:00\n\n"
            "## 人工笔记\n\n保留这一段人工整理的内容。\n"
        )
        updated = update_episode_metadata(original, video)
        self.assertTrue(updated.startswith("# 当前标题\n\n"))
        self.assertIn("> **发布日期（北京时间）**：2026-01-08 | **时长**：7:18\n\n", updated)
        self.assertTrue(updated.endswith("## 人工笔记\n\n保留这一段人工整理的内容。\n"))

    def test_duration(self):
        self.assertEqual(duration_label(3601), "1:00:01")

    def test_short_ids_excluded_even_when_present_in_videos_tab(self):
        xml, playlist = self.sources()
        rows, _ = parse_sources(xml, playlist, shorts_playlist=self.shorts("abcdefghij6"))
        self.assertNotIn("abcdefghij6", [video["id"] for video in rows])
        self.assertEqual(len(rows), 6)

    def test_short_ids_are_skipped_before_direct_metadata_lookup(self):
        xml, playlist = self.sources()
        playlist["entries"].pop()

        def should_not_fetch(_video_id):
            raise AssertionError("Shorts should be filtered before metadata lookup")

        rows, _ = parse_sources(xml, playlist, should_not_fetch, self.shorts("abcdefghij6"))
        self.assertNotIn("abcdefghij6", [video["id"] for video in rows])

    def test_unavailable_shorts_source_fails_closed(self):
        xml, playlist = self.sources()
        with self.assertRaises(ValueError):
            parse_sources(xml, playlist)

    def test_playlist_fallback_merges_verified_public_uploads_and_excludes_shorts(self):
        previous = {"videos": [
            {"id": f"oldvideo{i:03d}", "title": f"旧节目 {i}",
             "publishedAt": "2026-10-10T10:00:00+00:00",
             "date": "2026-10-10", "duration": "7:18",
             "url": f"https://www.youtube.com/watch?v=oldvideo{i:03d}"}
            for i in range(14)
        ]}
        playlist = {"id": CHANNEL_ID, "channel_id": CHANNEL_ID, "entries": [
            {"id": "newvideo001"}, {"id": "shortvid001"}, {"id": "newvideo002"},
            {"id": "newvideo003"}, *[{"id": f"oldvideo{i:03d}"} for i in range(12)],
        ]}
        shorts = {"id": CHANNEL_ID, "channel_id": CHANNEL_ID,
                  "entries": [{"id": "shortvid001"}]}
        metadata = {
            "newvideo001": {"id": "newvideo001", "channel_id": CHANNEL_ID, "availability": "public",
                            "live_status": "not_live", "title": "最新节目 1", "duration": 550,
                            "timestamp": 1791892260, "description": "节目简介\n\n第二段"},
            "newvideo002": {"id": "newvideo002", "channel_id": CHANNEL_ID, "availability": "public",
                            "live_status": "not_live", "title": "最新节目 2", "duration": 618,
                            "timestamp": 1791843570, "description": "另一个简介"},
            "newvideo003": {"id": "newvideo003", "channel_id": CHANNEL_ID, "availability": "public",
                            "live_status": "not_live", "title": "最新节目 3", "duration": 535,
                            "timestamp": 1791802812, "description": "第三个简介"},
        }
        calls = []
        rows, excerpts = parse_playlist_fallback(
            playlist, shorts, previous,
            lambda video_id: calls.append(video_id) or metadata[video_id],
        )
        self.assertEqual(len(rows), 15)
        self.assertEqual([v["id"] for v in rows[:3]], ["newvideo001", "newvideo002", "newvideo003"])
        self.assertNotIn("shortvid001", [v["id"] for v in rows])
        self.assertEqual(calls, ["newvideo001", "newvideo002", "newvideo003"])
        self.assertEqual(rows[0]["date"], "2026-10-13")
        self.assertEqual(excerpts["newvideo001"], "节目简介")

    def test_playlist_fallback_rejects_incomplete_shorts_or_wrong_channel(self):
        playlist = {"id": CHANNEL_ID, "channel_id": CHANNEL_ID, "entries": []}
        with self.assertRaises(ValueError):
            parse_playlist_fallback(playlist, None, {"videos": []}, lambda _: {})
        wrong = {"id": "other", "channel_id": "other", "entries": []}
        with self.assertRaises(ValueError):
            parse_playlist_fallback(wrong, self.shorts(), {"videos": []}, lambda _: {})


if __name__ == "__main__":
    unittest.main()
