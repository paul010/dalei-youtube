#!/usr/bin/env python3
"""Sync public YouTube metadata, episode stubs and the homepage JSON feed.

Requires Python 3.9+, curl and yt-dlp. No credentials or video downloads.
Existing editorial episode notes are never overwritten. All sources are
validated before writing; failed fetches leave the published index intact.
"""
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CHANNEL_ID = "UCk9tu0mFtXj_rOEfIncxuJQ"
CHANNEL_URL = "https://www.youtube.com/@dalei2025/videos"
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "m": "http://search.yahoo.com/mrss/"}


def command(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120).stdout


def duration_label(seconds):
    seconds = round(seconds)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def markdown_title(title):
    return title.replace("|", "&#124;").replace("[", "&#91;").replace("]", "&#93;").replace("\n", " ")


def parse_sources(xml, playlist):
    root = ET.fromstring(xml)
    if root.findtext("yt:channelId", namespaces=NS) not in {CHANNEL_ID, CHANNEL_ID[2:]} or playlist.get("channel_id") != CHANNEL_ID:
        raise ValueError("Unexpected source channel")
    listed = {item["id"]: item for item in playlist["entries"]}
    videos, excerpts = [], {}
    now = datetime.now(timezone.utc)
    for entry in root.findall("a:entry", NS):
        video_id = entry.findtext("yt:videoId", namespaces=NS)
        if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("Invalid video ID")
        item = listed.get(video_id)
        if item is None:
            # Do not silently publish an incomplete feed on source disagreement.
            raise ValueError(f"RSS video missing from channel Videos tab: {video_id}")
        if item.get("availability") in {"subscriber_only", "premium_only", "private", "needs_auth"} or item.get("live_status") in {"is_live", "is_upcoming"}:
            continue
        published = entry.findtext("a:published", namespaces=NS)
        instant = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if instant > now:
            continue
        title = item.get("title", "").strip() or entry.findtext("a:title", namespaces=NS)
        duration = item.get("duration")
        if not title or not isinstance(duration, (int, float)) or duration <= 0:
            raise ValueError(f"Incomplete public video metadata: {video_id}")
        videos.append({"id": video_id, "title": title, "publishedAt": published,
                       "date": instant.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                       "duration": duration_label(duration), "url": f"https://www.youtube.com/watch?v={video_id}"})
        description = entry.findtext("m:group/m:description", default="", namespaces=NS)
        paragraphs = [p.strip() for p in description.split("\n\n") if p.strip()]
        excerpts[video_id] = next((p for p in paragraphs if "/join" not in p and "成为此频道的会员" not in p), "")
    videos.sort(key=lambda video: video["publishedAt"], reverse=True)
    if len(videos) < 6 or len({v["id"] for v in videos}) != len(videos):
        raise ValueError("Too few public videos, or duplicate IDs")
    return videos, excerpts


def update_readme(readme, videos):
    # Preserve every existing archive row. Add only missing videos in place.
    for video in reversed(videos):
        if re.search(r"episodes/\d{4}-\d{2}/" + re.escape(video["id"]) + r"\.md", readme):
            continue
        year, month, _ = video["date"].split("-")
        year_heading, month_heading = f"### {year}\n", f"#### {int(month)}月\n"
        if year_heading not in readme:
            readme = readme.replace("## 📺 视频索引\n", f"## 📺 视频索引\n\n{year_heading}", 1)
        start = readme.index(year_heading) + len(year_heading)
        end_match = re.search(r"\n(?:### \d{4}|---)\n", readme[start:])
        end = start + end_match.start() if end_match else len(readme)
        block = readme[start:end]
        if month_heading not in block:
            block = f"\n{month_heading}| 日期 | 标题 | 时长 |\n|------|------|------|\n" + block
        insert_at = block.index("|------|------|------|\n", block.index(month_heading)) + len("|------|------|------|\n")
        row = f"| {video['date'][5:]} | [{markdown_title(video['title'])}](episodes/{year}-{month}/{video['id']}.md) | {video['duration']} |\n"
        block = block[:insert_at] + row + block[insert_at:]
        readme = readme[:start] + block + readme[end:]
    return readme


def main():
    xml = command(["curl", "-fLsS", "--retry", "2", "--max-time", "30", RSS_URL])
    playlist = json.loads(command(["yt-dlp", "--flat-playlist", "--playlist-end", "80", "--dump-single-json", CHANNEL_URL]))
    videos, excerpts = parse_sources(xml, playlist)
    readme_path = ROOT / "README.md"
    original = readme_path.read_text()
    readme = update_readme(original, videos)
    feed_path = ROOT / "latest.json"
    previous = json.loads(feed_path.read_text()) if feed_path.exists() else {}
    # A stale CDN response must not regress the homepage's newest episode.
    if previous.get("videos") and videos[0]["publishedAt"] < previous["videos"][0]["publishedAt"]:
        raise ValueError("Source looks older than the saved feed; refusing regression")
    created = 0
    for video in videos:
        path = ROOT / "episodes" / video["date"][:7] / f"{video['id']}.md"
        # An existing note may use a UTC archive date. Preserve its location.
        if list((ROOT / "episodes").glob(f"*/{video['id']}.md")):
            continue
        excerpt = excerpts[video["id"]]
        excerpt = excerpt[:600].rstrip() + ("…" if len(excerpt) > 600 else "")
        quote = "\n".join("> " + line for line in excerpt.splitlines())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {video['title']}\n\n> **发布日期（北京时间）**：{video['date']} | **时长**：{video['duration']}\n\n[▶ 在 YouTube 观看]({video['url']})\n\n## 频道简介节选\n\n{quote}\n\n---\n\n本页自动同步公开节目的标题、发布时间、时长和频道简介节选，尚未补充人工整理的逐段笔记、代码或提示词。简介中的观点、指标及章节以原视频为准。\n\n[← 返回视频索引](../../README.md)\n")
        created += 1
    feed = {"schemaVersion": 1, "channelId": CHANNEL_ID, "source": CHANNEL_URL,
            "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dateTimezone": "Asia/Shanghai", "videos": videos}
    if previous.get("videos") != videos:
        feed_path.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n")
    if original != readme:
        readme_path.write_text(readme)
    print(json.dumps({"publicVideos": len(videos), "newEpisodePages": created,
                      "latest": videos[0], "feedChanged": previous.get("videos") != videos}, ensure_ascii=False))


if __name__ == "__main__":
    main()
