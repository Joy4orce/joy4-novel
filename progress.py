"""번역 진행 상태 사이드카 저장/로드.

드롭된 파일 `소설.txt` 옆에 `소설.progress.json` 을 만들어 청크별 번역 결과를
청크 단위로 저장한다. 프로그램이 죽거나 사용자가 취소해도 다음 실행 시 자동 감지해
완료된 청크를 재사용하고 미완료 청크만 다시 번역한다.

상태 구조:
{
  "version": 1,
  "source_hash": "sha256...",
  "api_id": "gemini",
  "src_lang": "일본어",
  "tgt_lang": "한국어",
  "max_chars": 6000,
  "total": 98,
  "chunks": [
    {"i": 1, "status": "ok",      "text": "번역된 텍스트..."},
    {"i": 2, "status": "fail",    "text": "에러 메시지"},
    {"i": 3, "status": "pending", "text": null},
    ...
  ]
}
"""

import os
import json
import hashlib
from typing import List, Optional, Tuple


PROGRESS_VERSION = 1


def _hash_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def progress_path(source_path: str) -> str:
    """원본 파일 경로 → 진행 파일 경로."""
    return source_path + ".progress.json"


def load(source_path: str) -> Optional[dict]:
    """진행 파일이 있으면 로드, 없거나 깨졌으면 None."""
    p = progress_path(source_path)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save(source_path: str, state: dict):
    """진행 상태를 atomic write 로 저장 (tmp → rename)."""
    p = progress_path(source_path)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def remove(source_path: str):
    """진행 파일 삭제 (있으면)."""
    p = progress_path(source_path)
    if os.path.isfile(p):
        try:
            os.remove(p)
        except Exception:
            pass


def make_state(
    source_text: str,
    chunk_count: int,
    api_id: str,
    src_lang: str,
    tgt_lang: str,
    max_chars: int,
) -> dict:
    """새 진행 상태 생성 — 모든 청크 pending."""
    return {
        "version": PROGRESS_VERSION,
        "source_hash": _hash_text(source_text),
        "api_id": api_id,
        "src_lang": src_lang,
        "tgt_lang": tgt_lang,
        "max_chars": max_chars,
        "total": chunk_count,
        "chunks": [
            {"i": i + 1, "status": "pending", "text": None}
            for i in range(chunk_count)
        ],
    }


def matches(state: dict, source_text: str, chunk_count: int, max_chars: int) -> bool:
    """진행 파일이 현재 입력과 호환되는지 — 본문/청크 수/청크 크기가 모두 일치해야."""
    if state.get("version") != PROGRESS_VERSION:
        return False
    if state.get("source_hash") != _hash_text(source_text):
        return False
    if state.get("total") != chunk_count:
        return False
    if state.get("max_chars") != max_chars:
        return False
    return True


def summary(state: dict) -> Tuple[int, int, int]:
    """(ok 개수, fail 개수, pending 개수)"""
    ok = fail = pending = 0
    for c in state.get("chunks", []):
        s = c.get("status")
        if s == "ok":
            ok += 1
        elif s == "fail":
            fail += 1
        else:
            pending += 1
    return ok, fail, pending


def update_chunk(state: dict, i: int, status: str, text: Optional[str]):
    """1-based 청크 인덱스의 상태/텍스트 갱신."""
    idx = i - 1
    if 0 <= idx < len(state.get("chunks", [])):
        state["chunks"][idx] = {"i": i, "status": status, "text": text}
