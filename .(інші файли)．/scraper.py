import asyncio
import aiohttp
import os
import time
import re
import html as htmllib
from pathlib import Path
from typing import Optional, Tuple

# "/books/" or "/world/" or "/suchasna/"
baseurl = "https://www.ukrlib.com.ua/suchasna/getfile.php"
outputdir = "ukrainianliterature"
concurrentrequests = 16
starttid = 1
endtid = 20000
scriptdir = Path(__file__).resolve().parent
projectroot = scriptdir.parent

#*//////////////////////////////////////////////////////////////////////*#

if not os.path.isabs(outputdir):
    outputdir = str((projectroot / outputdir).resolve())
os.makedirs(outputdir, exist_ok=True)

# fillwidth replacement symbols like in ytdlp :p 
disallowedchar = {
    "<": "＜", ">": "＞",
    ":": "：", '"': "＂",
    "/": "／", "\\": "＼",
    "|": "｜", "?": "？",
    "*": "＊",
}

def safefilename(s: str) -> str:
    s = s.replace("\r", " ").replace("\n", " ")
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s)
    s = "".join(disallowedchar.get(c, c) for c in s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(" .").strip()
    return s or "unknown"

#*//////////////////////////////////////////////////////////////////////*#

# read the html export to grab the author & title 
def extracttitleauthor(contentbytes: bytes, charset: Optional[str]) -> Tuple[str, str]:
    text = contentbytes.decode(charset or "utf-8", errors="replace")

    titlematch = re.search(r"<title>(.*?)</title>", text, flags=re.DOTALL | re.IGNORECASE)
    title = titlematch.group(1).strip() if titlematch else "unknown"
    title = htmllib.unescape(title)

    pagetitleblock = re.search(
        r'<div[^>]*class="page-title"[^>]*>(.*?)</div>',
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    author = "unknown"
    if pagetitleblock:
        h2match = re.search(r"<h2>(.*?)</h2>", pagetitleblock.group(1), flags=re.DOTALL | re.IGNORECASE)
        if h2match:
            author = h2match.group(1).strip()
    else:
        h2match = re.search(r"<h2>(.*?)</h2>", text, flags=re.DOTALL | re.IGNORECASE)
        if h2match:
            author = h2match.group(1).strip()
    author = htmllib.unescape(author)

    return title, author

#*//////////////////////////////////////////////////////////////////////*#

async def fetchandsave(session, tid, semaphore):
    params = {"tid": tid, "type": 6}
    url = baseurl
    async with semaphore:
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    content = await resp.read()

                    title, author = extracttitleauthor(content, resp.charset)
                    safetitle = safefilename(title)
                    safeauthor = safefilename(author)

                    filename = os.path.join(outputdir, f"{safetitle} — {safeauthor}.html")
                    if os.path.exists(filename):
                        return "skipped", tid
                    with open(filename, "wb") as f:
                        f.write(content)
                    return "success", tid
                elif resp.status == 429:
                    return "ratelimited", tid
                elif resp.status >= 400 and resp.status < 500:
                    return "clienterror", tid
                else:
                    return "othererror", tid
        except Exception as e:
            return "exception", tid
        
#*//////////////////////////////////////////////////////////////////////*#

async def main():
    semaphore = asyncio.Semaphore(concurrentrequests)
    tids = range(starttid, endtid + 1)
    connector = aiohttp.TCPConnector(limit=None)
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        i = starttid
        while i <= endtid:
            tasks = []
            for offset in range(concurrentrequests):
                tid = i + offset
                if tid > endtid:
                    break
                tasks.append(fetchandsave(session, tid, semaphore))
            if not tasks:
                break
            results = await asyncio.gather(*tasks)
            ratelimitedhit = False
            for res, tid in results:
                if res == "ratelimited":
                    ratelimitedhit = True
            if ratelimitedhit:
                print("rate limited, idling for a minute")
                await asyncio.sleep(60)
            i += concurrentrequests

if __name__ == "__main__":
    asyncio.run(main())
