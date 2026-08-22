"""
Central configuration for the Harvester series EPUB build.

Every value that could plausibly need to change (source repo, book
metadata, visual style) lives here so the rest of the pipeline stays
free of magic strings and hardcoded URLs.
"""
from __future__ import annotations

# --- Source of truth --------------------------------------------------------
# The blog is a Jekyll site built from this public repo and served at
# alexbevi.com. We prefer pulling the raw Markdown source directly from the
# repo -- clean, versioned, no theme markup, no lazy-loading rewrites, no
# client-side JS to fight -- and only fall back to scraping the rendered page
# if the repo is ever unreachable. See source.py for the fallback logic.
SOURCE_OWNER = "alexbevi"
SOURCE_REPO = "alexbevi.github.com"
SOURCE_BRANCH = "main"
SOURCE_RAW_BASE = f"https://raw.githubusercontent.com/{SOURCE_OWNER}/{SOURCE_REPO}/{SOURCE_BRANCH}"
SOURCE_API_BASE = f"https://api.github.com/repos/{SOURCE_OWNER}/{SOURCE_REPO}"

# The live, rendered site. Used to build absolute URLs for links that leave
# the book, and as the fallback path if raw.githubusercontent.com is ever
# unreachable (see source.fetch_live_content_html).
LIVE_SITE_BASE = "https://alexbevi.com"

# Exact _posts/ filenames, in reading order. Pinned deliberately rather than
# discovered by crawling an index page: this book is a curated compilation
# of one specific series, not "whatever the blog happens to publish next."
POST_FILENAMES = [
    "2026-03-14-reverse-engineering-a-dos-game-with-ghidra-and-codex.markdown",
    "2026-03-17-reverse-engineering-harvester-with-ghidra-and-codex-part-2.markdown",
    "2026-03-23-reverse-engineering-harvester-with-ghidra-and-codex-part-3-file-formats.markdown",
    "2026-03-23-reverse-engineering-harvester-with-ghidra-and-codex-part-4-command-opcodes.markdown",
    "2026-03-29-reverse-engineering-harvester-with-ghidra-and-codex-part-5-debugging-audio-issues.markdown",
    "2026-04-14-reverse-engineering-harvester-with-ghidra-and-codex-part-6-timers.markdown",
    "2026-07-11-reverse-engineering-harvester-with-ghidra-and-codex-part-7-demo-support.markdown",
]

# --- Book metadata -----------------------------------------------------------
BOOK_TITLE = "Reverse Engineering Harvester with Ghidra and Codex"
BOOK_SUBTITLE = "A Seven-Part Field Log"
BOOK_AUTHOR = "Alex Bevilacqua"
BOOK_AUTHOR_HANDLE = "alexbevi"
BOOK_AUTHOR_URL = "https://alexbevi.com"
BOOK_LANGUAGE = "en"

LICENSE_NAME = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"

REPO_URL = "https://github.com/boolforge/alexbevi-harvester-epub"

OUTPUT_FILENAME = "reverse-engineering-harvester-with-ghidra-and-codex.epub"

# --- Visual identity ---------------------------------------------------------
# See README's "Design notes" section for the reasoning. Short version: this
# leans on Ghidra's actual brand teal and a period-accurate amber phosphor
# accent instead of the generic "black background, neon-green terminal" look.
PALETTE = {
    "bg": "#17151A",
    "bg_raised": "#211E26",
    "ink": "#EDE7DD",
    "ink_muted": "#A9A2AC",
    "amber": "#E8A33D",
    "teal": "#4FB0A5",
    "rule": "#38323F",
}

PYGMENTS_STYLE = "monokai"

# Cover art: two real images from the series, chosen after reviewing all
# 26 embedded figures. bm-pal.png is the game's own "HARVESTER" title card,
# the single most striking and immediately recognizable image in the whole
# series; ghidra-exe-extended.png is a real disassembly/strings view from
# the author's own Ghidra session, used as a thin "data layer" between the
# game-art band and the book's own typography. See media.build_cover().
COVER_HERO_IMAGE = "/images/ghidra3/bm-pal.png"
COVER_DATA_STRIP_IMAGE = "/images/ghidra1/ghidra-exe-extended.png"

FONT_DIR = "/usr/share/fonts/truetype"
FONTS = {
    "serif": f"{FONT_DIR}/ibm-plex/IBMPlexSerif-Regular.ttf",
    "serif_italic": f"{FONT_DIR}/ibm-plex/IBMPlexSerif-Italic.ttf",
    "serif_bold": f"{FONT_DIR}/ibm-plex/IBMPlexSerif-Bold.ttf",
    "mono": f"{FONT_DIR}/ibm-plex/IBMPlexMono-Regular.ttf",
    "mono_medium": f"{FONT_DIR}/ibm-plex/IBMPlexMono-Medium.ttf",
    "mono_bold": f"{FONT_DIR}/ibm-plex/IBMPlexMono-Bold.ttf",
}

# --- Networking ---------------------------------------------------------
HTTP_TIMEOUT = 20
HTTP_RETRIES = 4
USER_AGENT = (
    "harvester-epub-builder/1.0 "
    f"(+{REPO_URL}; personal archival tool, not a crawler)"
)
