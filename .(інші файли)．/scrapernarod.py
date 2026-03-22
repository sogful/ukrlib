from __future__ import annotations

import asyncio
import html as htmllib
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from collections import defaultdict
from typing import Optional, Tuple
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

baseurl = "https://www.ukrlib.com.ua/narod/printzip.php"
printouturl = "https://www.ukrlib.com.ua/narod/printout.php"
narodroot = "https://www.ukrlib.com.ua/narod/"

# 0 up to 26
genreid = (0,26)
bookidstart = 0
bookidend = 30
outputdir = "narodliterature"
concurrentrequests = 4

scriptdir = Path(__file__).resolve().parent
projectroot = scriptdir.parent

#*//////////////////////////////////////////////////////////////////////*#

if not os.path.isabs(outputdir):
    outputdir = str((projectroot / outputdir).resolve())
os.makedirs(outputdir, exist_ok=True)

useragent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
defaultheaders = {"User-Agent": useragent}

disallowedchars = {
    "<": "＜", ">": "＞",
    ":": "：", '"': "＂",
    "/": "／", "\\": "＼",
    "|": "｜", "?": "？",
    "*": "＊",
}

ziphrefre = re.compile(r"""href=(["'])([^"']+\.zip)\1""", re.IGNORECASE)
htmlshell = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"
    "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="stylesheet" type="text/css" href="styles.css" />
<title>{title_esc}</title>
<link rel="stylesheet" type="text/css" href="/style.css">
</head>
<body>
<div class="wrap">
 <div class="page-title">
  <h1>{h1_esc}</h1>
  <h2>{h2_esc}</h2>
 </div>
 <div class="prose" id="content">
{body}
 </div>
</div>
</body>
</html>
"""

#*//////////////////////////////////////////////////////////////////////*#

def safefilename(s: str) -> str:
    s = s.replace("\r", " ").replace("\n", " ")
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s)
    s = "".join(disallowedchars.get(c, c) for c in s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(" .").strip()
    return s or "unknown"

def findsoffice() -> Optional[str]:
    for c in (
        shutil.which("soffice"),
        shutil.which("soffice.exe"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if c and Path(c).exists():
            return str(Path(c))
    return None

def parseziphref(html: str) -> Optional[str]:
    m = ziphrefre.search(html)
    if not m:
        return None
    href = m.group(2).replace("&amp;", "&")
    return href

def resolvegenreids(v) -> list[int]:
    if isinstance(v, int):
        return [v]
    if isinstance(v, (tuple, list)) and len(v) == 2:
        a = int(v[0])
        b = int(v[1])
        step = 1 if a <= b else -1
        return list(range(a, b + step, step))
    raise ValueError("genreid must be int or a 2-item tuple/list, e.g. 23 or (20, 30)")

def narodtitleh2(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    pt = soup.select_one("div.page-title")
    if not pt:
        return "unknown", "unknown"
    h1 = pt.select_one("h1")
    h2 = pt.select_one("h2")
    t = h1.get_text(" ", strip=True) if h1 else "unknown"
    a = h2.get_text(" ", strip=True) if h2 else "unknown"
    return t, a

def bodyfromprintout(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    article = soup.select_one("article#content")
    if not article:
        return None
    for junk in article.select("div.post-right-zagolovok, script, ins.adsbygoogle"):
        junk.decompose()
    return article.decode_contents().strip() or None

def _normtitletext(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[«»\"'“”„‟`]+", "", s)
    s = re.sub(r"[^\w\s\u0400-\u04FF-]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _compacttitletext(s: str) -> str:
    s = _normtitletext(s)
    s = re.sub(r"[\s_-]+", "", s)
    s = re.sub(r"\d+$", "", s)
    return s

def normalizebodyhtml(bodyhtml: str, h1: str) -> str:
    soup = BeautifulSoup(bodyhtml, "lxml")

    for tag in soup.find_all(True):
        for attr in ("style", "class", "lang", "align", "dir"):
            if tag.has_attr(attr):
                del tag[attr]
    for t in soup.find_all("font"):
        t.unwrap()

    titlenor = _normtitletext(h1)
    titlecompact = _compacttitletext(h1)
    container = soup.body if soup.body else soup
    firsttag = None
    for ch in container.children:
        if getattr(ch, "name", None):
            txt = ch.get_text(" ", strip=True)
            if txt:
                firsttag = ch
                break
    if firsttag is not None:
        firsttext = firsttag.get_text(" ", strip=True)
        firstnorm = _normtitletext(firsttext)
        firstcompact = _compacttitletext(firsttext)
        firstcompactlen = len(firstcompact)
        titlecompactlen = len(titlecompact)
        heading_like = (
            titlecompactlen > 0
            and firstcompactlen <= titlecompactlen + 2
        )
        if titlenor and (
            firstnorm == titlenor
            or firstcompact == titlecompact
            or (heading_like and firstcompact.startswith(titlecompact))
        ):
            firsttag.decompose()

    body = soup.body
    if body:
        return body.decode_contents().strip()
    return str(soup).strip()

def extractdocfromzip(zdata: bytes) -> Tuple[str, bytes]:
    with zipfile.ZipFile(BytesIO(zdata), "r") as zf:
        names = zf.namelist()
        docs = [n for n in names if re.search(r"\.docx?$", n, re.I)]
        if not docs:
            raise ValueError("no .doc/.docx in zip")
        docs.sort(key=lambda n: (0 if n.lower().endswith(".doc") else 1, n))
        name = docs[0]
        return name, zf.read(name)

def convertdocbytestohtml(data: bytes, ext: str) -> str:
    ext = ext.lower()
    if data[:2] == b"PK" or ext == ".docx":
        try:
            import mammoth
        except ImportError as e:
            raise RuntimeError(
                "zip contained docx; install mammoth: pip install mammoth"
            ) from e
        result = mammoth.convert_to_html(BytesIO(data))
        return result.value

    soffice = findsoffice()
    if not soffice:
        raise RuntimeError(
            "libreoffice not found (.doc conversion). install libreoffice or add soffice to path."
        )
    tmp = Path(tempfile.mkdtemp(prefix="narodzip_"))
    try:
        docpath = tmp / ("work.doc" if ext == ".doc" else "work.docx")
        docpath.write_bytes(data)
        outdir = tmp / "lo_out"
        outdir.mkdir()
        try:
            subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    "html",
                    "--outdir",
                    str(outdir),
                    str(docpath),
                ],
                check=True,
                timeout=120,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or "").strip()
            raise RuntimeError(
                f"libreoffice exit {e.returncode}: {err[:500] or 'no stderr'}"
            ) from e
        htmls = list(outdir.glob("*.html"))
        if not htmls:
            raise RuntimeError("libreoffice produced no html output")
        lohtml = htmls[0].read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(lohtml, "lxml")
        body = soup.body
        if not body:
            return lohtml
        parts = []
        for child in body.children:
            if getattr(child, "name", None):
                parts.append(str(child))
            elif isinstance(child, str) and child.strip():
                parts.append(htmllib.escape(child))
        return "\n".join(parts) if parts else body.decode_contents()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def buildexporthtml(h1: str, h2: str, body: str) -> str:
    titleesc = htmllib.escape(f"{h1} — {h2}")
    h1esc = htmllib.escape(h1)
    h2esc = htmllib.escape(h2)
    return htmlshell.format(
        title_esc=titleesc,
        h1_esc=h1esc,
        h2_esc=h2esc,
        body=body,
    )

#*//////////////////////////////////////////////////////////////////////*#

async def fetchtext(session: aiohttp.ClientSession, url: str, params: dict) -> Tuple[int, str]:
    async with session.get(url, params=params, headers=defaultheaders) as resp:
        text = await resp.text(errors="replace")
        return resp.status, text

async def fetchbytes(session: aiohttp.ClientSession, url: str) -> Tuple[int, bytes]:
    async with session.get(url, headers=defaultheaders) as resp:
        data = await resp.read()
        return resp.status, data

async def fetchzippageretry(session: aiohttp.ClientSession, params: dict) -> Tuple[int, str]:
    for attempt in range(3):
        st, txt = await fetchtext(session, baseurl, params)
        if st != 429:
            return st, txt
        await asyncio.sleep(30 * (attempt + 1))
    return st, txt

async def processone(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    genre: int,
    bookid: int,
) -> Tuple[str, int]:
    params = {"id": genre, "bookid": bookid}
    async with semaphore:
        try:
            st, printziphtml = await fetchzippageretry(session, params)
            if st != 200:
                print(f"[skip] bookid={bookid} printzip http {st}")
                return "http_error_zippage", bookid
            href = parseziphref(printziphtml)
            if not href:
                print(f"[skip] bookid={bookid} no .zip href in printzip page")
                return "no_zip_link", bookid

            zipurl = urljoin(narodroot, href)

            stpo, printouthtml = await fetchtext(session, printouturl, params)
            if stpo != 200:
                print(f"[skip] bookid={bookid} printout http {stpo}")
                return "http_error_printout", bookid
            h1, h2 = narodtitleh2(printouthtml)
            safet = safefilename(h1)
            safea = safefilename(h2)
            outpath = os.path.join(outputdir, f"{safet} — {safea}.html")
            if os.path.exists(outpath):
                print(f"[skip] bookid={bookid} already exists: {outpath}")
                return "skipped", bookid

            stz, zdata = await fetchbytes(session, zipurl)
            if stz != 200 or not zdata:
                print(f"[skip] bookid={bookid} zip download http {stz} ({zipurl})")
                return "zip_download_fail", bookid

            try:
                innername, docbytes = extractdocfromzip(zdata)
            except Exception as e:
                print(f"[skip] bookid={bookid} bad zip: {e}")
                return "bad_zip", bookid

            ext = Path(innername).suffix.lower()
            usedfallback = False
            try:
                bodyhtml = await asyncio.to_thread(
                    convertdocbytestohtml, docbytes, ext
                )
            except Exception as e:
                fallback = bodyfromprintout(printouthtml)
                if not fallback:
                    print(f"[skip] bookid={bookid} doc→html failed: {e}")
                    return "convert_fail", bookid
                bodyhtml = fallback
                usedfallback = True
                print(f"[warn] bookid={bookid} doc→html failed, used printout fallback: {e}")

            bodyhtml = normalizebodyhtml(bodyhtml, h1)
            finalhtml = buildexporthtml(h1, h2, bodyhtml)
            with open(outpath, "w", encoding="utf-8") as f:
                f.write(finalhtml)
            print(f"[ok] bookid={bookid} -> {outpath}")
            return ("success_fallback" if usedfallback else "success"), bookid
        except asyncio.TimeoutError:
            print(f"[skip] bookid={bookid} timeout")
            return "timeout", bookid
        except Exception as e:
            print(f"[skip] bookid={bookid} exception: {e!r}")
            return "exception", bookid

#*//////////////////////////////////////////////////////////////////////*#

async def main():
    print(f"[run] output folder: {outputdir}")
    genres = resolvegenreids(genreid)
    print(f"[run] genre id(s)={genres}, bookid range {bookidstart}..{bookidend}")
    soffice = findsoffice()
    print(f"[run] libreoffice soffice: {soffice or 'NOT FOUND (needed for .doc in zip)'}")

    semaphore = asyncio.Semaphore(concurrentrequests)
    connector = aiohttp.TCPConnector(limit=None)
    timeout = aiohttp.ClientTimeout(total=120)
    stats: defaultdict[str, int] = defaultdict(int)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        for g in genres:
            print(f"[run] processing genre {g}")
            i = bookidstart
            while i <= bookidend:
                tasks = []
                for offset in range(concurrentrequests):
                    bid = i + offset
                    if bid > bookidend:
                        break
                    tasks.append(processone(session, semaphore, g, bid))
                if not tasks:
                    break
                results = await asyncio.gather(*tasks)
                for s, _ in results:
                    stats[s] += 1
                if any(s == "timeout" for s, _ in results):
                    await asyncio.sleep(5)
                i += concurrentrequests

    print("[run] summary:", dict(sorted(stats.items(), key=lambda x: (-x[1], x[0]))))
    if stats.get("success", 0) == 0 and stats.get("skipped", 0) == 0:
        print(
            "[hint] if you see many convert_fail: install LibreOffice and ensure "
            "soffice.exe is on PATH, or pip install mammoth for .docx-only zips."
        )

if __name__ == "__main__":
    asyncio.run(main())

