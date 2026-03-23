import argparse
import asyncio
import os
import random
import re
from io import BytesIO
from pathlib import Path
import html as html
from typing import Optional, Tuple, List, Dict

import aiohttp
from bs4 import BeautifulSoup
from PIL import Image

# /bio/ or /bio-zl/
basesite = "https://www.ukrlib.com.ua"
scriptdir = Path(__file__).resolve().parent
projroot = scriptdir.parent


def sectionlinks(section: str) -> Dict[str, str]:
    root = f"{basesite}/{section}/"
    return {
        "index": root + "index.php",
        "author": root + "author.php",
        "printit": root + "printit.php",
    }


def inferfromoutdir(out_path: str) -> str:
    low = out_path.replace("\\", "/").lower()
    if "foreign" in low or "зарубіжн" in out_path:
        return "bio-zl"
    if "ukrainian" in low or "українськ" in out_path:
        return "bio"
    return "bio"


def defaultauthoroutdir() -> Path:
    candidates = [
        projroot / "Автори (authors)" / "Зарубіжні автори (foreign authors)",
        projroot / "Автори (authors)" / "Українські автори (ukrainian authors)",
        projroot / "ukrainianauthors",
        projroot / "Українські автори (ukrainian authors)",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]

disallowedchar = {
    "<": "＜", ">": "＞", 
    ":": "：", '"': "＂", 
    "/": "／", "\\": "＼", 
    "|": "｜", "?": "？", 
    "*": "＊"
}

def safefilename(name: str) -> str:
    name = name.replace('\r', ' ').replace('\n', ' ')
    name = re.sub(r"[\x00-\x1f\x7f]", " ", name)
    name = ''.join(disallowedchar.get(c, c) for c in name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(" .").strip()
    return name or "unknown"

def styleblock() -> str:
    # copied from the usual export file, except with side portraits!
    return r"""
        .wrap{margin:0 auto;max-width:980px}
        p{text-indent:30px;margin:0px;padding:0px}
        .page-title{text-align:center;display:block;clear:both;margin:0;padding:0 20px 20px}
        .page-title h1{font-size:32px;line-height:32px;font-weight:bold;margin-bottom:20px}
        .page-title h2{font-size:20px;line-height:20px;font-weight:bold}
        .pt-normal h2{font-weight:normal}
        h1{padding-bottom:0px;line-height:auto;text-align:center;font-family:Verdana,sans-serif;}
        h2{text-align:center;font-family:Verdana,sans-serif;}
        .prose{text-align:justify;font:16px/24px Georgia,'Times New Roman',Serif;color:#000;}
        .prose blockquote{text-align:right;margin-bottom:20px}
        .cita{padding-left:40px!important;font-style:italic!important;margin-top:20px!important;margin-bottom:20px!important}
        .cita p{color:#737373!important;font-style:italic!important}
        .side-layout{display:flex;gap:24px;align-items:flex-start;}
        .side-column{flex:0 0 auto;width:140px;display:flex;flex-direction:column;gap:12px;}
        .side-img{width:100%;height:auto;margin-top:0;}
        .side-thumb{cursor:pointer;}
        .prose-text{flex:1 1 auto;min-width:0;}
        @media (max-width: 700px){
            .side-layout{flex-direction:column;gap:12px;}
            .side-column{width:220px;margin:0 auto;}
        }
        .imageoverlay{position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;visibility:hidden;}
        .imageoverlay::before{content:'';position:absolute;inset:0;background:rgba(0,0,0,0.75);opacity:0;transition:opacity 200ms ease;}
        .imageoverlay.open{visibility:visible;}
        .imageoverlay.open::before{opacity:1;}
        .imageoverlaypic{position:relative;max-width:80vw;max-height:80vh;width:auto;height:auto;object-fit:contain;cursor:pointer;opacity:0;transition:opacity 200ms ease;}
        .imageoverlay.open .imageoverlaypic{opacity:1;}
    """.strip()

#*//////////////////////////////////////////////////////////////////////*#

def parsepage(htmltext: str) -> dict:
    soup = BeautifulSoup(htmltext, "lxml")

    real_name_tag = soup.select_one('div.breadcrumbs a[href^="author.php"]')
    real_name = real_name_tag.get_text(strip=True) if real_name_tag else None

    h2 = soup.select_one("div.page-title h2")
    page_h2 = h2.get_text(strip=True) if h2 else "Біографія"

    article = soup.select_one("article#content")
    if not article:
        raise ValueError("no article#content")
    ps = article.find_all("p", recursive=False)
    if len(ps) < 3:
        raise ValueError(f"not enough p's ({len(ps)})")

    author_short = ps[0].get_text(" ", strip=True)
    years = ps[1].get_text(" ", strip=True)
    bio_text = " ".join(p.get_text(" ", strip=True) for p in ps[2:]).strip()
    bio_text = re.sub(r"\s+", " ", bio_text)

    def imgsrc2full(x: str) -> str:
        if x.startswith("/my/images/full/"):
            return x
        b = os.path.basename(x)
        if x.startswith("/my/images/crop/") and b.startswith("crop_"):
            b = b[len("crop_") :]
        elif x.startswith("/my/images/medium/") and b.startswith("md_"):
            b = b[len("md_") :]
        return "/my/images/full/" + b

    imgs = []
    for img in soup.select("div.post-right-zagolovok img"):
        src = (img.get("src") or "").strip()
        if src.startswith("/my/images/crop/") or src.startswith("/my/images/medium/") or src.startswith("/my/images/full/"):
            alt = (img.get("alt") or "").strip() or (img.get("title") or "").strip()
            imgs.append({"full_src": imgsrc2full(src), "alt": alt})

    all_imgs = []
    seen = set()
    for d in imgs:
        if d["full_src"] in seen:
            continue
        seen.add(d["full_src"])
        all_imgs.append(d)

    if not real_name:
        h1 = soup.select_one("h1")
        real_name = h1.get_text(strip=True) if h1 else author_short

    if isinstance(real_name, str) and "Помилка 400" in real_name:
        raise ValueError("HTTP 400")

    return {
        "real_name": real_name,
        "page_h2": page_h2,
        "author_short": author_short,
        "years": years,
        "bio_text": bio_text,
        "images": all_imgs,
    }

#*//////////////////////////////////////////////////////////////////////*#

def exporthtml(real_name, page_h2, author_short, years, bio_text, images):
    title = html.escape(f"{real_name} — {page_h2}")
    image_tags = "".join(
        (
            f'<img class="side-img side-thumb" src="./{html.escape(img["file"])}" '
            f'alt="{html.escape(img["alt"])}" title="{html.escape(img["alt"])}" '
            f'data-full="./{html.escape(img["file"])}" />'
        )
        for img in images
    )
    leftcol = f'<div class="side-column">{image_tags}</div>' if image_tags else ""

    style = styleblock()
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <link rel="stylesheet" type="text/css" href="/style.css">
</head>
<body>
  <div class="wrap">
    <div class="page-title">
      <h1>{html.escape(real_name)}</h1>
      <h2>{html.escape(page_h2)}</h2>
    </div>
    <div class="prose" id="content">
      <div class="side-layout">
        {leftcol}
        <div class="prose-text">
          <p>{html.escape(author_short)}</p>
          <p>{html.escape(years)}</p>
          <p>{html.escape(bio_text)}</p>
        </div>
      </div>
    </div>
    <div id="imageoverlay" class="imageoverlay" aria-hidden="true">
      <img id="imageoverlaypic" class="imageoverlaypic" src="" alt="" />
    </div>
    <script>
      (function() {{
        const overlay = document.getElementById('imageoverlay');
        const overlaypic = document.getElementById('imageoverlaypic');
        if (!overlay || !overlaypic) return;

        function closeimage() {{
          overlay.classList.add('fade');
          overlay.addEventListener('transitionend', whentransitionends);
          overlay.setAttribute('aria-hidden', 'true');
          overlaypic.src = '';
        }}
        function whentransitionends(e) {{
          if (e.target === overlay) {{
            overlay.classList.remove('open');
            overlay.classList.remove('fade');
            overlay.removeEventListener('transitionend', whentransitionends);
          }}
        }}
        function expandimage(fullsrc) {{
          if (!fullsrc) return;
          overlaypic.src = fullsrc;
          overlay.classList.add('open');
          overlay.classList.remove('fade');
          overlay.setAttribute('aria-hidden', 'false');
        }}
        overlay.addEventListener('click', function() {{
          closeimage();
        }});
        overlaypic.addEventListener('click', function(e) {{
          e.stopPropagation();
          if (overlaypic && overlaypic.src) {{
            window.open(overlaypic.src, '_blank', 'noopener');
          }}
        }});
        document.querySelectorAll('.side-thumb').forEach(function(img) {{
          img.addEventListener('click', function(e) {{
            e.preventDefault();
            e.stopPropagation();
            const full = img.getAttribute('data-full');
            expandimage(full);
          }});
        }});
      }})();
    </script>
    <style>
      #imageoverlay {{
        transition: opacity 0.25s ease;
        opacity: 0;
        pointer-events: none;
      }}
      #imageoverlay.open {{
        opacity: 1;
        pointer-events: auto;
      }}
      #imageoverlay.fade {{
        opacity: 0;
        pointer-events: none;
      }}
    </style>
  </div>
</body>
</html>
"""

#*//////////////////////////////////////////////////////////////////////*#

async def downloadifneeded(session, url, dest):
    if os.path.exists(dest):
        return True
    async with session.get(url) as resp:
        if resp.status != 200:
            return False
        b = await resp.read()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b)
    return True

async def downloadbytes(session, url):
    async with session.get(url) as resp:
        if resp.status != 200:
            return None
        return await resp.read()

# compression is relatively loose to not ruin the looks
def img2webp(imgbytes, dest, *, maxwidth, quality, method):
    with Image.open(BytesIO(imgbytes)) as im:
        im.load()
        im = im.convert("RGB")
        if maxwidth and im.width > maxwidth:
            h = int(im.height * (maxwidth / im.width))
            im = im.resize((maxwidth, h), resample=Image.LANCZOS)
        im.save(dest, "WEBP", quality=quality, method=method)
    return True

async def downloadfull2webp(
    session, url, dest, *, maxwidth=700, quality=45, method=6, force=False
):
    if not force and os.path.exists(dest):
        return os.path.basename(dest)
    imgbytes = await downloadbytes(session, url)
    if not imgbytes:
        return None
    await asyncio.to_thread(img2webp, imgbytes, dest, maxwidth=maxwidth, quality=quality, method=method)
    return os.path.basename(dest)

#*//////////////////////////////////////////////////////////////////////*#

async def fetchhtml(session, printit_url: str, tid, sema, maxtry=2):
    p = {"tid": tid}
    for attempt in range(1, maxtry+1):
        async with sema:
            try:
                async with session.get(printit_url, params=p) as resp:
                    if resp.status == 200:
                        b = await resp.read()
                        charset = resp.charset or "utf-8"
                        return "success", b.decode(charset, errors="replace")
                    if resp.status == 429:
                        return "ratelimited", None
                    if 400 <= resp.status < 500:
                        return "clienterror", None
                    return "othererror", None
            except Exception:
                if attempt == maxtry:
                    return "exception", None
                await asyncio.sleep(2 * attempt)
    return "exception", None

async def fetchtext(session, url, sema, params=None, maxtry=2):
    for attempt in range(1, maxtry + 1):
        async with sema:
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        b = await resp.read()
                        charset = resp.charset or "utf-8"
                        return "success", b.decode(charset, errors="replace")
                    if resp.status == 429:
                        return "ratelimited", None
                    if 400 <= resp.status < 500:
                        return "clienterror", None
                    return "othererror", None
            except Exception:
                if attempt == maxtry:
                    return "exception", None
                await asyncio.sleep(2 * attempt)
    return "exception", None

def parseauthorids(index_html: str) -> List[int]:
    ids = [int(x) for x in re.findall(r"author\.php\?id=(\d+)", index_html)]
    return sorted(set(ids))

def parseprinttids(author_html: str) -> Optional[int]:
    matches = re.findall(r"printit\.php\?tid=(\d+)", author_html)
    if not matches:
        return None
    return int(matches[0])

async def discover_tids(session, sema, concurrency: int, section: str):
    urls = sectionlinks(section)
    indexurl = urls["index"]
    authorurl = urls["author"]

    allauthorids = set()
    previousids = None
    page = 1
    hard_limit = 5000
    while page <= hard_limit:
        status, text = await fetchtext(session, indexurl, sema, params={"page": page})
        if status == "ratelimited":
            print("[discover] rate limited on listing pages, waiting 60s")
            await asyncio.sleep(60)
            continue
        if status != "success" or not text:
            break

        ids = parseauthorids(text)
        if not ids:
            break
        currentids = tuple(ids)
        if previousids is not None and currentids == previousids:
            break

        allauthorids.update(ids)
        if page == 1 or page % 10 == 0:
            print(f"[discover] scanned listing page {page}")
        previousids = currentids
        page += 1

    total_pages = page - 1
    print(f"[discover] listing pages: 1..{total_pages}")

    authorids = sorted(allauthorids)
    print(f"[discover] unique author ids: {len(authorids)}")
    print(f"[discover] resolving author ids -> print tids ({len(authorids)} authors)")

    all_tids = set()
    i = 0
    resolved = 0
    while i < len(authorids):
        batch = []
        for off in range(concurrency):
            j = i + off
            if j >= len(authorids):
                break
            aid = authorids[j]
            batch.append(fetchtext(session, authorurl, sema, params={"id": aid}))
        if not batch:
            break
        results = await asyncio.gather(*batch)
        for status, text in results:
            if status == "success" and text:
                tid = parseprinttids(text)
                if tid is not None:
                    all_tids.add(tid)
            resolved += 1
        if resolved % max(25, concurrency) == 0 or resolved >= len(authorids):
            print(f"[discover] resolved author pages: {resolved}/{len(authorids)} (tids={len(all_tids)})")
        if any(status == "ratelimited" for status, _ in results):
            print("[discover] rate limited on author pages, waiting 60s")
            await asyncio.sleep(60)
        i += concurrency

    tids = sorted(all_tids)
    print(f"[discover] unique print tids: {len(tids)}")
    return tids

async def processtid(session, sema, tid, outdir, *, images_only=False, printit_url: str):
    status, text = await fetchhtml(session, printit_url, tid, sema)
    if status != "success" or not text:
        return status, tid
    try:
        p = parsepage(text)
    except Exception:
        return "parseerror", tid

    real_name = str(p["real_name"])
    page_h2 = str(p["page_h2"])
    author_short = str(p["author_short"])
    years = str(p["years"])
    bio_text = str(p["bio_text"])
    author_dir = os.path.join(outdir, safefilename(real_name))
    if images_only and not os.path.isdir(author_dir):
        return "skipped", tid

    os.makedirs(author_dir, exist_ok=True)
    indexfile = os.path.join(author_dir, "index.html")

    images = []
    def url2webpfile(src):
        base = os.path.basename(src)
        root, _ = os.path.splitext(base)
        return safefilename(root) + ".webp"

    for img in (p.get("images") if isinstance(p.get("images"), list) else []):
        if not isinstance(img, dict):
            continue
        fullsrc = img.get("full_src")
        alt = (img.get("alt") or author_short).strip()
        if not fullsrc:
            continue
        webp = url2webpfile(fullsrc)
        saved = await downloadfull2webp(
            session,
            url="https://www.ukrlib.com.ua" + fullsrc,
            dest=os.path.join(author_dir, webp),
            maxwidth=700,
            quality=45,
            method=6,
            force=images_only,
        )
        if saved:
            images.append({"file": saved, "alt": alt})

    if not images_only:
        htmlout = exporthtml(real_name, page_h2, author_short, years, bio_text, images)
        with open(indexfile, "w", encoding="utf-8") as f:
            f.write(htmlout)

    label = f"webp-only tid={tid} -> {author_dir}" if images_only else f"tid={tid} -> {indexfile}"
    print(f"[ok] {label}")
    return "success", tid

#*//////////////////////////////////////////////////////////////////////*#

async def run(args):
    os.makedirs(args.out_base_dir, exist_ok=True)
    section = args.section or inferfromoutdir(args.out_base_dir)
    printit_url = sectionlinks(section)["printit"]
    print(f"[run] ukrlib section=/{section}/ printit={printit_url}")

    conn = aiohttp.TCPConnector(limit=None)
    to = aiohttp.ClientTimeout(total=60)
    sema = asyncio.Semaphore(args.concurrency)
    async with aiohttp.ClientSession(connector=conn, timeout=to) as session:
        foundtids = await discover_tids(session, sema, args.concurrency, section)
        if not foundtids:
            print("[run] no tids discovered")
            return

        startid = max(1, args.startid)
        endid = args.endid if args.endid and args.endid > 0 else len(foundtids)
        endid = min(endid, len(foundtids))
        if startid > endid:
            print(f"[run] invalid slice: start={startid}, end={endid}")
            return

        tids = foundtids[startid - 1:endid]
        print(f"[run] tids selected={len(tids)} (slice {startid}..{endid} of {len(foundtids)})")

        if args.test:
            tid = random.choice(tids)
            print(f"[test] one random tid={tid}")
            status, tid = await processtid(
                session, sema, tid, args.out_base_dir,
                images_only=args.images_only,
                printit_url=printit_url,
            )
            print(f"[test] status={status} tid={tid}")
            return

        i = 0
        done = 0
        success_total = 0
        skipped_total = 0
        while i < len(tids):
            batch = []
            for off in range(args.concurrency):
                j = i + off
                if j >= len(tids):
                    break
                batch.append(
                    processtid(
                        session, sema, tids[j], args.out_base_dir,
                        images_only=args.images_only,
                        printit_url=printit_url,
                    )
                )
            if not batch:
                break
            results = await asyncio.gather(*batch)
            hit = any(s == "ratelimited" for s, _ in results)
            done += len(results)
            success_total += sum(1 for s, _ in results if s == "success")
            skipped_total += sum(1 for s, _ in results if s == "skipped")
            if done % max(25, args.concurrency) == 0 or done >= len(tids):
                success = sum(1 for s, _ in results if s == "success")
                skipped = sum(1 for s, _ in results if s == "skipped")
                extra = f" skipped={skipped}" if args.images_only else ""
                print(
                    f"[run] progress {done}/{len(tids)} (last batch success={success}/{len(results)}{extra})"
                )
            if hit:
                print("rate limited, waiting 60s")
                await asyncio.sleep(60)
            i += args.concurrency

        print(
            f"[run] finished: success={success_total}, skipped={skipped_total}, total={len(tids)}"
        )

#*//////////////////////////////////////////////////////////////////////*#

def main():
    parser = argparse.ArgumentParser(description="local export for ukrlib bio pages (with side images)")
    parser.add_argument("--start", dest="startid", type=int, default=1, help="start index in discovered tids (1-based)")
    parser.add_argument("--end", dest="endid", type=int, default=None, help="end index in discovered tids (inclusive)")
    parser.add_argument(
        "--out",
        dest="out_base_dir",
        default=str(defaultauthoroutdir()),
        help="output base folder (default: ../ukrainianauthors from script folder)",
    )
    parser.add_argument("--concurrency", type=int, default=8, help="max concurrent")
    parser.add_argument("--test", "-t", action="store_true", help="process one random discovered tid only")
    parser.add_argument(
        "--images-only",
        action="store_true",
        help="re-download and overwrite .webp side portraits only; do not write index.html",
    )
    parser.add_argument(
        "--section",
        choices=("bio", "bio-zl"),
        default=None,
        help="ukrlib bio area: bio=Ukrainian (/bio/), bio-zl=worldwide (/bio-zl/). "
        "Default: inferred from --out (foreign→bio-zl, ukrainian→bio).",
    )
    args = parser.parse_args()
    if not os.path.isabs(args.out_base_dir):
        args.out_base_dir = str((projroot / args.out_base_dir).resolve())
    asyncio.run(run(args))

if __name__ == "__main__":
    main()

