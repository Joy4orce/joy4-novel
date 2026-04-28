"""소설 텍스트 파일을 에피소드별/용량별로 분할.

다운로드된 소설은 crawler.py 가 다음과 같은 형식으로 저장:

    # <제목>
    출처: <url>
    사이트: <크롤러>

    ============

    ◆ 제1話  <에피소드 제목>

    <본문>

    ────────

    ◆ 제2話  ...

이 마커(`◆ 제N話`)를 경계로 에피소드를 인식해 분할한다.
"""

import os
import re
from typing import List, Tuple


# "◆ 제N話" 또는 "◆ 第N話" — 줄 시작에서 매치
EPISODE_RE = re.compile(r"(?m)^◆\s*제\d+話")


def parse_novel(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """소설 텍스트를 (header, episodes) 로 파싱.

    header   : 첫 에피소드 직전까지의 메타데이터 블록 (제목/출처/사이트/=== 줄)
    episodes : [(label, body), ...]  label = '◆ 제N話 ...' 한 줄, body = 그 뒤 본문
    """
    matches = list(EPISODE_RE.finditer(text))
    if not matches:
        # 에피소드 마커가 없으면 단편으로 간주 — 분할 불가능 신호
        return text, []

    header = text[: matches[0].start()].rstrip() + "\n"
    episodes: List[Tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].rstrip("\n")
        nl = chunk.find("\n")
        if nl == -1:
            label, body = chunk, ""
        else:
            label = chunk[:nl].rstrip()
            body = chunk[nl + 1 :]
        episodes.append((label, body))
    return header, episodes


def split_by_episodes(
    header: str, episodes: List[Tuple[str, str]], n_per_part: int
) -> List[str]:
    """N편씩 묶어 분할."""
    if n_per_part < 1:
        n_per_part = 1
    parts: List[str] = []
    total_parts = (len(episodes) + n_per_part - 1) // n_per_part
    for i in range(0, len(episodes), n_per_part):
        group = episodes[i : i + n_per_part]
        parts.append(_render_part(header, group, len(parts) + 1, total_parts))
    return parts


def split_by_size(
    header: str, episodes: List[Tuple[str, str]], max_chars: int
) -> List[str]:
    """문자 수 기준 분할 — 에피소드 경계에서만 끊음.
    한 에피소드가 max_chars를 넘으면 그 에피소드만 단독 파트로 떨어진다.
    """
    if max_chars < 1000:
        max_chars = 1000
    parts: List[str] = []
    cur: List[Tuple[str, str]] = []
    cur_size = 0
    for label, body in episodes:
        size = len(label) + len(body) + 2
        if cur and cur_size + size > max_chars:
            parts.append(_render_part(header, cur, len(parts) + 1, 0))
            cur = []
            cur_size = 0
        cur.append((label, body))
        cur_size += size
    if cur:
        parts.append(_render_part(header, cur, len(parts) + 1, 0))

    # 총 파트 수가 사후에 결정되므로 라벨을 한 번 더 갱신
    total = len(parts)
    parts = [re.sub(r"# Part \d+(?:/\d+)?", f"# Part {i+1}/{total}", p, count=1)
             for i, p in enumerate(parts)]
    return parts


def _render_part(
    header: str, group: List[Tuple[str, str]], part_no: int, total_parts: int
) -> str:
    label = f"# Part {part_no}/{total_parts}" if total_parts else f"# Part {part_no}"
    lines = [header.rstrip(), label, ""]
    for ep_label, body in group:
        lines.append(ep_label)
        lines.append("")
        lines.append(body.rstrip())
        lines.append("")
        lines.append("─" * 40)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_parts(parts: List[str], src_path: str) -> List[str]:
    """파트들을 원본 옆에 `<basename>_part01.txt` 식으로 저장.
    반환: 저장된 파일 경로 리스트.
    """
    base, _ = os.path.splitext(src_path)
    pad = max(2, len(str(len(parts))))
    saved: List[str] = []
    for i, p in enumerate(parts, 1):
        out = f"{base}_part{str(i).zfill(pad)}.txt"
        with open(out, "w", encoding="utf-8") as f:
            f.write(p)
        saved.append(out)
    return saved
