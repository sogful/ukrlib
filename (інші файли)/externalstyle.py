from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

scriptdir = Path(__file__).resolve().parent
projectroot = scriptdir.parent

targetdirs = (
    projectroot / "Література (literature)",
    projectroot / "Автори (authors)",
)

stylecss = projectroot / "style.css"

authorpageextracss = """

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

styleblockre = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)

stylesheetlinkre = re.compile(
    r"<link\b[^>]*\brel\s*=\s*[\"']stylesheet[\"'][^>]*(?:/?)>",
    re.IGNORECASE,
)

#*//////////////////////////////////////////////////////////////////////*#

def hreftostylecss(htmlpath: Path) -> str:
    return Path(os.path.relpath(stylecss, htmlpath.parent)).as_posix()

def isxhtmllike(html: str) -> bool:
    head = html[:4000]
    if re.match(r"<\?xml\b", head):
        return True
    return 'xmlns="http://www.w3.org/1999/xhtml"' in head or "XHTML" in head[:1500]

def linktag(href: str, xhtml: bool) -> str:
    core = f'<link rel="stylesheet" type="text/css" href="{href}"'
    return core + " />" if xhtml else core + ">"

def ensuresinglestylesheetlink(html: str, href: str) -> str:
    xhtml = isxhtmllike(html)
    tag = linktag(href, xhtml)
    matches = list(stylesheetlinkre.finditer(html))
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

    insertat = matches[0].start()
    without = html
    for m in reversed(matches):
        without = without[: m.start()] + without[m.end() :]
    return without[:insertat] + tag + "\n" + without[insertat:]

#*//////////////////////////////////////////////////////////////////////*#

def processhtml(path: Path) -> tuple[bool, str]:
    raw = path.read_text(encoding="utf-8", errors="surrogateescape")
    try:
        text = raw.encode("utf-8", errors="surrogateescape").decode("utf-8")
    except UnicodeError:
        text = raw

    textwostyle = styleblockre.sub("", text)

    href = hreftostylecss(path)
    normalized = ensuresinglestylesheetlink(textwostyle, href)

    return normalized != text, normalized

def appendauthorcssifneeded() -> bool:
    if not stylecss.is_file():
        return False
    existing = stylecss.read_text(encoding="utf-8", errors="replace")
    if "#imageoverlay" in existing:
        return False
    stylecss.write_text(
        existing.rstrip() + authorpageextracss + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return True

#*//////////////////////////////////////////////////////////////////////*#


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write changes to html files and append author css to style.css if needed.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="list each file (dry run or write); default is summary only.",
    )
    args = parser.parse_args()

    htmlpaths: list[Path] = []
    for base in targetdirs:
        if not base.is_dir():
            print(f"skip missing: {base}")
            continue
        htmlpaths.extend(sorted(base.rglob("*.html")))

    if args.write:
        if appendauthorcssifneeded():
            print(f"appended author page css to {stylecss}")
        else:
            print(f"author page css already present in (or missing file) {stylecss}")

    changedn = 0
    for p in htmlpaths:
        changed, newtext = processhtml(p)
        if not changed:
            continue
        changedn += 1
        rel = p.relative_to(projectroot)
        if args.verbose:
            print(f"{'updated' if args.write else 'would update'}: {rel}")
        if args.write:
            p.write_text(newtext, encoding="utf-8", newline="\n")

    if not args.write:
        print(f"\ndry run: {changedn} of {len(htmlpaths)} html files would change.")
        print("run with --write to apply.")
    else:
        print(f"\nwrote {changedn} html file(s).")

if __name__ == "__main__":
    main()
