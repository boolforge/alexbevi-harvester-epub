# alexbevi-harvester-epub

An unofficial EPUB compilation of Alex Bevilacqua's seven-part series
[*Reverse Engineering Harvester with Ghidra and Codex*](https://alexbevi.com/blog/2026/03/14/reverse-engineering-a-dos-game-with-ghidra-and-codex/),
built for offline reading.

**[Download the latest build &rarr;](https://boolforge.github.io/alexbevi-harvester-epub/)**

## What this is

Seven blog posts, one EPUB: real chapters, a working table of contents,
embedded screenshots, extracted preview frames where the original had
video (EPUB readers can't play video), and working links between parts of
the series. The text itself is untouched — same words, same code listings,
same typos — the only thing added is the packaging.

It rebuilds from the live source automatically (push, manual trigger, or
monthly via a scheduled Action), so it doesn't go stale if the author
edits a post or adds an eighth part.

## Attribution and license

All original text and images are © Alex Bevilacqua, licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), which explicitly
permits exactly this kind of reformatting and redistribution as long as
authorship is credited — which the book does, on the cover, in a prologue,
and again in a colophon listing every source URL and the exact commit of
the source repo the build was made from.

This is a reader-made compilation with no affiliation to the author. If
you're Alex and would rather this not exist in this form, open an issue.

The **code** in this repo (everything except the text it compiles) is
MIT-licensed — see [`LICENSE`](LICENSE).

## Why a repo instead of just a script

The obvious version of this task is "write a script that scrapes 7 URLs
and calls `ebooklib.write_epub()`." That version breaks in specific,
predictable ways — most of which only show up once you actually look at
the real page and the real output instead of assuming a couple of
`find()` calls will do:

- **Guessing the content container is fragile.** This blog runs the Jekyll
  "Chirpy" theme; the article body lives at `<article> > div.content`, a
  class name no generic `.post-content` guess would ever find. Falling back
  to `<article>` or `<main>` grabs the theme chrome around it too — nav,
  share buttons, tag lists, the license footer.
- **The rendered page lazy-loads images via `data-src`, not `src`.** A
  scraper that reads `img['src']` gets nothing, or a lazy-load placeholder.
- **The clean source is somewhere else entirely.** The blog is built from a
  public Jekyll repo, so the actual authored Markdown — no theme markup, no
  client-side rewrites — is one `raw.githubusercontent.com` request away.
  This build reads that directly and only falls back to scraping the
  rendered page if the repo is ever unreachable.
- **Markdown ≠ portable Markdown.** The source uses Jekyll-specific Liquid
  tags (`{% post_url %}`, `{% series_nav %}`) that a plain Markdown parser
  either chokes on or leaves as literal `{% ... %}` text in the output.
  These get resolved before rendering — including turning cross-references
  between parts of *this* series into real in-book links instead of round
  trips to the live site.
- **Not every fenced code block is code.** Most of this series' fenced
  blocks are prompt transcripts and raw hex/opcode listings with no
  language hint, not source in a language Pygments can identify. Guessing
  a lexer for those produces confident, wrong syntax coloring — so only
  fences with an explicit language (` ```python `, ` ```diff `, ...) get
  highlighted; everything else stays plain, deliberately.
- **A missing chapter shouldn't ship silently.** If a post's content
  can't be found, the build fails loudly with a non-zero exit code instead
  of logging a warning and producing an EPUB that's quietly missing a
  chapter. That distinction is the difference between CI catching a
  broken build and a reader finding out first.
- **"Looks right" and "valid EPUB3" are different bars.** The build runs
  the real [w3c/epubcheck](https://github.com/w3c/epubcheck) validator, not
  just a check that a file got written. Several real bugs only showed up
  this way during development — a couple of posts embed a raw `<style>`
  block directly in their Markdown (a site-specific CSS hack that's
  invalid as EPUB body content), and the auto-generated cover page needs
  to be marked reachable or a strict reader can flag it as broken.

None of this is exotic — it's the normal gap between "a script that runs
once on my machine" and "a pipeline that keeps working when the source
changes, in CI, without anyone watching it."

## Design notes

The cover and in-book styling lean on Ghidra's own brand teal and a
period-accurate amber phosphor accent instead of the reflexive "black
background, neon-green terminal" look — plus a hex-dump-style texture,
a real motif from the subject matter rather than decoration for its own
sake. Typography is IBM Plex Mono (headers, code, field-log framing) paired
with IBM Plex Serif (body text), both embedded in the EPUB under their
SIL Open Font License.

One constraint shaped the in-book CSS more than anything else: EPUB
readers let people choose day/night/sepia themes, and different reading
systems honor different subsets of a stylesheet. A hardcoded dark
`background-color` on `<body>` is a common way for an EPUB that looks
great in preview to render as pale grey text on a white page in practice.
So the vivid palette is used freely on the cover (a raster image, fully
under this project's control) and inside self-contained elements like
code blocks, while body text and page background are left to the reader's
own theme.

## Building locally

```bash
pip install -r requirements.txt
cd src
python -m harvester_epub.main -o ../reverse-engineering-harvester-with-ghidra-and-codex.epub
```

Needs `ffmpeg` on `PATH` for video poster frames, and the IBM Plex fonts
installed system-wide (`apt install fonts-ibm-plex fonts-jetbrains-mono`
on Debian/Ubuntu) for the cover and embedded EPUB fonts. Both are missing,
the build degrades gracefully — the code path always has a design reason
for its fallback documented next to it. `--skip-epubcheck` skips the
Java-based validator if you don't have a JRE handy; structural validation
always runs regardless.

## Project layout

```
src/harvester_epub/
  config.py       book metadata, source repo, visual identity tokens
  source.py       networking: fetch source repo content, fall back to live site
  content.py      Markdown -> HTML, Liquid tag resolution, chapter post-processing
  media.py        image/video download, poster-frame extraction, cover generation
  epub_build.py   ebooklib assembly: stylesheet, fonts, prologue, colophon, TOC
  validate.py     structural checks + epubcheck
  main.py         CLI orchestration
site/index.html   the GitHub Pages download page
.github/workflows/build.yml   builds + validates + deploys to Pages
```
