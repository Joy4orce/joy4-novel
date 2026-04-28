"""
일본어 소설 사이트 크롤러
지원: 小説家になろう(ncode.syosetu.com), ノクターン(novel18.syosetu.com),
      ハーメルン(syosetu.org), カクヨム(kakuyomu.jp), pixiv(pixiv.net/novel)
"""

import json
import re
import time
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class CrawlerError(Exception):
    pass


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name).strip().strip(".")
    return name[:120] or "novel"


# ──────────────────────────────────────────────────────────────────────────────
# 공통 베이스
# ──────────────────────────────────────────────────────────────────────────────

class BaseCrawler:
    name = ""
    host_pattern: "re.Pattern" = None
    request_delay = 0.8

    def matches(self, url: str) -> bool:
        return bool(self.host_pattern and self.host_pattern.search(url))

    def fetch_toc(self, url: str):
        """returns (novel_title: str, [(episode_title, episode_url), ...])"""
        raise NotImplementedError

    def fetch_episode(self, url: str):
        """returns (episode_title: str, body_text: str)"""
        raise NotImplementedError

    def _get(self, url: str, cookies=None, extra_headers=None):
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"}
        if extra_headers:
            headers.update(extra_headers)
        r = requests.get(url, headers=headers, cookies=cookies, timeout=30)
        r.raise_for_status()
        return r


# ──────────────────────────────────────────────────────────────────────────────
# 小説家になろう / ノクターンノベルズ (동일 시스템)
# ──────────────────────────────────────────────────────────────────────────────

class NarouCrawler(BaseCrawler):
    name = "小説家になろう / ノクターン"
    host_pattern = re.compile(r"(ncode|novel18)\.syosetu\.com")
    request_delay = 0.8

    def _cookies(self, url):
        return {"over18": "yes"} if "novel18" in url else None

    def _get(self, url, **kw):
        return super()._get(url, cookies=self._cookies(url), **kw)

    def _extract_episodes(self, soup, base):
        eps = []
        # 신 구조
        for a in soup.select("a.p-eplist__subtitle"):
            href = a.get("href", "")
            if href:
                eps.append((a.get_text(strip=True), urljoin(base, href)))
        # 구 구조
        if not eps:
            for a in soup.select("dl.novel_sublist2 a, dd.subtitle a"):
                href = a.get("href", "")
                if href:
                    eps.append((a.get_text(strip=True), urljoin(base, href)))
        return eps

    def fetch_toc(self, url):
        m = re.search(r"(https?://(?:ncode|novel18)\.syosetu\.com/[a-zA-Z0-9]+)", url)
        if not m:
            raise CrawlerError("URL 형식이 올바르지 않습니다.")
        base = m.group(1) + "/"

        # 1페이지를 먼저 받아 제목과 총 페이지 수 확인
        first_soup = BeautifulSoup(self._get(base).text, "html.parser")

        title_el = (first_soup.select_one("h1.p-novel__title")
                    or first_soup.select_one("p.novel_title"))
        title = title_el.get_text(strip=True) if title_el else "novel"

        # 페이지네이션 앵커(href 또는 텍스트)에서 최대 ?p=N 값 탐색
        max_page = 1
        for a in first_soup.select("a[href]"):
            href = a.get("href", "") or ""
            for pm in re.finditer(r"[?&]p=(\d+)", href):
                n = int(pm.group(1))
                if n > max_page:
                    max_page = n
        # 숫자 텍스트 링크(예: "4", "最後")도 보조로 체크
        for a in first_soup.select(".novelview_pager-last, a.c-pager__item--last, a[href*='?p=']"):
            txt = a.get_text(strip=True)
            if txt.isdigit():
                n = int(txt)
                if n > max_page:
                    max_page = n

        episodes = []
        seen = set()

        def add_from(soup):
            added = 0
            for t, u in self._extract_episodes(soup, base):
                if u not in seen:
                    seen.add(u)
                    episodes.append((t, u))
                    added += 1
            return added

        add_from(first_soup)

        # 2페이지부터 max_page까지 순차 크롤
        for p in range(2, min(max_page, 500) + 1):
            time.sleep(0.3)
            page_url = f"{base}?p={p}"
            soup = BeautifulSoup(self._get(page_url).text, "html.parser")
            if add_from(soup) == 0:
                break

        # 단편 소설 — TOC에 에피소드 링크가 없는 경우
        if not episodes:
            episodes = [(title or "novel", base)]

        return title or "novel", episodes

    def fetch_episode(self, url):
        soup = BeautifulSoup(self._get(url).text, "html.parser")

        title_el = soup.select_one("h1.p-novel__title") or soup.select_one("p.novel_subtitle")
        title = title_el.get_text(strip=True) if title_el else ""

        parts = []

        def read(el):
            return el.get_text("\n", strip=True) if el else ""

        pre  = soup.select_one(".p-novel__text--preface")   or soup.select_one("#novel_p")
        post = soup.select_one(".p-novel__text--afterword") or soup.select_one("#novel_a")
        main = None
        for el in soup.select(".p-novel__text"):
            cls = el.get("class", [])
            if "p-novel__text--preface" not in cls and "p-novel__text--afterword" not in cls:
                main = el
                break
        if not main:
            main = soup.select_one("#novel_honbun")

        pre_t, main_t, post_t = read(pre), read(main), read(post)
        if pre_t:
            parts += [pre_t, "", "───", ""]
        if main_t:
            parts.append(main_t)
        if post_t:
            parts += ["", "───", "", post_t]

        return title, "\n".join(parts).strip()


# ──────────────────────────────────────────────────────────────────────────────
# ハーメルン
# ──────────────────────────────────────────────────────────────────────────────

class HamelnCrawler(BaseCrawler):
    name = "ハーメルン"
    host_pattern = re.compile(r"syosetu\.org")
    request_delay = 1.0

    def _cookies(self, url):
        return {"over18": "off"}  # 성인 페이지 확인 우회용

    def _get(self, url, **kw):
        return super()._get(url, cookies=self._cookies(url), **kw)

    def fetch_toc(self, url):
        m = re.search(r"(https?://syosetu\.org/novel/\d+)", url)
        if not m:
            raise CrawlerError("URL 형식이 올바르지 않습니다.")
        base = m.group(1) + "/"

        soup = BeautifulSoup(self._get(base).text, "html.parser")

        title_el = soup.select_one('span[itemprop="name"]') or soup.find("h1") or soup.find("title")
        title = title_el.get_text(strip=True) if title_el else "novel"

        episodes = []
        seen = set()
        for a in soup.select("a[href]"):
            href = a.get("href", "").strip()
            # N.html 또는 절대 경로 /novel/XXXX/N.html
            if re.match(r"^\d+\.html$", href) or re.match(r"^/novel/\d+/\d+\.html$", href):
                full = urljoin(base, href)
                if full not in seen:
                    seen.add(full)
                    episodes.append((a.get_text(strip=True) or "Untitled", full))

        if not episodes:
            # 단편일 가능성
            episodes = [(title, base + "1.html")]

        return title, episodes

    def fetch_episode(self, url):
        soup = BeautifulSoup(self._get(url).text, "html.parser")

        # 에피소드 제목 — 여러 위치 시도
        title = ""
        for sel in [".ss span.bold", "#maintitle", ".ss p", "h1"]:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if t:
                    title = t
                    break

        parts = []
        for sid, label in [("maegaki", "[前書き]"), ("honbun", None), ("atogaki", "[後書き]")]:
            el = soup.find(id=sid)
            if el:
                text = el.get_text("\n", strip=True)
                if text:
                    if label:
                        parts.append(label)
                    parts.append(text)
                    parts.append("")

        if not parts:
            main = soup.select_one("#novel_honbun") or soup.select_one(".ss")
            if main:
                parts.append(main.get_text("\n", strip=True))

        return title, "\n".join(parts).strip()


# ──────────────────────────────────────────────────────────────────────────────
# カクヨム
# ──────────────────────────────────────────────────────────────────────────────

class KakuyomuCrawler(BaseCrawler):
    name = "カクヨム"
    host_pattern = re.compile(r"kakuyomu\.jp/works")
    request_delay = 1.0

    def fetch_toc(self, url):
        m = re.search(r"(https?://kakuyomu\.jp/works/(\d+))", url)
        if not m:
            raise CrawlerError("URL 형식이 올바르지 않습니다.")
        base    = m.group(1)
        work_id = m.group(2)

        soup = BeautifulSoup(self._get(base).text, "html.parser")

        # 제목
        title = "novel"
        og = soup.select_one('meta[property="og:title"]')
        if og and og.get("content"):
            title = og["content"].split(" - カクヨム")[0].strip()
        else:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)

        # 1순위: __NEXT_DATA__ JSON 파싱 — 접힌 부분 포함 전체 에피소드 획득
        episodes = self._episodes_from_next_data(soup, work_id)

        # 2순위 폴백: 화면에 보이는 앵커만 (접힌 부분은 누락될 수 있음)
        if not episodes:
            seen = set()
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if re.match(r"^/works/\d+/episodes/\d+", href):
                    full = urljoin("https://kakuyomu.jp", href.split("?")[0])
                    if full not in seen:
                        seen.add(full)
                        ep_title = a.get_text(strip=True) or "Untitled"
                        episodes.append((ep_title, full))

        if not episodes:
            raise CrawlerError("에피소드 링크를 찾지 못했습니다.")

        return title, episodes

    def _episodes_from_next_data(self, soup, work_id):
        """카쿠요무의 __NEXT_DATA__ (Apollo cache) 에서 에피소드 전체 추출.
        접힌 '続きを表示' 에피소드도 모두 포함됨."""
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            return []
        try:
            data = json.loads(script.string)
        except Exception:
            return []

        # Apollo 상태 딕셔너리 탐색 (구조 변경 대비 여러 경로 시도)
        apollo = None
        for path in (
            ("props", "pageProps", "__APOLLO_STATE__"),
            ("props", "apolloState"),
            ("props", "pageProps", "apolloState"),
        ):
            cur = data
            for p in path:
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    cur = None
                    break
            if isinstance(cur, dict):
                apollo = cur
                break
        if not apollo:
            return []

        def deref(obj):
            if isinstance(obj, dict) and "__ref" in obj:
                return apollo.get(obj["__ref"])
            return obj

        # 1) Work → TOC → 챕터 → 에피소드 순서 재현
        ordered = []
        work_obj = apollo.get(f"Work:{work_id}") or next(
            (v for k, v in apollo.items()
             if k.startswith("Work:") and isinstance(v, dict)),
            None,
        )
        if work_obj:
            toc = work_obj.get("tableOfContents") or []
            if isinstance(toc, dict):
                toc = toc.get("edges") or toc.get("items") or []
            for item in toc:
                ch = deref(item)
                if not isinstance(ch, dict):
                    continue
                for key in ("episodeUnions", "episodes", "chapterEpisodes"):
                    eps = ch.get(key)
                    if not eps:
                        continue
                    for ep_ref in eps:
                        ep = deref(ep_ref)
                        if isinstance(ep, dict):
                            ordered.append(ep)
                    break

        # 2) 여전히 비었으면 — apollo 전체에서 Episode:* 수집 후 발행일 정렬
        if not ordered:
            for k, v in apollo.items():
                if k.startswith("Episode:") and isinstance(v, dict):
                    ordered.append(v)
            ordered.sort(key=lambda e: e.get("publishedAt") or "")

        results = []
        seen = set()
        for ep in ordered:
            # 일부 항목은 EpisodeUnion 처럼 한 단계 감싸져 있을 수 있음
            if ep.get("__typename") and "Episode" not in ep.get("__typename", ""):
                inner = deref(ep.get("episode") or {})
                if isinstance(inner, dict):
                    ep = inner
            eid = ep.get("id") or ep.get("_id")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            ep_title = ep.get("title") or "Untitled"
            results.append((ep_title, f"https://kakuyomu.jp/works/{work_id}/episodes/{eid}"))
        return results

    def fetch_episode(self, url):
        soup = BeautifulSoup(self._get(url).text, "html.parser")

        title = ""
        for sel in [".widget-episodeTitle", "p.widget-episodeTitle", "h1"]:
            el = soup.select_one(sel)
            if el:
                title = el.get_text(strip=True)
                break

        body = ""
        for sel in [
            ".widget-episodeBody",
            ".js-episode-body",
            'div[class*="episodeBody"]',
            "article",
        ]:
            el = soup.select_one(sel)
            if el:
                # <p><br></p> 같은 빈 문단도 보존
                lines = []
                for p in el.find_all(["p", "div"]):
                    t = p.get_text("\n", strip=False).rstrip()
                    lines.append(t)
                body = "\n".join(lines).strip() if lines else el.get_text("\n", strip=True)
                break

        return title, body


# ──────────────────────────────────────────────────────────────────────────────
# pixiv
# ──────────────────────────────────────────────────────────────────────────────

class PixivCrawler(BaseCrawler):
    name = "pixiv"
    host_pattern = re.compile(r"pixiv\.net/novel")
    request_delay = 0.6
    session_id = ""  # 설정창의 PHPSESSID — 로그인 필요 작품용

    def configure(self, site_cfg: dict):
        self.session_id = (site_cfg or {}).get("session_id", "").strip()

    def _ajax(self, url):
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": "https://www.pixiv.net/",
            "Accept": "application/json",
            "Accept-Language": "ja,en;q=0.8",
            "x-requested-with": "fetch",
        }
        cookies = {"PHPSESSID": self.session_id} if self.session_id else None
        r = requests.get(url, headers=headers, cookies=cookies, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            msg = data.get("message") or "error"
            if not self.session_id:
                msg += "  (API 설정에서 Pixiv Session ID를 입력하면 로그인 전용 작품에 접근할 수 있습니다)"
            raise CrawlerError(f"Pixiv: {msg}")
        return data.get("body", {})

    def fetch_toc(self, url):
        # 시리즈
        m = re.search(r"/novel/series/(\d+)", url)
        if m:
            sid = m.group(1)
            info = self._ajax(f"https://www.pixiv.net/ajax/novel/series/{sid}")
            title = info.get("title") or "novel"

            # 모든 에피소드 타이틀
            titles = self._ajax(
                f"https://www.pixiv.net/ajax/novel/series/{sid}/content_titles"
            )
            items = titles if isinstance(titles, list) else titles.get("contentTitles", [])
            episodes = []
            for it in items:
                nid = str(it.get("id", ""))
                t = it.get("title", "")
                if nid:
                    episodes.append((t or "Untitled", f"https://www.pixiv.net/novel/show.php?id={nid}"))

            if not episodes:
                raise CrawlerError("에피소드를 가져오지 못했습니다. (로그인 필요일 수 있음)")
            return title, episodes

        # 단일 소설
        m = re.search(r"[?&]id=(\d+)", url)
        if m:
            nid = m.group(1)
            info = self._ajax(f"https://www.pixiv.net/ajax/novel/{nid}")
            t = info.get("title") or "novel"
            return t, [(t, f"https://www.pixiv.net/novel/show.php?id={nid}")]

        raise CrawlerError("Pixiv URL 형식을 인식하지 못했습니다.")

    def fetch_episode(self, url):
        m = re.search(r"[?&]id=(\d+)", url)
        if not m:
            raise CrawlerError("에피소드 ID를 찾지 못했습니다.")
        nid = m.group(1)
        info = self._ajax(f"https://www.pixiv.net/ajax/novel/{nid}")
        title = info.get("title") or ""
        content = info.get("content") or ""

        # pixiv 고유 마크업 정리
        content = re.sub(r"\[\[rb:\s*([^>]+?)\s*>\s*[^\]]+\]\]", r"\1", content)
        content = re.sub(r"\[\[jumpuri:\s*([^>]+?)\s*>\s*[^\]]+\]\]", r"\1", content)
        content = re.sub(r"\[pixivimage:[^\]]+\]", "", content)
        content = re.sub(r"\[chapter:\s*([^\]]+)\]", r"\n\n■ \1\n", content)
        content = re.sub(r"\[newpage\]", "\n\n──────────\n\n", content)
        content = re.sub(r"\[jump:\s*\d+\]", "", content)

        return title, content.strip()


# ──────────────────────────────────────────────────────────────────────────────
# 레지스트리
# ──────────────────────────────────────────────────────────────────────────────

CRAWLERS = [
    NarouCrawler(),
    HamelnCrawler(),
    KakuyomuCrawler(),
    PixivCrawler(),
]


def detect_crawler(url: str):
    for c in CRAWLERS:
        if c.matches(url):
            return c
    return None


# ──────────────────────────────────────────────────────────────────────────────
# 크롤링 오케스트레이션
# ──────────────────────────────────────────────────────────────────────────────

def crawl_novel(url: str, progress_cb=None, cancel_cb=None, site_cfgs=None):
    """
    progress_cb(current:int, total:int, msg:str)
    cancel_cb() -> bool  (True이면 중단)
    site_cfgs: {"pixiv": {"session_id": "..."}} 등 사이트별 설정
    returns: (novel_title, full_text, succeeded_episodes, total_episodes)
    """
    crawler = detect_crawler(url)
    if not crawler:
        raise CrawlerError("지원하지 않는 사이트 URL입니다.")

    if site_cfgs and isinstance(crawler, PixivCrawler):
        crawler.configure(site_cfgs.get("pixiv", {}))

    if progress_cb:
        progress_cb(0, 0, f"[{crawler.name}] 목차를 불러오는 중...")

    title, episodes = crawler.fetch_toc(url)
    total = len(episodes)

    if progress_cb:
        progress_cb(0, total, f"▸ '{title}' — 에피소드 {total}개 발견")

    parts = [
        f"# {title}",
        f"출처: {url}",
        f"사이트: {crawler.name}",
        "",
        "=" * 60,
        "",
    ]

    ok = 0
    for i, (ep_title, ep_url) in enumerate(episodes, 1):
        if cancel_cb and cancel_cb():
            if progress_cb:
                progress_cb(i - 1, total, "⏹ 사용자에 의해 중단됨")
            break

        if progress_cb:
            progress_cb(i, total, f"[{i}/{total}] {ep_title}")

        try:
            ft_title, body = crawler.fetch_episode(ep_url)
            display = ft_title or ep_title
            parts.append("")
            parts.append(f"◆ 제{i}話  {display}")
            parts.append("")
            parts.append(body)
            parts.append("")
            parts.append("─" * 40)
            ok += 1
        except Exception as e:
            parts.append("")
            parts.append(f"◆ 제{i}話  {ep_title}  — [로드 실패: {e}]")
            parts.append("")
            if progress_cb:
                progress_cb(i, total, f"  ✗ 실패: {e}")

        if crawler.request_delay and i < total:
            time.sleep(crawler.request_delay)

    return title, "\n".join(parts), ok, total
