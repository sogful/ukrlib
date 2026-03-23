import argparse
import re
from pathlib import Path

scriptdir = Path(__file__).resolve().parent
projectroot = scriptdir.parent

sidecolre = re.compile(
    r'(<div class="side-column">)(.*?)(</div>)',
    flags=re.DOTALL | re.IGNORECASE,
)
imgre = re.compile(r"<img\b[^>]*?/?>", flags=re.IGNORECASE | re.DOTALL)

#*//////////////////////////////////////////////////////////////////////*#

def getattrval(tag, attr):
    m = re.search(
        rf"""\b{re.escape(attr)}\s*=\s*(["'])(.*?)\1""",
        tag,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return ""
    return m.group(2).strip()


def dedupesidecol(content):
    seen = set()
    removed = 0
    out = []
    last = 0

    for m in imgre.finditer(content):
        start, end = m.span()
        tag = m.group(0)
        src = getattrval(tag, "src")
        data_full = getattrval(tag, "data-full")
        key = (src, data_full)

        out.append(content[last:start])
        if key in seen:
            removed += 1
        else:
            seen.add(key)
            out.append(tag)
        last = end
    out.append(content[last:])
    return "".join(out), removed

def dedupehtml(text):
    total_removed = 0
    segs = []
    last = 0

    for m in sidecolre.finditer(text):
        start, end = m.span()
        open_div, content, close_div = m.groups()
        new_content, removed = dedupesidecol(content)
        total_removed += removed

        segs.append(text[last:start])
        segs.append(open_div + new_content + close_div)
        last = end

    segs.append(text[last:])
    return "".join(segs), total_removed

def findtargets(root):
    return (p / "index.html" for p in root.iterdir() if p.is_dir())


def defaultauthorroot():
    candidates = [
        projectroot / "ukrainianauthors",
        projectroot / "Українські автори (ukrainian authors)",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]

#*//////////////////////////////////////////////////////////////////////*#

def main():
    parser = argparse.ArgumentParser(
        description="remove duplicate side-thumb images from exported author pages",
    )
    parser.add_argument(
        "--root",
        default=str(defaultauthorroot()),
        help="author root directory (default: ../ukrainianauthors from script folder)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="apply changes without confirmation prompt",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_absolute():
        root = (projectroot / root).resolve()
    if not root.exists():
        print(f"[error] root directory not found: {root}")
        return

    scanned = 0
    candidates = []
    total_removed = 0

    for path in findtargets(root):
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        new_text, removed = dedupehtml(text)
        if removed > 0 and new_text != text:
            candidates.append((path, new_text, removed))
            total_removed += removed

    print(f"[scan] scanned author files: {scanned}")
    print(f"[scan] files with duplicates: {len(candidates)}")
    print(f"[scan] duplicate <img> tags to remove: {total_removed}")

    if not candidates:
        print("[done] nothing to fix.")
        return

    if not args.yes:
        answer = input(f"apply fixes to {len(candidates)} files? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("[abort] no files were modified.")
            return

    changed = 0
    for path, new_text, _ in candidates:
        path.write_text(new_text, encoding="utf-8")
        changed += 1

    print(f"[done] updated files: {changed}")

#*//////////////////////////////////////////////////////////////////////*#

if __name__ == "__main__":
    main()

