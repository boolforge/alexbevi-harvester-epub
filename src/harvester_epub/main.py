"""
CLI entry point. Orchestrates the full pipeline:

  fetch source markdown (repo, falling back to live-HTML scrape)
    -> parse front matter + resolve Liquid tags
    -> render Markdown to HTML
    -> post-process (images, video embeds, tables)
    -> download every referenced image / extract video posters
    -> assemble the EPUB
    -> validate the result

Every stage logs through the standard `logging` module instead of bare
print() calls, and the process exits non-zero on any condition that would
produce a broken or incomplete book. That last point matters more than it
sounds: a scraper that silently skips a chapter it couldn't parse and
still exits 0 is the single most common way this kind of tool ships a
broken artifact without anyone noticing until a reader complains.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from . import config, content, epub_build, media, source, validate

log = logging.getLogger("harvester_epub")

_WORDS_PER_MINUTE = 220


def _estimate_reading_minutes(html: str) -> int:
    text = re.sub(r"<[^>]+>", " ", html)
    words = len(text.split())
    return max(1, round(words / _WORDS_PER_MINUTE))


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def build_known_posts_index() -> dict[tuple[str, str], int]:
    known: dict[tuple[str, str], int] = {}
    for idx, filename in enumerate(config.POST_FILENAMES, start=1):
        date, slug = content.slug_and_date_from_filename(filename)
        known[(date.strftime("%Y-%m-%d"), slug)] = idx
    return known


def load_chapter(index: int, filename: str, known_posts: dict[tuple[str, str], int]) -> content.Chapter:
    date, slug = content.slug_and_date_from_filename(filename)
    source_url = content.build_permalink(date, slug)

    try:
        raw = source.fetch_post_markdown(filename)
        front_matter, body = content.parse_front_matter(raw)
        body = content.preprocess_liquid(body, known_posts)
        rendered = content.render_markdown(body)
        title = front_matter.get("title", slug)
    except (source.SourceError, content.ContentError) as exc:
        log.error("Repo fetch failed for %s (%s); falling back to live HTML scrape", filename, exc)
        rendered = source.fetch_live_content_html(source_url)
        front_matter = {"tags": [], "categories": []}
        title = slug.replace("-", " ").title()

    body_html, images, videos = content.postprocess_chapter_html(rendered, index, title, source_url)

    return content.Chapter(
        index=index,
        title=title,
        date=date,
        slug=slug,
        tags=_as_list(front_matter.get("tags")),
        category=", ".join(_as_list(front_matter.get("categories"))),
        reading_minutes=_estimate_reading_minutes(body_html),
        source_url=source_url,
        body_html=body_html,
        images=images,
        videos=videos,
    )


def run(output_path: str, run_epubcheck: bool, work_dir: Path) -> int:
    known_posts = build_known_posts_index()

    log.info("=== Loading %d chapters ===", len(config.POST_FILENAMES))
    chapters: list[content.Chapter] = []
    for idx, filename in enumerate(config.POST_FILENAMES, start=1):
        log.info("[%d/%d] %s", idx, len(config.POST_FILENAMES), filename)
        chapter = load_chapter(idx, filename, known_posts)
        if not chapter.body_html or len(chapter.body_html.strip()) < 200:
            # A chapter this short almost certainly means the content
            # container was found but came back empty -- exactly the
            # failure mode that a silent `continue` would ship anyway.
            # Fail loudly instead.
            log.error(
                "Chapter %d (%s) rendered to only %d characters of body HTML -- "
                "treating as a broken extraction rather than shipping a near-empty chapter",
                idx, filename, len(chapter.body_html.strip()),
            )
            return 1
        chapters.append(chapter)
        log.info(
            "  -> %r: %d images, %d videos, ~%d min read",
            chapter.title, len(chapter.images), len(chapter.videos), chapter.reading_minutes,
        )

    log.info("=== Downloading media ===")
    stats = epub_build.BuildStats()
    prepared_images_by_chapter: dict[int, dict[str, media.PreparedAsset | None]] = {}
    prepared_videos_by_chapter: dict[int, dict[str, media.PreparedAsset | None]] = {}

    for ch in chapters:
        img_map: dict[str, media.PreparedAsset | None] = {}
        for ref in ch.images:
            prepared = media.prepare_image(ref.node_id, ref.source_path)
            img_map[ref.node_id] = prepared
            if prepared:
                stats.images_embedded += 1
            else:
                stats.images_failed += 1
        prepared_images_by_chapter[ch.index] = img_map

        vid_map: dict[str, media.PreparedAsset | None] = {}
        for ref in ch.videos:
            prepared = media.prepare_video_poster(ref.node_id, ref.source_path, ref.watch_url)
            vid_map[ref.node_id] = prepared
            if prepared:
                stats.videos_embedded += 1
            else:
                stats.videos_failed += 1
        prepared_videos_by_chapter[ch.index] = vid_map

    log.info(
        "Media summary: %d/%d images embedded, %d/%d video posters embedded",
        stats.images_embedded, stats.images_embedded + stats.images_failed,
        stats.videos_embedded, stats.videos_embedded + stats.videos_failed,
    )

    source_commit = source.fetch_source_commit_sha()
    log.info("Source commit: %s", source_commit or "unavailable")

    log.info("=== Assembling EPUB ===")
    output_dir = Path(output_path).parent
    if str(output_dir) not in ("", "."):
        output_dir.mkdir(parents=True, exist_ok=True)
    epub_build.assemble_and_write(
        chapters, prepared_images_by_chapter, prepared_videos_by_chapter,
        source_commit, stats, output_path,
    )

    log.info("=== Validating ===")
    try:
        validate.validate_structure(output_path)
    except validate.ValidationError as exc:
        log.error("Structural validation FAILED: %s", exc)
        return 1

    if run_epubcheck:
        ok = validate.run_epubcheck(output_path, work_dir)
        if not ok:
            log.error("epubcheck FAILED")
            return 1

    log.info("=== Done: %s ===", output_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Harvester series EPUB.")
    parser.add_argument("-o", "--output", default=config.OUTPUT_FILENAME)
    parser.add_argument("--skip-epubcheck", action="store_true")
    parser.add_argument("--work-dir", default=".epubcheck-cache")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        return run(args.output, run_epubcheck=not args.skip_epubcheck, work_dir=work_dir)
    except Exception:
        log.exception("Build failed with an unhandled exception")
        return 1


if __name__ == "__main__":
    sys.exit(main())
