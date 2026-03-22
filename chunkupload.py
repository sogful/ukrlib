"""
Batch-add, commit, and push the working tree in chunks (max size + max file count).

Defaults: 100 MiB per commit, 5000 files per commit. Commit message prefix: "chunk upload".

Usage (from repo root):
  python chunk_upload.py
  python chunk_upload.py --dry-run
  python chunk_upload.py --no-push
  python chunk_upload.py --max-mib 100 --max-files 5000

Requires git user.name and user.email for commits. Remote should exist (e.g. origin).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run_git(repo: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
    )


def _has_head(repo: Path) -> bool:
    p = _run_git(repo, ["rev-parse", "--verify", "HEAD"], check=False)
    return p.returncode == 0


def _z_lines(data: bytes) -> list[str]:
    if not data:
        return []
    out = []
    for raw in data.split(b"\0"):
        if not raw:
            continue
        out.append(raw.decode("utf-8", errors="surrogateescape"))
    return out


def paths_to_commit(repo: Path) -> list[str]:
    """Paths that are not yet committed (untracked + all diffs vs HEAD or index)."""
    paths: set[str] = set()

    r = _run_git(repo, ["ls-files", "-z", "-o", "--exclude-standard"], check=False)
    paths.update(_z_lines(r.stdout))

    if _has_head(repo):
        r = _run_git(repo, ["diff", "--name-only", "-z", "HEAD"], check=False)
        paths.update(_z_lines(r.stdout))
    else:
        r = _run_git(repo, ["diff", "--cached", "--name-only", "-z"], check=False)
        paths.update(_z_lines(r.stdout))

    return sorted(paths)


def file_size(repo: Path, rel: str) -> int:
    return (repo / rel).stat().st_size


def build_batches(
    repo: Path,
    rel_paths: list[str],
    *,
    max_bytes: int,
    max_files: int,
) -> tuple[list[list[str]], list[str]]:
    """Return (batches of relative paths, skipped paths with reason)."""
    batches: list[list[str]] = []
    skipped: list[str] = []

    cur: list[str] = []
    cur_size = 0

    for rel in rel_paths:
        try:
            sz = file_size(repo, rel)
        except OSError as e:
            skipped.append(f"{rel} (unreadable: {e})")
            continue

        if sz > max_bytes:
            skipped.append(f"{rel} ({sz} bytes > per-chunk limit; use Git LFS or split)")
            continue

        if not cur:
            cur = [rel]
            cur_size = sz
            continue

        if len(cur) >= max_files or cur_size + sz > max_bytes:
            batches.append(cur)
            cur = [rel]
            cur_size = sz
        else:
            cur.append(rel)
            cur_size += sz

    if cur:
        batches.append(cur)

    return batches, skipped


def git_add_pathspec_nul(repo: Path, rel_paths: list[str]) -> None:
    """git add many paths without hitting OS argv limits."""
    if not rel_paths:
        return
    fd, path = tempfile.mkstemp(prefix="chunk_upload_pathspec_", suffix=".dat")
    try:
        with os.fdopen(fd, "wb") as f:
            for rel in rel_paths:
                f.write(Path(rel).as_posix().encode("utf-8", errors="surrogateescape"))
                f.write(b"\0")
        _run_git(
            repo,
            ["add", f"--pathspec-from-file={path}", "--pathspec-file-nul"],
            check=True,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def current_branch(repo: Path) -> str:
    p = _run_git(repo, ["branch", "--show-current"], check=True)
    name = p.stdout.decode("utf-8", errors="replace").strip()
    if not name:
        sys.exit("Detached HEAD or no branch; checkout a branch (e.g. main) first.")
    return name


def ensure_remote(repo: Path, remote: str) -> None:
    p = _run_git(repo, ["remote", "get-url", remote], check=False)
    if p.returncode != 0:
        sys.exit(f'Git remote "{remote}" is not configured. Add it, e.g.:\n'
                 f'  git remote add {remote} git@github.com:USER/ukrlib.git')


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunked git add/commit/push for large repos.")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Repository root (default: directory containing this script).",
    )
    parser.add_argument("--remote", default="origin", help="Remote name (default: origin).")
    parser.add_argument("--max-mib", type=float, default=100.0, help="Max total file bytes per chunk (default: 100).")
    parser.add_argument("--max-files", type=int, default=5000, help="Max files per chunk (default: 5000).")
    parser.add_argument("--dry-run", action="store_true", help="Print plan only; no git changes.")
    parser.add_argument("--no-push", action="store_true", help="Commit each chunk but do not push.")
    args = parser.parse_args()

    repo = args.repo.resolve()
    max_bytes = int(args.max_mib * 1024 * 1024)

    if not (repo / ".git").is_dir():
        sys.exit(f"Not a git repository: {repo}")

    all_paths = paths_to_commit(repo)
    if not all_paths:
        print("Nothing to commit (working tree clean relative to this script's rules).")
        return

    batches, skipped = build_batches(
        repo,
        all_paths,
        max_bytes=max_bytes,
        max_files=args.max_files,
    )

    for s in skipped:
        print(f"skip: {s}", file=sys.stderr)

    if not batches:
        print("No batches to commit after filtering.")
        return

    total_files = sum(len(b) for b in batches)
    print(f"Planned {len(batches)} commit(s), {total_files} file(s) total.")

    if args.dry_run:
        for i, batch in enumerate(batches, start=1):
            bsum = sum(file_size(repo, r) for r in batch)
            print(f"  batch {i}/{len(batches)}: {len(batch)} files, {bsum / (1024 * 1024):.2f} MiB")
        return

    ensure_remote(repo, args.remote)
    branch = current_branch(repo)

    for i, batch in enumerate(batches, start=1):
        msg = f"chunk upload ({i}/{len(batches)})"
        print(f"Adding {len(batch)} file(s) — {msg}")
        git_add_pathspec_nul(repo, batch)
        _run_git(repo, ["commit", "-m", msg], check=True)
        if not args.no_push:
            print(f"Pushing to {args.remote} ({branch})…")
            _run_git(repo, ["push", "-u", args.remote, branch], check=True)

    print("Done.")


if __name__ == "__main__":
    main()
