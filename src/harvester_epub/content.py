"""
Turns one post's raw Markdown (front matter + body) into a clean XHTML
chapter fragment ready to drop into the EPUB, plus a manifest of the
images/videos it references.

Pipeline:
  1. parse_front_matter   -- real YAML parsing, not regex-scraping of key: value lines
  2. strip/resolve Jekyll Liquid tags a plain Markdown parser doesn't understand
  3. render_markdown       -- Markdown -> HTML (Python-Markdown, fenced code + tables)
  4. postprocess_chapter_html -- BeautifulSoup pass: images, <video> embeds, tables
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin

import markdown as md
import yaml
from bs4 import BeautifulSoup

from . import config

log = logging.getLogger("harvester_epub.content")

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n?(.*)\Z", re.DOTALL)
_SERIES_NAV_RE = re.compile(r"\{%\s*series_nav\s*%\}\s*\n?")
_POST_URL_RE = re.compile(r"\{%\s*post_url\s+([\w-]+)\s*%\}")
_LEFTOVER_LIQUID_RE = re.compile(r"\{%|\{\{")


class ContentError(RuntimeError):
    """Raised when a post's content can't be safely turned into a chapter."""


@dataclass
class ImageRef:
    node_id: str
    source_path: str  # as written in the Markdown -- usually site-root-relative;
    # left unresolved on purpose so source.fetch_asset() can still try the
    # source repo first and the live site second, instead of the "prefer
    # the repo" fallback being silently defeated by resolving to an
    # absolute alexbevi.com URL here.
    alt: str


@dataclass
class VideoRef:
    node_id: str
    source_path: str  # as written in the Markdown; used for fetching (repo-first)
    watch_url: str     # absolute URL of the video file itself, for the human-facing link
    page_url: str       # the article page the video appeared on


@dataclass
class Chapter:
    index: int
    title: str
    date: datetime
    slug: str
    tags: list[str]
    category: str
    reading_minutes: int | None
    source_url: str
    body_html: str
    images: list[ImageRef] = field(default_factory=list)
    videos: list[VideoRef] = field(default_factory=list)


def parse_front_matter(raw_text: str) -> tuple[dict, str]:
    match = _FRONT_MATTER_RE.match(raw_text)
    if not match:
        raise ContentError("Post is missing a '---' delimited YAML front matter block")
    front_matter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(front_matter, dict):
        raise ContentError("Front matter did not parse to a mapping")
    return front_matter, match.group(2)


def slug_and_date_from_filename(filename: str) -> tuple[datetime, str]:
    """'2026-03-17-some-slug.markdown' -> (datetime(2026,3,17), 'some-slug')."""
    stem = filename.rsplit(".", 1)[0]
    date_part, slug = stem[:10], stem[11:]
    return datetime.strptime(date_part, "%Y-%m-%d"), slug


def build_permalink(date: datetime, slug: str) -> str:
    # Confirmed against the live site's actual rendered URLs for all 7 posts
    # (not assumed from _config.yml, which does not set `permalink:` at all --
    # it inherits a custom value from elsewhere in the Jekyll build).
    return f"{config.LIVE_SITE_BASE}/blog/{date:%Y}/{date:%m}/{date:%d}/{slug}/"


def _strip_series_nav(body: str) -> str:
    return _SERIES_NAV_RE.sub("", body, count=1)


def _resolve_post_url_tags(body: str, known_posts: dict[tuple[str, str], int]) -> str:
    """
    Replace every {% post_url YYYY-MM-DD-slug %} with a real link.

    If the target is one of the other chapters in *this* book, link to it
    internally (chapter_N.xhtml) so cross-references between parts of the
    series work offline, with no dependency on the reader having a network
    connection. Otherwise link out to the live post on alexbevi.com.
    """

    def _replace(m: re.Match) -> str:
        token = m.group(1)
        date_part, slug = token[:10], token[11:]
        key = (date_part, slug)
        if key in known_posts:
            return f"chapter_{known_posts[key]}.xhtml"
        try:
            date = datetime.strptime(date_part, "%Y-%m-%d")
        except ValueError:
            log.warning("Malformed {%% post_url %s %%}; linking to site root instead", token)
            return f"{config.LIVE_SITE_BASE}/"
        return build_permalink(date, slug)

    return _POST_URL_RE.sub(_replace, body)


def preprocess_liquid(body: str, known_posts: dict[tuple[str, str], int]) -> str:
    body = _strip_series_nav(body)
    body = _resolve_post_url_tags(body, known_posts)
    return body


def render_markdown(body: str) -> str:
    return md.markdown(
        body,
        extensions=["extra", "codehilite", "sane_lists"],
        extension_configs={
            "codehilite": {
                # Only highlight fences with an *explicit* language
                # (```python, ```bash, ```diff). This series' unlabelled
                # fences are prompt transcripts and raw hex/opcode
                # listings, not source code in any language Pygments
                # knows -- guessing a lexer for those produces confident,
                # wrong syntax coloring, which reads worse to an actual
                # reader than plain monospace text.
                "guess_lang": False,
                "css_class": "highlight",
                "pygments_style": config.PYGMENTS_STYLE,
            }
        },
        output_format="xhtml",
    )


def postprocess_chapter_html(
    html_fragment: str, chapter_index: int, chapter_title: str, page_url: str
) -> tuple[str, list[ImageRef], list[VideoRef]]:
    soup = BeautifulSoup(html_fragment, "html.parser")

    # Several posts embed a raw <style> block directly in their Markdown
    # (kramdown/Jekyll happily passes raw HTML through). That's a page-level
    # CSS hack for the author's own site cascade -- e.g. a code-wrapping fix
    # -- and it renders fine there because the theme hoists nothing, it just
    # sits in the flow. It is not valid EPUB3 body content (style/script are
    # head-only in the strict content model) and every EPUB reading system
    # would either reject it (as epubcheck does) or silently ignore it, so
    # it is dropped entirely; our own stylesheet already handles code-block
    # wrapping deliberately (see build_stylesheet in epub_build.py).
    for tag in soup.find_all(["style", "script"]):
        log.info("Chapter %d: dropping embedded <%s> block from source markdown", chapter_index, tag.name)
        tag.decompose()

    images: list[ImageRef] = []
    for i, img in enumerate(soup.find_all("img"), start=1):
        src = img.get("src", "")
        if not src:
            log.warning("Chapter %d: <img> with no src, dropping", chapter_index)
            img.decompose()
            continue
        node_id = f"ch{chapter_index}-img{i}"
        alt = (img.get("alt") or "").strip()
        if not alt:
            # The author doesn't caption inline screenshots; a numbered
            # fallback beats an empty alt attribute for accessibility.
            alt = f"Figure {i}, {chapter_title}"
        img["alt"] = alt
        img["id"] = node_id
        img["src"] = node_id  # placeholder; rewritten once the file is downloaded
        images.append(ImageRef(node_id=node_id, source_path=src, alt=alt))

    videos: list[VideoRef] = []
    for i, video in enumerate(soup.find_all("video"), start=1):
        source_tag = video.find("source")
        src = source_tag.get("src", "") if source_tag else ""
        if not src:
            log.warning("Chapter %d: <video> with no <source src>, dropping", chapter_index)
            video.decompose()
            continue
        watch_url = src if src.startswith("http") else urljoin(config.LIVE_SITE_BASE + "/", src.lstrip("/"))
        node_id = f"ch{chapter_index}-video{i}"
        videos.append(
            VideoRef(node_id=node_id, source_path=src, watch_url=watch_url, page_url=page_url)
        )
        # EPUB reading systems overwhelmingly do not support inline <video>
        # playback (Kindle, Apple Books, KOReader all ignore or strip it).
        # Shipping the raw tag verbatim -- what a naive scrape does -- means
        # most readers see nothing here at all, or literally the browser
        # fallback text ("Your browser does not support the video tag") as
        # orphaned prose. Leave a marker div; epub_build.py replaces it with
        # a real poster frame plus a "watch online" link.
        placeholder = soup.new_tag("div")
        placeholder["class"] = "video-placeholder"
        placeholder["id"] = node_id
        video.replace_with(placeholder)

    for table in soup.find_all("table"):
        wrapper = soup.new_tag("div")
        wrapper["class"] = "table-wrapper"
        table.wrap(wrapper)

    # Safety net: fail loudly rather than silently ship broken markup. This
    # would only fire if a Liquid tag slipped past preprocess_liquid --
    # either a genuinely new tag this series hasn't used before, or a bug
    # in the regexes above -- and it's much better caught here, as a build
    # failure with a clear message, than as "{% something %}" showing up as
    # literal text in a finished ebook.
    leftover = soup.find(string=_LEFTOVER_LIQUID_RE)
    if leftover:
        raise ContentError(
            f"Unprocessed Liquid syntax survived into chapter {chapter_index}: "
            f"{str(leftover).strip()[:120]!r}"
        )

    return str(soup), images, videos
