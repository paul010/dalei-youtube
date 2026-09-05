import copy
import unittest
from sync_youtube import CHANNEL_ID, duration_label, parse_sources, update_readme


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

    def test_exact_dates_current_title_and_sort(self):
        rows, _ = parse_sources(*self.sources())
        self.assertEqual(rows[0]["date"], "2026-01-08")
        self.assertEqual(rows[0]["title"], "当前标题 6")
        self.assertEqual(rows[0]["duration"], "7:18")

    def test_members_excluded(self):
        xml, playlist = self.sources()
        playlist["entries"][-1]["availability"] = "subscriber_only"
        rows, _ = parse_sources(xml, playlist)
        self.assertEqual(len(rows), 6)
        self.assertNotIn("abcdefghij6", [v["id"] for v in rows])

    def test_wrong_channel_rejected(self):
        xml, playlist = self.sources()
        playlist["channel_id"] = "other"
        with self.assertRaises(ValueError):
            parse_sources(xml, playlist)

    def test_missing_video_rejected(self):
        xml, playlist = self.sources()
        playlist["entries"].pop()
        with self.assertRaises(ValueError):
            parse_sources(xml, playlist)

    def test_missing_duration_rejected(self):
        xml, playlist = self.sources()
        playlist["entries"][0]["duration"] = None
        with self.assertRaises(ValueError):
            parse_sources(xml, playlist)

    def test_duplicates_rejected(self):
        xml, playlist = self.sources()
        xml = xml.replace("abcdefghij0", "abcdefghij1")
        with self.assertRaises(ValueError):
            parse_sources(xml, playlist)

    def test_archive_insertion_is_idempotent(self):
        original = "## 📺 视频索引\n\n### 2025\n\n#### 12月\n| 日期 | 标题 | 时长 |\n|------|------|------|\n| 12-01 | [旧内容](episodes/2025-12/oldoldold12.md) | 1:00 |\n\n---\nFooter\n"
        rows, _ = parse_sources(*self.sources())
        updated = update_readme(original, rows)
        self.assertEqual(update_readme(updated, rows), updated)
        self.assertIn("[旧内容](episodes/2025-12/oldoldold12.md)", updated)
        self.assertLess(updated.index("abcdefghij6.md"), updated.index("abcdefghij0.md"))
        self.assertEqual(updated.count("### 2026"), 1)
        self.assertTrue(updated.endswith("Footer\n"))

    def test_existing_note_with_utc_date_not_duplicated(self):
        rows, _ = parse_sources(*self.sources())
        original = "## 📺 视频索引\n\n### 2026\n\n#### 1月\n| 日期 | 标题 | 时长 |\n|------|------|------|\n| 01-07 | [人工笔记](episodes/2026-01/abcdefghij6.md) | 7:18 |\n"
        self.assertEqual(update_readme(original, rows[:1]), original)

    def test_duration(self):
        self.assertEqual(duration_label(3601), "1:00:01")


if __name__ == "__main__":
    unittest.main()
