"""Public metadata and bounded first-part subtitles; never a claim of watching a video."""

import json
import math
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin

from source_reader.network import ReadFailure, video_url


class VideoPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.scripts, self.current = {}, [], None
        self.links, self.anchor = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.meta[attrs.get("property", attrs.get("name", ""))] = attrs.get("content", "")
        elif tag == "script":
            self.current = ""
        elif tag == "a":
            self.anchor = {"url": attrs.get("href", ""), "title": attrs.get("title", "")}

    def handle_data(self, value):
        if self.current is not None:
            self.current += value
        elif self.anchor and not self.anchor["title"] and value.strip():
            self.anchor["title"] = value.strip()

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append(self.current)
            self.current = None
        elif tag == "a" and self.anchor:
            self.links.append(self.anchor)
            self.anchor = None

    def metadata(self):
        for script in self.scripts:
            match = re.search(r"(?:window\.)?__INITIAL_STATE__\s*=\s*", script)
            if match:
                try:
                    state, _ = json.JSONDecoder().raw_decode(script[match.end() :])
                    if isinstance(state.get("videoData"), dict):
                        return state["videoData"]
                except (ValueError, AttributeError):
                    continue
        return {
            "title": self.meta.get("og:title", ""),
            "desc": self.meta.get("og:description", self.meta.get("description", "")),
        }


def api_data(raw):
    payload = json.loads(raw)
    if payload.get("code") in {-101, -111, -352, -412, -403, -10403}:
        raise ReadFailure("UPSTREAM_ACCESS_LIMIT")
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
    return payload["data"]


def subtitle_excerpt(raw, limit=8000, duration=None):
    payload = json.loads(raw)
    rows = payload.get("body")
    if not isinstance(rows, list) or not rows:
        raise ReadFailure("SUBTITLE_UNAVAILABLE")
    lines, spans, used = [], [], 0
    for row in rows[:120]:
        start, end, text = row.get("from"), row.get("to"), row.get("content")
        if (
            not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not 0 <= start < end <= 36000
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
        if duration is not None and end > duration + 1:
            raise ReadFailure("READER_SUBTITLE_TARGET_MISMATCH")
        line = f"[{start:.2f}–{end:.2f}s] {text.strip()}"
        if used + len(line) + 1 > limit:
            break
        lines.append(line)
        spans.append({"start_seconds": start, "end_seconds": end})
        used += len(line) + 1
    if not spans:
        raise ReadFailure("SUBTITLE_UNAVAILABLE")
    return "\n".join(lines), spans, len(spans) < len(rows)


class BilibiliReader:
    def __init__(
        self, public, renderer=None, clock=lambda: datetime.now(UTC).isoformat(), subtitles=True
    ):
        self.public, self.renderer, self.clock = public, renderer, clock
        self.subtitles = subtitles

    async def read(self, item, budget):
        if item["operation"] == "discover":
            raw = await self.public.get("https://www.bilibili.com/", budget, "page")
            page = VideoPage()
            page.feed(raw.decode("utf-8", errors="replace"))
            found = {}
            for link in page.links:
                try:
                    url = video_url(urljoin("https://www.bilibili.com/", link["url"]))
                    if link["title"].strip():
                        found.setdefault(
                            url, {"url": url, "title": link["title"][:300], "text": ""}
                        )
                except (ReadFailure, ValueError):
                    continue
            return list(found.values())[: min(40, item["max_items"])]
        if item["operation"] != "read":
            raise ReadFailure("READER_OPERATION_INVALID")
        url = video_url(item["url"])
        identifier = url.rsplit("/", 1)[1]
        params = {"bvid": identifier} if identifier.startswith("BV") else {"aid": identifier[2:]}
        method = getattr(self.public, "reading_method", "public_api")
        raw = await self.public.get(
            "https://api.bilibili.com/x/web-interface/view?" + urlencode(params), budget, "metadata"
        )
        data = api_data(raw)
        if (identifier.startswith("BV") and data.get("bvid", identifier) != identifier) or (
            identifier.startswith("av") and str(data.get("aid", identifier[2:])) != identifier[2:]
        ):
            raise ReadFailure("READER_TARGET_MISMATCH")
        if not str(data.get("desc", "")).strip():
            # Fall back only after a successful public response, never around an access denial.
            try:
                raw = await self.public.get(url, budget, "page")
                page = VideoPage()
                page.feed(raw.decode("utf-8", errors="replace"))
                rendered = page.metadata()
                if not str(rendered.get("desc", "")).strip() and self.renderer:
                    rendered = await self.renderer.metadata(url, raw)
                    method = "browser_render"
                elif method != "account_api":
                    method = "static_page"
                data = {**data, **{k: v for k, v in rendered.items() if v}}
            except ReadFailure as error:
                if str(error) not in {"READER_REQUEST_LIMIT", "READER_BYTE_LIMIT"}:
                    raise
        title, description = data.get("title", ""), data.get("desc", "")
        if not isinstance(title, str) or not isinstance(description, str):
            raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
        title, description = title.strip()[:300], description.strip()[:1800]
        if not title or not description:
            raise ReadFailure("READ_BODY_UNAVAILABLE")
        up = data.get("owner") or {}
        intro = f"标题：{title}\nUP主：{str(up.get('name', '未取得'))[:100]}\n"
        intro += f"简介：{description}"
        evidence = {
            "level": "L1",
            "method": method,
            "fetched_at": self.clock(),
            "part": 1,
            "subtitle_status": "unavailable",
            "language": "",
            "spans": [],
            "sources": budget.sources,
            "truncated": False,
        }
        subtitle = ""
        cid = data.get("cid")
        if self.subtitles and isinstance(cid, int) and cid > 0:
            try:
                aid = data.get("aid")
                player_params = {"aid": aid} if type(aid) is int and aid > 0 else params
                player = api_data(
                    await self.public.get(
                        "https://api.bilibili.com/x/player/v2?"
                        + urlencode({**player_params, "cid": cid}),
                        budget,
                        "player",
                    )
                )
                if any(
                    key in player and key in data and player[key] != data[key]
                    for key in ("aid", "bvid", "cid")
                ):
                    raise ReadFailure("READER_SUBTITLE_TARGET_MISMATCH")
                info = player.get("subtitle") or {}
                tracks = info.get("subtitles") or []
                if not tracks:
                    evidence["subtitle_status"] = (
                        "restricted"
                        if player.get("need_login_subtitle") or info.get("need_login_subtitle")
                        else "missing"
                    )
                else:
                    track = next(
                        (t for t in tracks if str(t.get("lan", "")).startswith("zh")), tracks[0]
                    )
                    caption_url = urljoin("https://aisubtitle.hdslb.com/", track["subtitle_url"])
                    # Observed upstream AI URLs bind a video/part to a 32-hex content key.
                    # Reject a mismatched target before fetching someone else's transcript.
                    ai_path = "/bfs/ai_subtitle/prod/"
                    if ai_path in caption_url and (
                        type(aid) is not int
                        or not re.fullmatch(
                            r"https://aisubtitle\.hdslb\.com"
                            + ai_path
                            + str(aid)
                            + str(cid)
                            + r"[0-9a-fA-F]{32}(?:\.json)?(?:\?[^#]*)?",
                            caption_url,
                        )
                    ):
                        raise ReadFailure("READER_SUBTITLE_TARGET_MISMATCH")
                    raw = await self.public.get(caption_url, budget, "subtitle")
                    duration = data.get("duration")
                    pages = data.get("pages") or []
                    if pages and pages[0].get("cid") == cid:
                        duration = pages[0].get("duration", duration)
                    if type(duration) not in {int, float} or not math.isfinite(duration):
                        duration = None
                    subtitle, spans, truncated = subtitle_excerpt(raw, duration=duration)
                    evidence.update(
                        level="L2",
                        subtitle_status="available",
                        spans=spans,
                        language=str(track.get("lan", ""))[:32],
                        truncated=truncated,
                    )
            except ReadFailure as error:
                if str(error) in {
                    "OWNER_ACCOUNT_AUTHORIZATION_REQUIRED",
                    "SOURCE_CREDENTIAL_REVOKED",
                    "SOURCE_DISABLED",
                    "PLUGIN_UNAVAILABLE",
                    "READER_SUBTITLE_TARGET_MISMATCH",
                }:
                    raise
                evidence["subtitle_status"] = (
                    "restricted"
                    if str(error) == "UPSTREAM_ACCESS_LIMIT"
                    else "budget_limited"
                    if str(error).startswith("READER_")
                    else "unavailable"
                )
            except (ValueError, KeyError, TypeError, AttributeError):
                evidence["subtitle_status"] = "unavailable"
        else:
            evidence["subtitle_status"] = "unavailable"
        access = "账号授权" if method == "account_api" else "公开"
        scope = (
            f"读取范围：{access}元数据及第1分P的部分字幕；未分析画面或音频，未完整观看。"
            if subtitle
            else f"读取范围：{access}标题和简介；字幕{evidence['subtitle_status']}；未观看视频。"
        )
        evidence["fetched_at"] = self.clock()
        return [
            {
                "title": title[:300],
                "url": url,
                "text": scope + "\n" + intro + ("\n字幕：\n" + subtitle if subtitle else ""),
                "reading": evidence,
            }
        ]
