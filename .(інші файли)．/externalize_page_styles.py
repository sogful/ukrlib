"""
Replace inline <style> blocks in HTML under «Література (literature)» and
«Автори (authors)» with a single <link> to the repo root style.css.

Normalizes any existing stylesheet <link> (styles.css, /style.css, etc.) to a
relative href pointing at project root style.css.

Run without --write to preview; use --write to modify files and update style.css.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

SCRIPTDIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTDIR.parent

TARGET_DIRS = (
    PROJECT_ROOT / "Література (literature)",
    PROJECT_ROOT / "Автори (authors)",
)

STYLE_CSS = PROJECT_ROOT / "style.css"

# CSS that was inline on author biography pages (image lightbox).
AUTHOR_PAGE_EXTRA_CSS = """

/* Author biography pages — image lightbox */
#imageoverlay {
    transition: opacity 0.25s ease;
    opacity: 0;
    pointer-events: none;
}
#imageoverlay.open {
    opacity: 1;
    pointer-events: auto;
}
#imageoverlay.fade {
    opacity: 0;
    pointer-events: none;
}
"""

STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)

# <link ... rel="stylesheet" ...> (single line; sufficient for these archives)
STYLESHEET_LINK_RE = re.compile(
    r"<link\b[^>]*\brel\s*=\s*[\"']stylesheet[\"'][^>]*(?:/?)>",
    re.IGNORECASE,
)


def href_to_style_css(html_path: Path) -> str:
    return Path(os.path.relpath(STYLE_CSS, html_path.parent)).as_posix()


def is_xhtml_like(html: str) -> bool:
    head = html[:4000]
    if re.match(r"<\?xml\b", head):
        return True
    return 'xmlns="http://www.w3.org/1999/xhtml"' in head or "XHTML" in head[:1500]


def link_tag(href: str, xhtml: bool) -> str:
    core = f'<link rel="stylesheet" type="text/css" href="{href}"'
    return core + " />" if xhtml else core + ">"


def ensure_single_stylesheet_link(html: str, href: str) -> str:
    xhtml = is_xhtml_like(html)
    tag = link_tag(href, xhtml)
    matches = list(STYLESHEET_LINK_RE.finditer(html))
    if not matches:
        def inject(m: re.Match[str]) -> str:
            return m.group(1) + "\n" + tag + "\n" + m.group(2)

        out, n = re.subn(
            r"(<head\b[^>]*>)(\s*)",
            inject,
            html,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if n:
            return out
        out, n = re.subn(
            r"(</head\s*>)",
            tag + "\n" + r"\1",
            html,
            count=1,
            flags=re.IGNORECASE,
        )
        return out if n else html

    insert_at = matches[0].start()
    without = html
    for m in reversed(matches):
        without = without[: m.start()] + without[m.end() :]
    return without[:insert_at] + tag + "\n" + without[insert_at:]


def process_html(path: Path) -> tuple[bool, str]:
    raw = path.read_text(encoding="utf-8", errors="surrogateescape")
    try:
        text = raw.encode("utf-8", errors="surrogateescape").decode("utf-8")
    except UnicodeError:
        text = raw

    text_wo_style = STYLE_BLOCK_RE.sub("", text)

    href = href_to_style_css(path)
    normalized = ensure_single_stylesheet_link(text_wo_style, href)

    return normalized != text, normalized


def append_author_css_if_needed() -> bool:
    if not STYLE_CSS.is_file():
        return False
    existing = STYLE_CSS.read_text(encoding="utf-8", errors="replace")
    if "#imageoverlay" in existing:
        return False
    STYLE_CSS.write_text(
        existing.rstrip() + AUTHOR_PAGE_EXTRA_CSS + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write changes to HTML files and append author CSS to style.css if needed.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="List each file (dry run or write); default is summary only.",
    )
    args = parser.parse_args()

    html_paths: list[Path] = []
    for base in TARGET_DIRS:
        if not base.is_dir():
            print(f"skip missing: {base}")
            continue
        html_paths.extend(sorted(base.rglob("*.html")))

    if args.write:
        if append_author_css_if_needed():
            print(f"Appended author page CSS to {STYLE_CSS}")
        else:
            print(f"Author page CSS already present in (or missing file) {STYLE_CSS}")

    changed_n = 0
    for p in html_paths:
        changed, new_text = process_html(p)
        if not changed:
            continue
        changed_n += 1
        rel = p.relative_to(PROJECT_ROOT)
        if args.verbose:
            print(f"{'updated' if args.write else 'would update'}: {rel}")
        if args.write:
            p.write_text(new_text, encoding="utf-8", newline="\n")

    if not args.write:
        print(f"\nDry run: {changed_n} of {len(html_paths)} HTML files would change.")
        print("Run with --write to apply.")
    else:
        print(f"\nWrote {changed_n} HTML file(s).")


if __name__ == "__main__":
    main()
