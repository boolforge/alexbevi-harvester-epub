"""
Assembles the final EPUB: metadata, stylesheet, embedded fonts, cover,
prologue, the seven chapters (with images/video posters wired in), a
colophon, and the navigation documents.

One deliberate constraint shapes the CSS here: EPUB reading systems let
readers choose day/night/sepia themes, and different systems honor
different subsets of a stylesheet's `color` / `background-color` pairs.
Forcing a dark `background-color` on <body> is a common way for a
"good-looking in the preview" EPUB to render as invisible light-grey text
on a reader's white theme in practice. So the vivid brand palette
(config.PALETTE) is used freely on the cover (a raster image, fully under
our control) and inside self-contained dark elements like code blocks, but
body text and backgrounds are left to the reading system, with heading
accents pulled a shade darker so they stay legible on a plain white page,
which is the overwhelmingly common default.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub
from pygments.formatters import HtmlFormatter

from . import config, media
from .content import Chapter, ImageRef, VideoRef

log = logging.getLogger("harvester_epub.epub_build")

TEXT_ON_LIGHT = {
    "amber": "#96591C",
    "teal": "#1D7A70",
}


@dataclass
class BuildStats:
    images_embedded: int = 0
    images_failed: int = 0
    videos_embedded: int = 0
    videos_failed: int = 0


def _font_face_rules() -> str:
    rules = []
    face_specs = [
        ("IBM Plex Serif", "normal", "normal", "serif"),
        ("IBM Plex Serif", "italic", "normal", "serif_italic"),
        ("IBM Plex Serif", "normal", "bold", "serif_bold"),
        ("IBM Plex Mono", "normal", "normal", "mono"),
        ("IBM Plex Mono", "normal", "500", "mono_medium"),
        ("IBM Plex Mono", "normal", "bold", "mono_bold"),
    ]
    for family, style, weight, key in face_specs:
        rules.append(
            f"""@font-face {{
  font-family: "{family}";
  font-style: {style};
  font-weight: {weight};
  src: url("../fonts/{key}.ttf");
}}"""
        )
    return "\n".join(rules)


def build_stylesheet() -> str:
    p = config.PALETTE
    pygments_css = HtmlFormatter(style=config.PYGMENTS_STYLE).get_style_defs(".highlight")
    return f"""
{_font_face_rules()}

body {{
  font-family: "IBM Plex Serif", Georgia, "Times New Roman", serif;
  line-height: 1.55;
  margin: 1.4em;
  text-align: left;
}}

h1, h2, h3, h4 {{
  font-family: "IBM Plex Mono", "Courier New", monospace;
  line-height: 1.25;
  margin-top: 1.6em;
  margin-bottom: 0.5em;
}}

h1 {{ color: {TEXT_ON_LIGHT['amber']}; font-size: 1.5em; border-bottom: 2px solid {p['rule']}; padding-bottom: 0.25em; }}
h2 {{ color: {TEXT_ON_LIGHT['teal']}; font-size: 1.2em; }}
h3 {{ font-size: 1.05em; }}

a {{ color: {TEXT_ON_LIGHT['teal']}; }}

.chapter-meta {{
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.78em;
  color: #666;
  letter-spacing: 0.02em;
  margin-bottom: 1.6em;
}}
.chapter-meta .sep {{ margin: 0 0.5em; }}

.eyebrow {{
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.75em;
  letter-spacing: 0.12em;
  color: {TEXT_ON_LIGHT['teal']};
  text-transform: uppercase;
}}

img {{ max-width: 100%; height: auto; display: block; margin: 1.3em auto; }}

figure {{ margin: 1.3em 0; text-align: center; }}
figcaption {{
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.75em;
  color: #666;
  margin-top: 0.4em;
}}
figcaption a {{ color: {TEXT_ON_LIGHT['teal']}; }}

.video-figure img {{ border-radius: 4px; }}
.play-badge {{ font-weight: bold; }}

blockquote {{
  border-left: 3px solid {p['rule']};
  margin: 1.3em 0.2em;
  padding: 0.2em 1em;
  font-style: italic;
  color: #444;
}}

.table-wrapper {{ overflow-x: auto; margin: 1.3em 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.88em; }}
th, td {{ border: 1px solid {p['rule']}; padding: 0.4em 0.6em; text-align: left; vertical-align: top; }}
th {{ font-family: "IBM Plex Mono", monospace; background-color: rgba(79, 176, 165, 0.12); }}

code {{ font-family: "IBM Plex Mono", "Consolas", monospace; font-size: 0.88em; }}

.highlight, pre {{
  background-color: {p['bg_raised']};
  color: {p['ink']};
  border-radius: 6px;
  padding: 0.9em 1em;
  overflow-x: auto;
  font-family: "IBM Plex Mono", "Consolas", monospace;
  font-size: 0.82em;
  line-height: 1.5;
}}
p > code, li > code {{ background-color: rgba(56, 50, 63, 0.10); padding: 0.1em 0.3em; border-radius: 3px; }}

{pygments_css}

.colophon, .prologue {{ font-size: 0.95em; }}
.attribution-box {{
  border: 1px solid {p['rule']};
  border-radius: 6px;
  padding: 1em 1.2em;
  margin: 1.4em 0;
  background-color: rgba(232, 163, 61, 0.06);
}}
hr.hex-rule {{
  border: none;
  border-top: 1px dashed {p['rule']};
  margin: 2em 0;
}}
""".strip()


def wire_media_into_chapter(
    html_fragment: str,
    images: list[ImageRef],
    videos: list[VideoRef],
    prepared_images: dict[str, media.PreparedAsset | None],
    prepared_videos: dict[str, media.PreparedAsset | None],
) -> str:
    """
    Second pass over a chapter's HTML: rewrite each image placeholder to
    its real embedded filename, and expand each video placeholder <div>
    into a poster figure with a "watch online" link. Runs after media.py
    has attempted to download everything, so it knows which assets
    actually succeeded.
    """
    soup = BeautifulSoup(html_fragment, "html.parser")

    for ref in images:
        node = soup.find(id=ref.node_id)
        if node is None:
            continue
        prepared = prepared_images.get(ref.node_id)
        if prepared is None:
            node.decompose()  # missing figure beats a broken-image icon
            continue
        node["src"] = f"images/{prepared.filename}"

    for ref in videos:
        node = soup.find(id=ref.node_id)
        if node is None:
            continue
        prepared = prepared_videos.get(ref.node_id)
        figure = soup.new_tag("figure")
        figure["class"] = "video-figure"
        img = soup.new_tag("img")
        img["alt"] = "Video preview -- not playable in this format"
        if prepared is not None:
            img["src"] = f"images/{prepared.filename}"
        figure.append(img)
        caption = soup.new_tag("figcaption")
        caption.append(BeautifulSoup('<span class="play-badge">&#9654;</span> Video &mdash; ', "html.parser"))
        link = soup.new_tag("a", href=ref.watch_url)
        link.string = "watch online"
        caption.append(link)
        figure.append(caption)
        node.replace_with(figure)

    return soup.decode() if soup.contents else html_fragment


def _chapter_meta_html(ch: Chapter) -> str:
    parts = [f'<span class="eyebrow">0x0{ch.index} // Field Log</span>']
    meta_bits = [ch.date.strftime("%B %-d, %Y") if hasattr(ch.date, "strftime") else str(ch.date)]
    if ch.reading_minutes:
        meta_bits.append(f"{ch.reading_minutes} min read")
    if ch.tags:
        meta_bits.append(", ".join(ch.tags))
    parts.append('<p class="chapter-meta">' + '<span class="sep">&middot;</span>'.join(meta_bits) + "</p>")
    return "\n".join(parts)


def build_prologue_xhtml(source_commit: str | None) -> str:
    n = len(config.POST_FILENAMES)
    return f"""
<h1>Prologo y atribucion</h1>
<p class="eyebrow">Nota del compilador</p>
<p>Este libro reune, en un unico volumen offline, los {n} articulos de la serie
<em>&ldquo;{config.BOOK_TITLE}&rdquo;</em>, publicados originalmente por
<strong>{config.BOOK_AUTHOR}</strong> (<code>{config.BOOK_AUTHOR_HANDLE}</code>) en su blog personal.
El texto no ha sido alterado ni corregido: se conserva tal cual fue escrito, incluidas
sus imperfecciones. Lo unico anadido es el formato: portada, indice, notas al pie de
cada capitulo y un colofon con la procedencia exacta de cada fuente.</p>

<div class="attribution-box">
<p><strong>Autor original:</strong> {config.BOOK_AUTHOR} (<code>{config.BOOK_AUTHOR_HANDLE}</code>)<br/>
<strong>Fuente:</strong> <a href="{config.BOOK_AUTHOR_URL}">{config.BOOK_AUTHOR_URL}</a><br/>
<strong>Licencia:</strong> <a href="{config.LICENSE_URL}">{config.LICENSE_NAME}</a> &mdash;
se permite copiar, redistribuir y adaptar el contenido, incluso con fines comerciales,
citando la autoria. Este volumen es precisamente ese tipo de adaptacion: un
reformateo para lectura offline, sin cambios en el texto ni en las ideas originales.</p>
</div>

<p>Esta es una compilacion no oficial, hecha por un lector, sin ninguna afiliacion
con el autor. Si el autor preferiese que esta compilacion no circulase en esta forma,
basta con abrir un issue en el repositorio del proyecto para retirarla.</p>

<hr class="hex-rule"/>

<p><em>In English: this book compiles Alex Bevilacqua's {n}-part
&ldquo;{config.BOOK_TITLE}&rdquo; series into a single offline EPUB, text unchanged,
under the terms of the original {config.LICENSE_NAME} license
(<a href="{config.LICENSE_URL}">{config.LICENSE_URL}</a>). It is an unofficial,
reader-made compilation with no affiliation to the author. Read the originals
and follow the author's ongoing work at
<a href="{config.BOOK_AUTHOR_URL}">{config.BOOK_AUTHOR_URL}</a>.</em></p>
""".strip()


def build_colophon_xhtml(chapters: list[Chapter], source_commit: str | None, stats: BuildStats) -> str:
    build_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = "\n".join(
        f'<tr><td>{c.index}</td><td>{c.title}</td>'
        f'<td><a href="{c.source_url}">{c.source_url}</a></td></tr>'
        for c in chapters
    )
    commit_line = (
        f'<a href="https://github.com/{config.SOURCE_OWNER}/{config.SOURCE_REPO}/commit/{source_commit}">'
        f'{source_commit[:12]}</a>'
        if source_commit
        else "unavailable at build time"
    )
    return f"""
<h1>Colofon</h1>
<p class="eyebrow">Procedencia / Provenance</p>
<p>Generado el {build_date} con
<a href="{config.REPO_URL}">{config.REPO_URL.rsplit('/', 2)[-2]}/{config.REPO_URL.rsplit('/', 1)[-1]}</a>,
a partir del commit <code>{commit_line}</code> del repositorio fuente
<code>{config.SOURCE_OWNER}/{config.SOURCE_REPO}</code>.</p>

<div class="table-wrapper">
<table>
<thead><tr><th>#</th><th>Capitulo</th><th>URL original</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>

<p class="eyebrow">Tipografia</p>
<p>IBM Plex Serif &amp; IBM Plex Mono, IBM Corporation, licencia SIL Open Font License 1.1.</p>

<p class="eyebrow">Build stats</p>
<p>Images embedded: {stats.images_embedded} &middot; failed: {stats.images_failed} &middot;
Video posters embedded: {stats.videos_embedded} &middot; failed: {stats.videos_failed}</p>
""".strip()


def new_book(source_commit: str | None) -> epub.EpubBook:
    book = epub.EpubBook()
    # Deterministic identifier: a uuid5 derived from the repo URL, so
    # rebuilding from the same source doesn't mint a new "different book"
    # identity every run, the way a fresh uuid4() per build would.
    book_uuid = uuid.uuid5(uuid.NAMESPACE_URL, config.REPO_URL)
    book.set_identifier(f"urn:uuid:{book_uuid}")
    book.set_title(config.BOOK_TITLE)
    book.set_language(config.BOOK_LANGUAGE)
    book.add_author(config.BOOK_AUTHOR)
    book.add_metadata("DC", "description", f"{config.BOOK_SUBTITLE}. Unofficial offline compilation.")
    book.add_metadata("DC", "rights", f"Original text {config.LICENSE_NAME} by {config.BOOK_AUTHOR}. {config.LICENSE_URL}")
    book.add_metadata("DC", "source", config.BOOK_AUTHOR_URL)
    if source_commit:
        book.add_metadata("DC", "identifier", f"source-commit:{source_commit}", others={"id": "source-commit"})
    return book


def assemble_and_write(
    chapters: list[Chapter],
    prepared_images_by_chapter: dict[int, dict[str, media.PreparedAsset | None]],
    prepared_videos_by_chapter: dict[int, dict[str, media.PreparedAsset | None]],
    source_commit: str | None,
    stats: BuildStats,
    output_path: str,
) -> None:
    book = new_book(source_commit)

    css = epub.EpubItem(
        uid="style_main", file_name="style/main.css", media_type="text/css",
        content=build_stylesheet(),
    )
    book.add_item(css)

    for key, path in config.FONTS.items():
        data = Path(path).read_bytes()
        book.add_item(
            epub.EpubItem(
                uid=f"font_{key}", file_name=f"fonts/{key}.ttf",
                media_type="font/ttf", content=data,
            )
        )

    cover_bytes = media.build_cover()
    book.set_cover("images/cover.png", cover_bytes)
    # ebooklib marks the auto-generated cover page non-linear by default,
    # which epubcheck (correctly) flags as unreachable content since
    # nothing links to it (OPF-096). Making it linear both fixes that and
    # matches how readers actually expect a cover to behave: the first
    # page you see, not a hidden extra.
    book.get_item_with_id("cover").is_linear = True

    prologue = epub.EpubHtml(
        title="Prologo y atribucion", file_name="prologue.xhtml", lang="es",
    )
    prologue.content = build_prologue_xhtml(source_commit)
    prologue.add_item(css)
    book.add_item(prologue)

    chapter_items = []
    for ch in chapters:
        wired_html = wire_media_into_chapter(
            ch.body_html, ch.images, ch.videos,
            prepared_images_by_chapter.get(ch.index, {}),
            prepared_videos_by_chapter.get(ch.index, {}),
        )
        item = epub.EpubHtml(
            title=ch.title, file_name=f"chapter_{ch.index}.xhtml", lang=config.BOOK_LANGUAGE,
        )
        item.content = (
            f"<h1>{ch.title}</h1>\n{_chapter_meta_html(ch)}\n{wired_html}\n"
            f'<hr class="hex-rule"/>\n'
            f'<p class="chapter-meta">Originally published at '
            f'<a href="{ch.source_url}">{ch.source_url}</a></p>'
        )
        item.add_item(css)
        book.add_item(item)
        chapter_items.append(item)

        for prepared in prepared_images_by_chapter.get(ch.index, {}).values():
            if prepared is not None:
                book.add_item(
                    epub.EpubItem(
                        uid=f"img_{prepared.filename}", file_name=f"images/{prepared.filename}",
                        media_type=prepared.media_type, content=prepared.data,
                    )
                )
        for prepared in prepared_videos_by_chapter.get(ch.index, {}).values():
            if prepared is not None:
                book.add_item(
                    epub.EpubItem(
                        uid=f"img_{prepared.filename}", file_name=f"images/{prepared.filename}",
                        media_type=prepared.media_type, content=prepared.data,
                    )
                )

    colophon = epub.EpubHtml(title="Colofon", file_name="colophon.xhtml", lang="es")
    colophon.content = build_colophon_xhtml(chapters, source_commit, stats)
    colophon.add_item(css)
    book.add_item(colophon)

    book.toc = (
        epub.Link("prologue.xhtml", "Prologo y atribucion", "prologue"),
        (epub.Section(config.BOOK_TITLE), tuple(chapter_items)),
        epub.Link("colophon.xhtml", "Colofon", "colophon"),
    )
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["cover", "nav", prologue, *chapter_items, colophon]

    log.info("Writing %s", output_path)
    epub.write_epub(output_path, book, {})
