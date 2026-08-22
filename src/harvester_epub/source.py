"""
Fetches everything the build needs from the network: post Markdown source,
referenced images/videos, and (only as a last resort) rendered HTML.

Preference order per asset:
  1. Raw file from the alexbevi.github.com source repo (raw.githubusercontent.com).
     This is the actual authored content: no theme markup, no lazy-loading
     rewrites, no client-side JS to fight.
  2. The live production site (alexbevi.com), for assets that only exist
     there, or if the repo is briefly unreachable.

Every network call goes through a requests.Session configured with retries
and a real User-Agent, because the default python-requests UA gets silently
throttled or served degraded content by more than a few hosts -- the single
biggest practical reason a naive scraper fails intermittently in CI.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

log = logging.getLogger("harvester_epub.source")


class SourceError(RuntimeError):
    """Raised when a required source asset cannot be fetched from any location."""


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT, "Accept": "*/*"})
    retry = Retry(
        total=config.HTTP_RETRIES,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = _build_session()


def fetch_post_markdown(filename: str) -> str:
    """Fetch one post's raw Markdown source (front matter + body) from the source repo."""
    url = f"{config.SOURCE_RAW_BASE}/_posts/{filename}"
    log.info("Fetching source markdown: %s", filename)
    resp = SESSION.get(url, timeout=config.HTTP_TIMEOUT)
    if resp.status_code != 200:
        raise SourceError(
            f"Could not fetch source markdown for {filename!r} "
            f"(HTTP {resp.status_code} from {url})"
        )
    resp.encoding = "utf-8"
    return resp.text


def fetch_asset(path_or_url: str) -> bytes:
    """
    Fetch a binary asset (image or video) referenced by a post.

    Accepts either a site-root-relative path as written in the Markdown
    (e.g. "/images/ghidra1/ghidra-exe-extended.png") -- the normal case for
    this site -- or an already-absolute URL. Relative paths try the source
    repo first (assets are committed alongside the posts at the same
    relative layout), then fall back to the live site; an absolute URL is
    fetched as-is, since there's no repo-relative equivalent to prefer.
    """
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        candidates = [path_or_url]
    else:
        clean = path_or_url.lstrip("/")
        candidates = [
            f"{config.SOURCE_RAW_BASE}/{clean}",
            urljoin(config.LIVE_SITE_BASE + "/", clean),
        ]
    last_error = "no candidates tried"
    for url in candidates:
        try:
            resp = SESSION.get(url, timeout=config.HTTP_TIMEOUT)
            if resp.status_code == 200 and resp.content:
                return resp.content
            last_error = f"HTTP {resp.status_code} from {url}"
        except requests.RequestException as exc:
            last_error = f"{exc} ({url})"
    raise SourceError(f"Could not fetch asset {path_or_url!r}: {last_error}")


def fetch_source_commit_sha() -> str | None:
    """
    Best-effort: record the exact commit of the source repo this build was
    made from, for the colophon. A missing value degrades the provenance
    note gracefully; it must never fail the whole build.
    """
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = SESSION.get(
            f"{config.SOURCE_API_BASE}/commits/{config.SOURCE_BRANCH}",
            timeout=config.HTTP_TIMEOUT,
            headers=headers,
        )
        if resp.status_code == 200:
            return resp.json().get("sha")
        log.warning("Could not resolve source commit SHA (HTTP %s)", resp.status_code)
    except requests.RequestException as exc:
        log.warning("Could not resolve source commit SHA: %s", exc)
    return None


def fetch_live_content_html(post_url: str) -> str:
    """
    Fallback path: fetch the rendered page and isolate the article body.

    Only used if fetch_post_markdown() fails for a post. Targets the
    verified Chirpy v7.6.0 structure (<article> > div.content, a *direct*
    child, added specifically after this theme's post-tail-wrapper was
    confirmed to sit as a sibling rather than inside .content) -- confirmed
    against this exact theme version rather than guessed from common
    WordPress/Jekyll class-name conventions.
    """
    log.warning("Falling back to live-HTML scrape for %s", post_url)
    resp = SESSION.get(post_url, timeout=config.HTTP_TIMEOUT)
    if resp.status_code != 200:
        raise SourceError(f"Could not fetch live page {post_url} (HTTP {resp.status_code})")

    soup = BeautifulSoup(resp.text, "lxml")
    article = soup.find("article")
    if article is None:
        raise SourceError(f"No <article> element on {post_url}; site structure may have changed")

    content = article.find("div", class_="content", recursive=False)
    if content is None:
        raise SourceError(
            f"No direct <article> > div.content on {post_url}; "
            "the Chirpy theme structure this fallback was built against "
            "(v7.6.0) may have changed. Update source.fetch_live_content_html."
        )
    return content.decode_contents()
