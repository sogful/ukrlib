import argparse
import random
import re
from pathlib import Path
from typing import Dict, List, Tuple

scriptdir = Path(__file__).resolve().parent
projectroot = scriptdir.parent

pagefolders = {
    "ukrlit": [
        "Українська література (ukrainian literature)",
        "ukrainianliterature",
    ],
    "forlit": [
        "Зарубіжна література (foreign literature)",
        "foreignliterature",
    ],
    "ukrauth": [
        "Українські автори (ukrainian authors)",
        "ukrainianauthors",
    ],
    "forauth": [
        "Зарубіжні автори (foreign authors)",
        "foreignauthors",
    ],
}

protectedblockre = re.compile(
    r"(<(script|style|pre|textarea)\b[^>]*>.*?</\2\s*>)",
    flags=re.IGNORECASE | re.DOTALL,
)
commentre = re.compile(r"<!--(?!\[if\b)(?!<!)(.*?)-->", flags=re.IGNORECASE | re.DOTALL)
betweentagswsre = re.compile(r">\s+<", flags=re.DOTALL)
multiwsre = re.compile(r"[ \t]{2,}")

#*//////////////////////////////////////////////////////////////////////*#

def resolvepageroots(base: Path) -> Dict[str, Path]:
    roots = {}
    for key, names in pagefolders.items():
        for name in names:
            p = base / name
            if p.exists() and p.is_dir():
                roots[key] = p
                break
    return roots

def htmlfilesinroot(root: Path) -> List[Path]:
    return sorted(root.rglob("*.html"))

def protectblocks(text: str) -> Tuple[str, List[str]]:
    blocks = []
    def repl(m):
        blocks.append(m.group(1))
        return f"__htmlminkeep{len(blocks)-1}__"
    return protectedblockre.sub(repl, text), blocks

def restoreblocks(text: str, blocks: List[str]) -> str:
    for i, block in enumerate(blocks):
        text = text.replace(f"__htmlminkeep{i}__", block)
    return text

def conservativeminifyhtml(text: str) -> str:
    protected, blocks = protectblocks(text)
    protected = commentre.sub("", protected)
    protected = betweentagswsre.sub("><", protected)
    protected = multiwsre.sub(" ", protected)
    protected = "\n".join(line.rstrip() for line in protected.splitlines())
    protected = protected.strip() + "\n"
    return restoreblocks(protected, blocks)

#*//////////////////////////////////////////////////////////////////////*#

def stratifiedsample(filesbygroup: Dict[str, List[Path]], n: int, rng: random.Random) -> List[Path]:
    allfiles = [p for files in filesbygroup.values() for p in files]
    if len(allfiles) <= n:
        return allfiles
    groups = [g for g in filesbygroup if filesbygroup[g]]
    if not groups:
        return []
    per = n // len(groups)
    sample = []
    leftovers = []
    for g in groups:
        files = filesbygroup[g][:]
        rng.shuffle(files)
        take = min(per, len(files))
        sample.extend(files[:take])
        leftovers.extend(files[take:])
    remaining = n - len(sample)
    if remaining > 0 and leftovers:
        rng.shuffle(leftovers)
        sample.extend(leftovers[:remaining])
    return sample

def analyze(paths: List[Path]) -> Tuple[int, int]:
    before = 0
    after = 0
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        mini = conservativeminifyhtml(text)
        before += len(text.encode("utf-8", errors="ignore"))
        after += len(mini.encode("utf-8", errors="ignore"))
    return before, after

#*//////////////////////////////////////////////////////////////////////*#

def main():
    parser = argparse.ArgumentParser(
        description="conservatively minify html files with preview estimate and confirmation."
    )
    parser.add_argument(
        "--root",
        default=str(projectroot),
        help="project root (default: one directory above script folder)."
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=100,
        help="random sample size for estimate (default: 100)."
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="optional random seed for reproducible sampling."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="apply changes without confirmation prompt."
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_absolute():
        root = (projectroot / root).resolve()

    roots = resolvepageroots(root)
    missing = [k for k in pagefolders if k not in roots]
    if missing:
        print(f"[error] missing expected page folders: {', '.join(missing)}")
        return

    filesbygroup = {k: htmlfilesinroot(v) for k, v in roots.items()}
    allfiles = [p for files in filesbygroup.values() for p in files]
    if not allfiles:
        print("[error] no html files found in page folders.")
        return

    rng = random.Random(args.seed)
    sample = stratifiedsample(filesbygroup, max(1, args.sample_size), rng)
    samplebefore, sampleafter = analyze(sample)
    samplesaved = max(0, samplebefore - sampleafter)
    sampleratio = (samplesaved / samplebefore) if samplebefore else 0.0

    totalsize = sum(p.stat().st_size for p in allfiles if p.exists())
    estsaved = int(totalsize * sampleratio)
    estafter = max(0, totalsize - estsaved)

    print(f"[scan] page folders: {len(roots)}")
    for key in ("ukrlit", "forlit", "ukrauth", "forauth"):
        print(f"[scan] {key}: {len(filesbygroup[key])} html files")
    print(f"[scan] total html files: {len(allfiles)}")
    print(f"[estimate] sample size: {len(sample)}")
    print(f"[estimate] sample save: {samplesaved / (1024*1024):.2f} mib ({sampleratio*100:.2f}%)")
    print(
        f"[estimate] approx total: {totalsize / (1024*1024):.2f} mib -> "
        f"{estafter / (1024*1024):.2f} mib (save ~{estsaved / (1024*1024):.2f} mib)"
    )

    if not args.yes:
        answer = input(f"apply conservative minification to {len(allfiles)} files? [y/n]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("[abort] no files were modified.")
            return

    changed = 0
    bytessaved = 0
    for p in allfiles:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        mini = conservativeminifyhtml(text)
        old = len(text.encode("utf-8", errors="ignore"))
        new = len(mini.encode("utf-8", errors="ignore"))
        if new < old and mini != text:
            p.write_text(mini, encoding="utf-8")
            changed += 1
            bytessaved += (old - new)

    print(f"[done] updated files: {changed}/{len(allfiles)}")
    print(f"[done] bytes saved: {bytessaved} (~{bytessaved / (1024*1024):.2f} mib)")

if __name__ == "__main__":
    main()

