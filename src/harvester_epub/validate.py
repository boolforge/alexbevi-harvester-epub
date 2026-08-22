"""
Validates a finished .epub file. Two layers:

1. Structural sanity checks we can do with zero extra dependencies: the
   zip's first entry is an uncompressed "mimetype" file (an EPUB spec
   requirement that's easy to violate with a hand-rolled zip writer),
   container.xml and the OPF both parse as XML, and every manifest href
   actually exists in the archive (catches "book references an image that
   never got embedded" -- exactly the class of bug a silent `continue` in
   a scraping loop produces).

2. If a Java runtime is available, download and run the real w3c/epubcheck
   tool for a proper spec-conformance check. This is best-effort: its
   absence downgrades to structural-only validation with a clear warning,
   it never silently skips reporting what it did or didn't check.

Either layer raises ValidationError on a hard failure, and main.py exits
non-zero when that happens -- the direct fix for the original script never
giving CI a way to know the build actually produced a broken book.
"""
from __future__ import annotations

import logging
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from . import source

log = logging.getLogger("harvester_epub.validate")

EPUBCHECK_VERSION = "5.2.1"
EPUBCHECK_URL = (
    f"https://github.com/w3c/epubcheck/releases/download/v{EPUBCHECK_VERSION}"
    f"/epubcheck-{EPUBCHECK_VERSION}.zip"
)


class ValidationError(RuntimeError):
    pass


def validate_structure(epub_path: str) -> None:
    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()
        infos = zf.infolist()

        if not names or names[0] != "mimetype":
            raise ValidationError("'mimetype' must be the first entry in the zip archive")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise ValidationError("'mimetype' entry must be stored uncompressed (ZIP_STORED)")
        if zf.read("mimetype") != b"application/epub+zip":
            raise ValidationError("'mimetype' content must be exactly 'application/epub+zip'")

        if "META-INF/container.xml" not in names:
            raise ValidationError("Missing META-INF/container.xml")
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
        rootfile_el = container.find(".//c:rootfile", ns)
        if rootfile_el is None:
            raise ValidationError("container.xml has no <rootfile> entry")
        opf_path = rootfile_el.attrib["full-path"]
        if opf_path not in names:
            raise ValidationError(f"container.xml points at {opf_path!r}, which isn't in the archive")

        opf_root = ET.fromstring(zf.read(opf_path))
        opf_dir = str(Path(opf_path).parent)
        opf_ns = {"o": "http://www.idpf.org/2007/opf"}

        manifest_items = {
            item.attrib["id"]: item.attrib["href"]
            for item in opf_root.findall(".//o:manifest/o:item", opf_ns)
        }
        if not manifest_items:
            raise ValidationError("OPF manifest is empty")

        missing = []
        for item_id, href in manifest_items.items():
            resolved = href if opf_dir in (".", "") else f"{opf_dir}/{href}"
            if resolved not in names:
                missing.append((item_id, resolved))
        if missing:
            raise ValidationError(
                f"{len(missing)} manifest item(s) reference files that don't exist "
                f"in the archive, e.g. {missing[:5]}"
            )

        spine_idrefs = [
            el.attrib["idref"] for el in opf_root.findall(".//o:spine/o:itemref", opf_ns)
        ]
        if not spine_idrefs:
            raise ValidationError("OPF spine is empty -- the book has no reading order")
        dangling = [ref for ref in spine_idrefs if ref not in manifest_items]
        if dangling:
            raise ValidationError(f"Spine references unknown manifest id(s): {dangling}")

    log.info(
        "Structural validation passed: %d manifest items, %d spine entries",
        len(manifest_items), len(spine_idrefs),
    )


def _download_epubcheck(dest_dir: Path) -> Path | None:
    import io
    import zipfile as zf_module

    try:
        data = source.SESSION.get(EPUBCHECK_URL, timeout=60).content
    except Exception as exc:
        log.warning("Could not download epubcheck: %s", exc)
        return None
    try:
        with zf_module.ZipFile(io.BytesIO(data)) as z:
            z.extractall(dest_dir)
    except Exception as exc:
        log.warning("Could not unpack epubcheck: %s", exc)
        return None
    jar = dest_dir / f"epubcheck-{EPUBCHECK_VERSION}" / "epubcheck.jar"
    return jar if jar.exists() else None


def run_epubcheck(epub_path: str, work_dir: Path) -> bool:
    """
    Returns True if epubcheck ran and reported no errors, False if it ran
    and found errors. Returns True (with a warning logged) if Java or
    epubcheck itself isn't available -- absence of the extra checker is
    not, by itself, a build failure, but it's never silent about it.
    """
    java = subprocess.run(["which", "java"], capture_output=True, text=True)
    if java.returncode != 0:
        log.warning("Java not available: skipping epubcheck, relying on structural validation only")
        return True

    jar = _download_epubcheck(work_dir)
    if jar is None:
        log.warning("epubcheck unavailable: relying on structural validation only")
        return True

    result = subprocess.run(
        ["java", "-jar", str(jar), epub_path],
        capture_output=True, text=True, timeout=120,
    )
    output = result.stdout + result.stderr
    for line in output.splitlines():
        if "ERROR" in line or "FATAL" in line:
            log.error("epubcheck: %s", line)
        elif "WARNING" in line:
            log.warning("epubcheck: %s", line)
    if result.returncode != 0:
        log.error("epubcheck reported errors (exit code %d)", result.returncode)
        return False
    log.info("epubcheck: no errors")
    return True
