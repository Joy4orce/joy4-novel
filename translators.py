import requests
import json
import os
import re
import sys
import shutil
import subprocess
import glob
import time


class TranslationError(Exception):
    pass


class _RateLimited(Exception):
    """단일 키가 레이트리밋/쿼터에 걸렸을 때 내부적으로 발생 — 호출자가 다음 키로 전환"""
    pass


# 레이트리밋/쿼터 초과로 간주할 HTTP 상태코드
_RATE_LIMIT_CODES = {402, 403, 429, 456}


def _keys_of(cfg) -> list:
    """api_keys 리스트 우선, 없으면 api_key 단일값 사용"""
    ks = cfg.get("api_keys") or []
    if isinstance(ks, str):
        ks = [k.strip() for k in ks.splitlines() if k.strip()]
    else:
        ks = [str(k).strip() for k in ks if str(k).strip()]
    if not ks:
        single = (cfg.get("api_key") or "").strip()
        if single:
            ks = [single]
    return ks


# 번역기 인스턴스마다 다음 호출에 쓸 키 인덱스를 기억 — 매 호출마다 라운드로빈으로
# 다음 키로 넘어가며, 도중에 _RateLimited 가 나면 그 자리에서 다음 키로 즉시 재시도.
class _KeyRotator:
    def __init__(self):
        self._idx = 0

    def run(self, keys: list, call):
        if not keys:
            raise TranslationError("API 키가 설정되지 않았습니다.")
        n = len(keys)
        last_err = None
        tried = 0
        while tried < n:
            idx = self._idx % n
            # 호출 전에 인덱스를 미리 전진시켜 — 성공/실패 무관하게 다음 호출은 다음 키
            self._idx = (idx + 1) % n
            try:
                return call(keys[idx])
            except _RateLimited as e:
                last_err = e
                tried += 1
        raise TranslationError(
            f"모든 API 키({n}개)의 사용량/쿼터 한도에 도달했습니다.\n마지막 응답: {last_err}"
        )


def _maybe_rate_limited(resp, api_label: str):
    if resp.status_code in _RATE_LIMIT_CODES:
        raise _RateLimited(f"{api_label} {resp.status_code}: {resp.text[:200]}")


# 일시적 네트워크 오류는 자동 재시도 — 타임아웃/연결끊김에 한해
_TRANSIENT_EXCS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)

# 일시적 서버 과부하/장애로 간주해 재시도할 HTTP 상태코드.
# 503: 모델 과부하 (Gemini 등에서 자주 발생), 502/504: 게이트웨이, 500: 일반 서버 오류.
_RETRY_STATUS = frozenset({500, 502, 503, 504})


def _parse_retry_after(value: str) -> float:
    """Retry-After 헤더 파싱 — 초(int) 또는 HTTP-date. 실패 시 0."""
    if not value:
        return 0.0
    value = value.strip()
    # delta-seconds 형태
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    # HTTP-date 형태
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        if dt is None:
            return 0.0
        import datetime as _dt
        now = _dt.datetime.now(dt.tzinfo) if dt.tzinfo else _dt.datetime.utcnow()
        return max(0.0, (dt - now).total_seconds())
    except Exception:
        return 0.0


def _post_with_retry(url, *, max_retries=4, backoff=2.0, max_delay=30.0, **kwargs):
    """requests.post 의 일시적 실패를 자동 재시도.

    재시도 대상:
      - 네트워크 예외 (타임아웃/연결끊김)
      - HTTP 5xx 중 일시적 장애 코드 (500/502/503/504)
        → Retry-After 헤더가 있으면 그만큼 대기, 없으면 지수 백오프 + 지터.

    영구적 4xx (인증/한도/요청오류 등) 응답은 즉시 반환 — 호출자가 처리."""
    import random
    last_err = None
    last_resp = None
    delay = 1.5
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, **kwargs)
        except _TRANSIENT_EXCS as e:
            last_err = e
            last_resp = None
            if attempt < max_retries:
                time.sleep(delay + random.uniform(0, delay * 0.25))
                delay = min(delay * backoff, max_delay)
                continue
            raise
        # 일시적 5xx → 재시도
        if resp.status_code in _RETRY_STATUS and attempt < max_retries:
            last_resp = resp
            wait = _parse_retry_after(resp.headers.get("Retry-After", ""))
            if wait <= 0:
                wait = delay + random.uniform(0, delay * 0.25)
            time.sleep(min(wait, max_delay))
            delay = min(delay * backoff, max_delay)
            continue
        return resp
    # 모든 재시도 실패 — 마지막 응답이라도 있으면 반환 (호출자가 5xx 메시지를 보여줄 수 있게)
    if last_resp is not None:
        return last_resp
    raise last_err  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────────────
# 사용자 프롬프트 / 사전 처리
# ──────────────────────────────────────────────────────────────────────────────

def _parse_user_dict(raw) -> list:
    """'원문=번역' 라인을 (src, tgt) 튜플 리스트로. '#'은 주석."""
    pairs = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            s, t = line.split("=", 1)
            s, t = s.strip(), t.strip()
            if s and t:
                pairs.append((s, t))
    return pairs


def _apply_dict_pre(text: str, pairs: list) -> str:
    """비-LLM 번역기용 — 원문 내 용어를 미리 목표 표기로 치환.
    긴 용어부터 처리해서 부분 일치로 인한 중복 치환을 방지."""
    if not pairs:
        return text
    for s, t in sorted(pairs, key=lambda p: -len(p[0])):
        text = text.replace(s, t)
    return text


def _llm_extras(cfg) -> str:
    """LLM 시스템 프롬프트에 덧붙일 사용자 지시/사전 블록 생성."""
    prompt = (cfg.get("_prompt") or "").strip()
    pairs  = _parse_user_dict(cfg.get("_dictionary"))
    parts = []
    if prompt:
        parts.append("Additional translation instructions from the user "
                     "(follow these carefully):\n" + prompt)
    if pairs:
        lines = "\n".join(f"  - {s}  →  {t}" for s, t in pairs)
        parts.append("User-provided glossary — translate these terms EXACTLY "
                     "as specified, do not paraphrase:\n" + lines)
    return ("\n\n".join(parts) + "\n\n") if parts else ""


# ──────────────────────────────────────────────────────────────────────────────
# LLM 응답 후처리 — 모델이 시스템 프롬프트를 어기고 사고 과정/서두를 본문에
# 그대로 출력하는 경우를 정리.
# ──────────────────────────────────────────────────────────────────────────────

# `<thinking>...</thinking>` 같은 의사 thinking 블록 (변종 태그명도 포함)
_THINK_TAGS = r"(?:thinking|think|reasoning|reason|scratchpad|analysis|reflection|internal|monologue)"

# 완전한 블록: <thinking>...</thinking>  (대소문자/공백 허용, 중첩 단순 처리)
_RE_THINK_BLOCK = re.compile(
    rf"<\s*{_THINK_TAGS}\s*>.*?<\s*/\s*{_THINK_TAGS}\s*>",
    re.IGNORECASE | re.DOTALL,
)

# 떠돌이 닫기 태그만 있는 경우 — 그 앞 내용 전부와 태그를 제거
_RE_STRAY_CLOSE = re.compile(
    rf"\A.*?<\s*/\s*{_THINK_TAGS}\s*>\s*",
    re.IGNORECASE | re.DOTALL,
)

# 떠돌이 여는 태그가 끝까지 닫히지 않은 경우 — 태그 이후를 잘라낼 수 없으므로
# 태그 위치에서 뒤를 살리되, 태그 자체는 제거 (보수적으로 한 줄만 제거).
_RE_STRAY_OPEN = re.compile(
    rf"<\s*{_THINK_TAGS}\s*>[^\n]*\n?",
    re.IGNORECASE,
)

# 흔한 영문 서두 (한 줄짜리). 줄 끝 콜론/마침표까지 함께 제거.
_PREFACE_PATTERNS = [
    r"^\s*(?:sure|okay|ok|alright|of course|certainly|absolutely)[,!\.]?\s*"
    r"(?:here(?:'s| is)|i(?:'ll| will))\b[^\n]*\n",
    r"^\s*here(?:'s| is)\s+(?:the\s+)?(?:korean\s+)?translation[^\n]*\n",
    r"^\s*translation\s*[:\-]\s*\n",
    r"^\s*(?:translated\s+text|번역(?:문|결과)?)\s*[:\-]\s*\n",
]
_RE_PREFACES = [re.compile(p, re.IGNORECASE) for p in _PREFACE_PATTERNS]


def _strip_llm_preamble(text: str) -> str:
    """모델이 본문에 흘린 사고 과정/서두를 제거."""
    if not text:
        return text
    out = text
    # 1) 완전한 <thinking>...</thinking> 블록 제거 (반복 적용)
    while True:
        new = _RE_THINK_BLOCK.sub("", out)
        if new == out:
            break
        out = new
    # 2) 떠돌이 </thinking> — 그 앞 내용까지 통째로 제거
    out = _RE_STRAY_CLOSE.sub("", out)
    # 3) 떠돌이 <thinking> 여는 태그만 있는 경우 — 태그 한 줄만 제거
    out = _RE_STRAY_OPEN.sub("", out)
    # 4) 흔한 영문 서두 라인 제거
    for rx in _RE_PREFACES:
        out = rx.sub("", out, count=1)
    return out.strip()


# API별 청크당 최대 문자 수 (요청 타임아웃 방지 및 진행률 가시성)
MAX_CHARS = {
    "openai":   6000,
    "papago":   4500,
    "google":   4500,
    "deepl":    4000,
    "gemini":   6000,
    "claude":   6000,
    "local":    4000,   # 4K 컨텍스트 가정 (사용자 조정 가능)
}


def chunk_text(text: str, max_chars: int) -> list:
    """문단(\\n\\n) → 줄(\\n) → 하드 분할 순서로 경계를 보존하며 나눔."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    buf = []
    buf_len = 0

    def flush():
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf))
            buf = []
            buf_len = 0

    for para in text.split("\n\n"):
        plen = len(para) + (2 if buf else 0)
        if plen <= max_chars and buf_len + plen <= max_chars:
            buf.append(para)
            buf_len += plen
            continue

        flush()

        if len(para) <= max_chars:
            buf = [para]
            buf_len = len(para)
            continue

        # 문단이 너무 큼 → 줄 단위
        line_buf = []
        line_buf_len = 0
        for line in para.split("\n"):
            llen = len(line) + (1 if line_buf else 0)
            if llen <= max_chars and line_buf_len + llen <= max_chars:
                line_buf.append(line)
                line_buf_len += llen
            else:
                if line_buf:
                    chunks.append("\n".join(line_buf))
                    line_buf = []
                    line_buf_len = 0
                if len(line) > max_chars:
                    # 한 줄이 한도 초과 — 강제 분할
                    for i in range(0, len(line), max_chars):
                        chunks.append(line[i:i + max_chars])
                else:
                    line_buf = [line]
                    line_buf_len = len(line)
        if line_buf:
            chunks.append("\n".join(line_buf))

    flush()
    return [c for c in chunks if c.strip()]


LANG_CODES = {
    "openai":   {"자동감지": None, "한국어": "Korean", "영어": "English", "일본어": "Japanese",
                 "중국어(간체)": "Simplified Chinese", "중국어(번체)": "Traditional Chinese",
                 "프랑스어": "French", "독일어": "German", "스페인어": "Spanish", "러시아어": "Russian"},
    "papago":   {"자동감지": "auto", "한국어": "ko", "영어": "en", "일본어": "ja",
                 "중국어(간체)": "zh-CN", "중국어(번체)": "zh-TW",
                 "프랑스어": "fr", "독일어": "de", "스페인어": "es", "러시아어": "ru"},
    "google":   {"자동감지": "auto", "한국어": "ko", "영어": "en", "일본어": "ja",
                 "중국어(간체)": "zh-CN", "중국어(번체)": "zh-TW",
                 "프랑스어": "fr", "독일어": "de", "스페인어": "es", "러시아어": "ru"},
    "deepl":    {"자동감지": None, "한국어": "KO", "영어": "EN", "일본어": "JA",
                 "중국어(간체)": "ZH-HANS", "중국어(번체)": "ZH-HANT",
                 "프랑스어": "FR", "독일어": "DE", "스페인어": "ES", "러시아어": "RU"},
    "gemini":   {"자동감지": None, "한국어": "Korean", "영어": "English", "일본어": "Japanese",
                 "중국어(간체)": "Simplified Chinese", "중국어(번체)": "Traditional Chinese",
                 "프랑스어": "French", "독일어": "German", "스페인어": "Spanish", "러시아어": "Russian"},
    "claude":   {"자동감지": None, "한국어": "Korean", "영어": "English", "일본어": "Japanese",
                 "중국어(간체)": "Simplified Chinese", "중국어(번체)": "Traditional Chinese",
                 "프랑스어": "French", "독일어": "German", "스페인어": "Spanish", "러시아어": "Russian"},
    "local":    {"자동감지": None, "한국어": "Korean", "영어": "English", "일본어": "Japanese",
                 "중국어(간체)": "Simplified Chinese", "중국어(번체)": "Traditional Chinese",
                 "프랑스어": "French", "독일어": "German", "스페인어": "Spanish", "러시아어": "Russian"},
}


def _llm_translate(text, source_lang, target_lang, system_prompt_fn, api_call_fn):
    src = source_lang if source_lang else "the source language"
    tgt = target_lang if target_lang else "Korean"
    system = system_prompt_fn(src, tgt)
    return api_call_fn(system, text)


# ──────────────────────────────────────────────────────────────────────────────
# OpenAI (ChatGPT)
# ──────────────────────────────────────────────────────────────────────────────

class OpenAITranslator:
    name = "ChatGPT (OpenAI)"

    def __init__(self):
        self._rot = _KeyRotator()

    def translate(self, text, source_lang_name, target_lang_name, cfg):
        keys  = _keys_of(cfg)
        if not keys:
            raise TranslationError("OpenAI API 키가 설정되지 않았습니다.")
        model = cfg.get("model", "gpt-4o-mini").strip() or "gpt-4o-mini"

        src = source_lang_name or "자동감지"
        tgt = target_lang_name or "Korean"
        system = (f"You are a professional translator. "
                  f"Translate the following text from {src} to {tgt}. "
                  f"Return only the translated text without any explanation.\n\n"
                  + _llm_extras(cfg))

        def call(api_key):
            resp = _post_with_retry(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": text},
                ]},
                timeout=180,
            )
            _maybe_rate_limited(resp, "OpenAI")
            if resp.status_code != 200:
                raise TranslationError(f"OpenAI 오류 {resp.status_code}: {resp.text[:300]}")
            return resp.json()["choices"][0]["message"]["content"].strip()

        return self._rot.run(keys, call)


# ──────────────────────────────────────────────────────────────────────────────
# Naver Papago
# ──────────────────────────────────────────────────────────────────────────────

class PapagoTranslator:
    name = "Naver Papago"

    def translate(self, text, source_lang_code, target_lang_code, cfg):
        client_id     = cfg.get("client_id", "").strip()
        client_secret = cfg.get("client_secret", "").strip()
        if not client_id or not client_secret:
            raise TranslationError("Papago Client ID 또는 Client Secret이 설정되지 않았습니다.")

        text = _apply_dict_pre(text, _parse_user_dict(cfg.get("_dictionary")))
        src = source_lang_code if source_lang_code and source_lang_code != "auto" else "auto"
        tgt = target_lang_code or "ko"

        resp = requests.post(
            "https://naveropenapi.apigw.ntruss.com/nmt/v1/translation",
            headers={
                "X-NCP-APIGW-API-KEY-ID": client_id,
                "X-NCP-APIGW-API-KEY":    client_secret,
                "Content-Type":           "application/json",
            },
            json={"source": src, "target": tgt, "text": text},
            timeout=30,
        )
        if resp.status_code != 200:
            raise TranslationError(f"Papago 오류 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return data["message"]["result"]["translatedText"]


# ──────────────────────────────────────────────────────────────────────────────
# Google Translate (Cloud Translation API v2)
# ──────────────────────────────────────────────────────────────────────────────

class GoogleTranslateTranslator:
    name = "Google Translate"

    def __init__(self):
        self._rot = _KeyRotator()

    def translate(self, text, source_lang_code, target_lang_code, cfg):
        keys = _keys_of(cfg)
        if not keys:
            raise TranslationError("Google Translate API 키가 설정되지 않았습니다.")

        text = _apply_dict_pre(text, _parse_user_dict(cfg.get("_dictionary")))
        tgt = target_lang_code or "ko"

        def call(api_key):
            params = {"key": api_key, "q": text, "target": tgt, "format": "text"}
            if source_lang_code and source_lang_code != "auto":
                params["source"] = source_lang_code
            resp = _post_with_retry(
                "https://translation.googleapis.com/language/translate/v2",
                params=params,
                timeout=180,
            )
            _maybe_rate_limited(resp, "Google Translate")
            if resp.status_code != 200:
                raise TranslationError(f"Google Translate 오류 {resp.status_code}: {resp.text[:300]}")
            return resp.json()["data"]["translations"][0]["translatedText"]

        return self._rot.run(keys, call)


# ──────────────────────────────────────────────────────────────────────────────
# DeepL
# ──────────────────────────────────────────────────────────────────────────────

class DeepLTranslator:
    name = "DeepL"

    def __init__(self):
        self._rot = _KeyRotator()

    def translate(self, text, source_lang_code, target_lang_code, cfg):
        keys     = _keys_of(cfg)
        use_free = cfg.get("use_free", True)
        if not keys:
            raise TranslationError("DeepL API 키가 설정되지 않았습니다.")

        text = _apply_dict_pre(text, _parse_user_dict(cfg.get("_dictionary")))
        base = "https://api-free.deepl.com" if use_free else "https://api.deepl.com"
        tgt  = target_lang_code or "KO"
        payload = {"text": [text], "target_lang": tgt}
        if source_lang_code:
            payload["source_lang"] = source_lang_code

        def call(api_key):
            resp = _post_with_retry(
                f"{base}/v2/translate",
                headers={"Authorization": f"DeepL-Auth-Key {api_key}",
                         "Content-Type": "application/json"},
                json=payload,
                timeout=180,
            )
            _maybe_rate_limited(resp, "DeepL")
            if resp.status_code != 200:
                raise TranslationError(f"DeepL 오류 {resp.status_code}: {resp.text[:300]}")
            return resp.json()["translations"][0]["text"]

        return self._rot.run(keys, call)


# ──────────────────────────────────────────────────────────────────────────────
# Google Gemini
# ──────────────────────────────────────────────────────────────────────────────

class GeminiTranslator:
    name = "Google Gemini"

    def __init__(self):
        self._rot = _KeyRotator()

    def translate(self, text, source_lang_name, target_lang_name, cfg):
        keys  = _keys_of(cfg)
        model = cfg.get("model", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
        if not keys:
            raise TranslationError("Gemini API 키가 설정되지 않았습니다.")

        src = source_lang_name or "자동감지"
        tgt = target_lang_name or "Korean"
        extras = _llm_extras(cfg)
        prompt = (f"Translate the following text from {src} to {tgt}.\n\n"
                  f"OUTPUT RULES (strict):\n"
                  f"1. Output ONLY the translated text. Nothing else.\n"
                  f"2. Do NOT think out loud or narrate your process.\n"
                  f"3. Do NOT use <thinking>, <reasoning>, or any meta tags.\n"
                  f"4. Do NOT write a preface, header, label, or commentary.\n"
                  f"5. The first and last characters of your response MUST be "
                  f"the first and last characters of the translation itself.\n\n"
                  f"{extras}"
                  f"TEXT:\n{text}")

        # 안전 필터를 가장 느슨하게 — 일본 소설 등 수위 있는 콘텐츠 번역 시 차단 빈도 감소
        safety = [
            {"category": c, "threshold": "BLOCK_NONE"}
            for c in (
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            )
        ]

        def call(api_key):
            resp = _post_with_retry(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "safetySettings": safety,
                },
                timeout=180,
            )
            _maybe_rate_limited(resp, "Gemini")
            if resp.status_code != 200:
                # 503/500/502/504 — _post_with_retry가 이미 백오프하며 재시도했지만 끝까지 실패한 경우
                if resp.status_code in _RETRY_STATUS:
                    raise TranslationError(
                        f"Gemini 서버 과부하 {resp.status_code} (재시도 모두 실패).\n"
                        f"Google 측 일시적 장애입니다. 다음 중 하나를 시도해보세요:\n"
                        f"  • 잠시 후(2~5분) 다시 시작 — 보통 곧 회복됨\n"
                        f"  • 다른 모델로 변경: gemini-2.0-flash, gemini-1.5-flash\n"
                        f"  • 다른 번역기(Claude / DeepL 등)로 전환\n"
                        f"원본 응답: {resp.text[:200]}"
                    )
                raise TranslationError(f"Gemini 오류 {resp.status_code}: {resp.text[:300]}")
            data = resp.json()

            # 차단/빈 응답 처리
            cands = data.get("candidates") or []
            if not cands:
                pf = data.get("promptFeedback", {})
                reason = pf.get("blockReason") or "응답에 candidates 없음"
                raise TranslationError(f"Gemini 차단/빈 응답: {reason} | {str(data)[:300]}")
            cand = cands[0]
            parts = (cand.get("content") or {}).get("parts") or []
            if not parts or "text" not in parts[0]:
                fr = cand.get("finishReason", "?")
                raise TranslationError(f"Gemini 응답에 텍스트 없음 (finishReason={fr}) | {str(cand)[:300]}")
            return _strip_llm_preamble(parts[0]["text"])

        return self._rot.run(keys, call)


# ──────────────────────────────────────────────────────────────────────────────
# Claude CLI (구독 사용)
# ──────────────────────────────────────────────────────────────────────────────

# Windows에서 windowed 부모(pythonw / PyInstaller --noconsole 빌드)가
# 콘솔 자식 프로세스(claude.exe)를 spawn할 때 콘솔창이 깜빡 뜨는 것을 막음.
# 다른 OS에서는 무시됨.
if sys.platform == "win32":
    _NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _NO_WINDOW_KW = {}


def _can_execute(path: str) -> bool:
    """파일을 실제로 실행해 보고 동작하면 True. os.path.isfile 우회용
    (일부 Windows 환경에서 stat은 실패해도 CreateProcess는 성공함)."""
    if not path:
        return False
    try:
        r = subprocess.run([path, "--version"], capture_output=True, timeout=15,
                           **_NO_WINDOW_KW)
        return r.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def _find_claude_cli(override: str = "") -> str:
    """claude 실행파일 위치 탐색. 각 후보를 실제 실행해 검증."""
    candidates = []
    if override:
        candidates.append(override)

    found = shutil.which("claude")
    if found:
        candidates.append(found)

    appdata = os.environ.get("APPDATA", "")
    if appdata:
        # Claude Desktop 번들 (Windows) — 최신 버전 우선
        try:
            bundled = sorted(
                glob.glob(os.path.join(appdata, "Claude", "claude-code", "*", "claude.exe")),
                reverse=True,
            )
            candidates.extend(bundled)
        except Exception:
            pass
        # 디렉토리 직접 열거(glob 실패 대비)
        base = os.path.join(appdata, "Claude", "claude-code")
        try:
            for sub in sorted(os.listdir(base), reverse=True):
                candidates.append(os.path.join(base, sub, "claude.exe"))
        except Exception:
            pass
        # npm 전역
        candidates.append(os.path.join(appdata, "npm", "claude.cmd"))

    # 공식 네이티브 인스톨러 기본 위치 (~/.local/bin)
    home = os.path.expanduser("~")
    candidates.extend([
        os.path.join(home, ".local", "bin", "claude.exe"),
        os.path.join(home, ".local", "bin", "claude"),
    ])

    # macOS/Linux 보편 위치
    candidates.extend([
        "/usr/local/bin/claude",
        os.path.expanduser("~/.claude/local/claude"),
    ])

    seen = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if _can_execute(c):
            return c
    return ""


# 구독 사용량/쿼터 한도에 도달했음을 시사하는 stderr 패턴
_CLAUDE_QUOTA_PATTERNS = re.compile(
    r"(usage limit|limit reached|rate.?limit|too many requests|quota|"
    r"weekly limit|5-?hour limit|reached the .* limit|you('| ha)ve reached)",
    re.IGNORECASE,
)


def _claude_tokens_of(cfg) -> list:
    """oauth_tokens 리스트(또는 줄바꿈 문자열) → 정규화된 토큰 리스트"""
    toks = cfg.get("oauth_tokens") or []
    if isinstance(toks, str):
        toks = [t.strip() for t in toks.splitlines() if t.strip()]
    else:
        toks = [str(t).strip() for t in toks if str(t).strip()]
    return toks


class ClaudeTranslator:
    name = "Claude (CLI / 구독)"

    def __init__(self):
        self._rot = _KeyRotator()
        # 매 청크마다 _find_claude_cli 가 --version 실행해서 콘솔창 띄우는 것 방지.
        # (override, 검증된 경로) 형태로 캐시.
        self._path_cache = ("", "")

    def _resolve_path(self, override: str) -> str:
        cached_override, cached_path = self._path_cache
        # 같은 override 로 이미 검증된 경로가 있고 파일이 그대로면 재사용.
        # (os.path.isfile 만으로 빠르게 sanity check — subprocess 안 띄움)
        if cached_path and cached_override == override and os.path.isfile(cached_path):
            return cached_path
        path = _find_claude_cli(override)
        self._path_cache = (override, path)
        return path

    def translate(self, text, source_lang_name, target_lang_name, cfg):
        override = (cfg.get("path") or "").strip()
        path = self._resolve_path(override)
        if not path:
            raise TranslationError(
                "claude CLI를 찾을 수 없습니다.\n"
                "PowerShell에서 다음을 실행해 설치하세요:\n"
                "  irm https://claude.ai/install.ps1 | iex\n"
                "또는 설정에서 claude 실행파일 경로를 직접 지정하세요."
            )
        model = (cfg.get("model") or "haiku").strip() or "haiku"

        src = source_lang_name or "자동감지"
        tgt = target_lang_name or "Korean"
        system = (f"You are a professional translator. "
                  f"Translate the user's text from {src} to {tgt}.\n\n"
                  f"OUTPUT RULES (these are strict):\n"
                  f"1. Output ONLY the translated text. Nothing else.\n"
                  f"2. Do NOT think out loud, deliberate, or narrate your process.\n"
                  f"3. Do NOT use <thinking>, <reasoning>, or any meta tags.\n"
                  f"4. Do NOT write a preface, header, label, or commentary "
                  f"(no \"Here's the translation:\", no \"Translation:\", etc.).\n"
                  f"5. The VERY FIRST character of your response MUST be the "
                  f"first character of the translated text itself.\n"
                  f"6. The VERY LAST character of your response MUST be the "
                  f"last character of the translated text itself.\n\n"
                  + _llm_extras(cfg))

        cmd = [
            path,
            "--print",
            "--output-format", "text",
            "--model", model,
            "--tools", "",
            "--system-prompt", system,
        ]

        def run_once(token: str) -> str:
            # 토큰이 있으면 환경변수로 주입 — CLI는 CLAUDE_CODE_OAUTH_TOKEN 을 우선 사용
            env = os.environ.copy()
            if token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            else:
                env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

            try:
                result = subprocess.run(
                    cmd,
                    input=text,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                    env=env,
                    **_NO_WINDOW_KW,
                )
            except subprocess.TimeoutExpired:
                raise TranslationError("Claude CLI 응답 시간 초과 (5분)")
            except FileNotFoundError:
                raise TranslationError(f"Claude CLI 실행 실패: 경로를 찾을 수 없음\n{path}")
            except OSError as e:
                raise TranslationError(f"Claude CLI 실행 오류: {e}")

            if result.returncode != 0:
                err = (result.stderr or result.stdout or "").strip()
                if not err:
                    err = f"(exit {result.returncode})"
                # 사용량 한도/쿼터 → 다음 토큰으로 회전
                if _CLAUDE_QUOTA_PATTERNS.search(err):
                    raise _RateLimited(f"Claude CLI 한도: {err[:200]}")
                raise TranslationError(f"Claude CLI 오류: {err[:400]}")
            return _strip_llm_preamble(result.stdout or "")

        tokens = _claude_tokens_of(cfg)
        if not tokens:
            # 토큰 미설정 — 시스템에 로그인된 계정 그대로 사용
            return run_once("")
        return self._rot.run(tokens, run_once)


# ──────────────────────────────────────────────────────────────────────────────
# Local LLM (OpenAI 호환 엔드포인트 — koboldcpp / LM Studio / Ollama / 자체 서버)
# ──────────────────────────────────────────────────────────────────────────────

# 사용자가 시스템 프롬프트를 비웠을 때만 사용되는 폴백.
# config.py 의 DEFAULT_CONFIG["apis"]["local"]["system_prompt"] 와 의미상 일치.
_LOCAL_DEFAULT_SYSTEM = (
    "당신은 전문 번역가입니다. 주어진 일본어 본문을 한국어로 번역하세요.\n\n"
    "★ 가장 중요: 응답은 반드시 한국어(한글)로 작성합니다. "
    "일본어 문자(히라가나·카타카나)를 그대로 복사하면 안 됩니다. "
    "원문을 그대로 출력하는 것은 명백한 번역 실패입니다.\n\n"
    "★ 고유명사(인명·기술명·괴물 이름)는 한글 음역으로 옮깁니다. "
    "예: ペルチェ → 페르체, グランド・ゴーレム → 그랜드 골렘, "
    "アイテム・ボックス → 아이템 박스.\n\n"
    "★ 원문의 표현·어투·수위는 한국어로 그대로 보존하고, "
    "자체 검열이나 완곡화 없이 충실하게 옮기세요.\n\n"
    "★ 번역문만 출력하세요. 사과·해설·메타 코멘트는 쓰지 마세요."
)


def _collapse_long_runs(text: str, threshold: int = 50, keep: int = 5) -> str:
    """입력에서 같은 문자가 N자 이상 연속이면 압축 표기로 치환.
    검열 마커(■) / 비명(あああ / イィイィ) 같은 패턴이 모델을 lock-in 시켜서
    runaway 생성으로 빠지는 걸 방지한다. 원문의 의미는 글자 수가 아니라
    '검열' 또는 '비명' 자체이므로, 개수만 표기에 보존하면 문맥 손실 없음.

    threshold: 이 길이 이상 반복이면 압축 (기본 50자)
    keep:      앞뒤로 남길 원본 글자 수 (기본 5자씩)

    예: ■ × 2000 → ■■■■■…[같은 문자 2000자 생략]…■■■■■
    """
    if threshold < 2 or not text:
        return text
    pattern = re.compile(r"(.)\1{" + str(threshold - 1) + r",}", flags=re.DOTALL)

    def replace(m):
        ch = m.group(1)
        n = len(m.group(0))
        return f"{ch * keep}…[같은 문자 {n}자 생략]…{ch * keep}"

    return pattern.sub(replace, text)


_VERIFY_SYSTEM_PROMPT = (
    "당신은 번역 품질 검수자입니다. 주어지는 일본어 원문과 한국어 번역을 비교하여 "
    "다음 조건을 모두 만족하는지 판정합니다.\n\n"
    "[검수 조건]\n"
    "1. 원문의 내용이 한국어로 옮겨졌고, 일본어 본문이 그대로 남아있지 않다.\n"
    "2. 누락된 문장·문단이 없고, 의미가 원문과 일치한다.\n"
    "3. 사과·거부·검토 메타 텍스트가 포함되지 않은 순수 번역만 있다.\n\n"
    "[출력 규칙 — 반드시 지키세요]\n"
    "- 첫 줄에 정확히 \"PASS\" 또는 \"FAIL\" 한 단어만 출력합니다.\n"
    "- FAIL이면 둘째 줄에 한 문장으로 사유를 적습니다.\n"
    "- 그 외 어떤 설명·번역·재작성도 출력하지 마세요."
)


class LocalLLMTranslator:
    """OpenAI 호환 /v1/chat/completions 엔드포인트로 호출.
    특정 모델·백엔드에 묶이지 않고 base_url / model / system_prompt 모두
    사용자가 설정 가능. 키 로테이션 없음 (단일 엔드포인트).

    검수가 활성화된 경우 (cfg.verify_enabled=True, 기본값) 번역마다
    프로그램적 체크 → 동일 모델로 LLM 검수 → PASS면 채택, FAIL이면
    temperature를 올려가며 재시도. 모두 실패하면 청크를 실패 마킹."""

    name = "Local LLM"

    # ── 공개 진입점 ─────────────────────────────────────────────────────────

    def translate(self, text, source_lang_name, target_lang_name, cfg):
        if not text or not text.strip():
            return text

        # 입력 전처리 — 긴 반복 (■ 검열, あああ 비명 등)을 압축해서 모델 lock-in 회피.
        # 이후 echo 검출 / 번역 호출 / 검수 모두 압축된 텍스트를 source로 사용.
        text = _collapse_long_runs(text)

        verify_enabled = bool(cfg.get("verify_enabled", True))
        try:
            max_attempts = int(cfg.get("verify_max_attempts", 3))
        except (TypeError, ValueError):
            max_attempts = 3
        if not verify_enabled:
            max_attempts = 1
        try:
            base_temp = float(cfg.get("temperature", 0.4))
        except (TypeError, ValueError):
            base_temp = 0.4

        last_translation = ""
        failures = []

        for attempt in range(max_attempts):
            # 시도마다 temperature 점진 상승 — 같은 echo가 반복되는 걸 방지
            attempt_temp = min(1.0, base_temp + 0.2 * attempt)
            attempt_cfg = dict(cfg)
            attempt_cfg["temperature"] = attempt_temp

            translation, finish_reason = self._translate_once(text, attempt_cfg)
            last_translation = translation

            if not verify_enabled:
                return translation

            # 단계 1: 프로그램적 체크 (LLM 호출 없이 빠르게)
            prog_err = self._programmatic_check(
                text, translation, target_lang_name, finish_reason=finish_reason)
            if prog_err:
                failures.append(
                    f"시도 {attempt + 1}/{max_attempts} (T={attempt_temp:.2f}): "
                    f"자동검출 — {prog_err}"
                )
                continue

            # 단계 2: 동일 모델로 PASS/FAIL 검수
            try:
                verdict = self._verify_translation(text, translation, cfg)
            except TranslationError:
                # 검수 호출 자체 실패 — 번역 결과는 살리고 통과 처리
                return translation

            if self._verdict_is_pass(verdict):
                return translation
            failures.append(
                f"시도 {attempt + 1}/{max_attempts} (T={attempt_temp:.2f}): "
                f"LLM 검수 — {verdict.strip()[:200]}"
            )

        raise TranslationError(
            f"Local LLM 검수 {max_attempts}회 모두 실패.\n"
            + "\n".join(failures)
            + f"\n\n마지막 번역(앞부분):\n{last_translation[:300]}"
        )

    # ── 단일 번역/검수 호출 ─────────────────────────────────────────────────

    def _translate_once(self, text: str, cfg: dict):
        """번역 1회 시도 — 시스템 프롬프트 구성 + API 호출.
        반환: (content, finish_reason). _api_call의 튜플을 그대로 전달."""
        system_base = (cfg.get("system_prompt") or "").strip() or _LOCAL_DEFAULT_SYSTEM
        extras = _llm_extras(cfg)
        system = (system_base + "\n\n" + extras).strip() if extras else system_base
        return self._api_call(cfg, system, text)

    def _verify_translation(self, source: str, translation: str, cfg: dict) -> str:
        """동일 모델로 검수. 첫 줄 PASS/FAIL 판정 텍스트 반환.
        finish_reason은 검수 단계에서 의미 없으므로 버리고 content만."""
        user_content = f"[원문]\n{source}\n\n[번역]\n{translation}"
        # 검수는 결정적으로 — 낮은 temperature, 짧은 응답
        verify_cfg = dict(cfg)
        verify_cfg["temperature"] = 0.1
        content, _ = self._api_call(
            verify_cfg, _VERIFY_SYSTEM_PROMPT, user_content,
            override_max_tokens=200,
        )
        return content

    # ── 검수 판정 ──────────────────────────────────────────────────────────

    @staticmethod
    def _verdict_is_pass(verdict: str) -> bool:
        """검수 응답에서 PASS/FAIL 판정. 둘 다 없으면 보수적으로 FAIL."""
        head = (verdict or "").strip().upper()[:80]
        if "FAIL" in head:
            return False
        if "PASS" in head:
            return True
        return False

    @staticmethod
    def _programmatic_check(source: str, translation: str, target_lang_name,
                            finish_reason=None) -> str:
        """LLM 호출 없이 잡을 수 있는 명백한 실패 모드 검출.
        통과하면 빈 문자열, 실패면 사유 반환.

        finish_reason 활용:
          'length' — max_tokens 도달로 잘림. runaway lock-in 강력한 신호.
          'stop'   — EOS 자연 종료. 긴 반복도 literary scream일 가능성.
          None     — 서버가 안 알려줌. 보수적으로 'stop'처럼 취급."""
        if not translation or not translation.strip():
            return "빈 응답"
        src_norm = source.strip()
        tgt_norm = translation.strip()
        # 완전 echo
        if src_norm == tgt_norm:
            return "원문 echo (출력 = 입력)"
        # 부분 echo — 입력 앞 200자가 출력 앞에 그대로 등장
        head = src_norm[:200]
        if len(head) >= 50 and tgt_norm.startswith(head):
            return "원문 echo (입력 앞 200자가 출력 앞에 그대로 등장)"
        # Runaway 반복 — 같은 문자 연속 등장. 임계값을 finish_reason에 따라 분기:
        #   - finish_reason='length' (max_tokens 도달, 잘림): 50자만 넘어도 의심
        #   - 'stop'/None (자연 종료): 200자 이상만 의심 (literary scream 허용)
        m = re.search(r"(.)\1{49,}", tgt_norm, flags=re.DOTALL)
        if m:
            ch = m.group(1)
            run_len = len(m.group(0))
            disp = ch if not ch.isspace() else repr(ch)
            is_truncated = (finish_reason == "length")
            if is_truncated:
                # 잘린 응답 + 50+ 반복 = runaway lock-in 거의 확실
                return (f"비정상 반복 출력 (문자 {disp} 가 {run_len}자 연속, "
                        f"max_tokens 도달로 잘림 — runaway lock-in)")
            elif run_len >= 200:
                # 자연 종료여도 200자 이상 반복은 의심
                return (f"비정상 반복 출력 (문자 {disp} 가 {run_len}자 연속, "
                        f"자연 종료여도 200자 초과)")
            # 50~199 + 자연 종료 = literary scream 등 정상 표현으로 간주, 통과
        # 일본어 잔존율 (목표가 한국어일 때만 적용)
        is_korean_target = False
        if isinstance(target_lang_name, str):
            tn = target_lang_name.strip().lower()
            is_korean_target = "한국어" in target_lang_name or tn == "korean"
        if is_korean_target:
            ja_chars = sum(
                1 for c in tgt_norm
                if ("぀" <= c <= "ゟ") or ("゠" <= c <= "ヿ")
            )
            non_space = sum(1 for c in tgt_norm if not c.isspace())
            if non_space > 0:
                ratio = ja_chars / non_space
                if ratio > 0.3:
                    return f"일본어 문자 잔존율 {ratio:.0%} (>30%)"
        return ""

    # ── 저수준 API 호출 ────────────────────────────────────────────────────

    def _api_call(self, cfg: dict, system: str, user_text: str,
                  *, override_max_tokens=None):
        """OpenAI 호환 chat/completions 단일 호출. 번역 / 검수 양쪽에서 공용.

        반환: (content: str, finish_reason: Optional[str])
          finish_reason 은 'stop' (EOS 자연 종료), 'length' (max_tokens 도달),
          또는 None (서버가 안 보내줌). 호출자가 runaway 판별에 활용."""
        base_url = (cfg.get("base_url") or "http://localhost:5001/v1").strip().rstrip("/")
        api_key  = (cfg.get("api_key") or "").strip()
        model    = (cfg.get("model") or "local").strip() or "local"
        try:
            temperature = float(cfg.get("temperature", 0.4))
        except (TypeError, ValueError):
            temperature = 0.4
        try:
            repeat_penalty = float(cfg.get("repeat_penalty", 1.1))
        except (TypeError, ValueError):
            repeat_penalty = 1.1
        try:
            frequency_penalty = float(cfg.get("frequency_penalty", 0.5))
        except (TypeError, ValueError):
            frequency_penalty = 0.5
        try:
            max_tokens = int(cfg.get("max_tokens", 8192))
        except (TypeError, ValueError):
            max_tokens = 8192
        if override_max_tokens is not None:
            try:
                max_tokens = int(override_max_tokens)
            except (TypeError, ValueError):
                pass
        try:
            timeout = int(cfg.get("timeout", 300))
        except (TypeError, ValueError):
            timeout = 300

        # Gemma 등 system role 미지원 모델 호환 (자세한 설명은 config.py 참조)
        merge_system = bool(cfg.get("merge_system_into_user", True))
        if merge_system and system:
            messages = [{"role": "user", "content": f"{system}\n\n{user_text}"}]
        else:
            messages = [
                {"role": "system", "content": system},
                {"role": "user",   "content": user_text},
            ]

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # OpenAI 표준 — 누적 빈도 기반 anti-repetition. runaway 차단.
            "frequency_penalty": frequency_penalty,
            # OpenAI 표준엔 없는 필드 — koboldcpp / LM Studio 등이 인식.
            # 미지원 서버는 무시함 (호환성 OK).
            "repeat_penalty": repeat_penalty,
        }

        try:
            resp = _post_with_retry(
                f"{base_url}/chat/completions",
                headers=headers,
                json=body,
                timeout=timeout,
            )
        except requests.exceptions.ConnectionError:
            raise TranslationError(
                f"Local LLM 서버에 연결할 수 없습니다.\n"
                f"  • Endpoint: {base_url}\n"
                f"  • F:\\Joy4_LLM\\scripts\\start_kobold.bat 을 실행했는지 확인하세요.\n"
                f"  • 또는 LM Studio / Ollama 등 OpenAI 호환 서버가 실행 중인지 확인하세요."
            )
        except requests.exceptions.Timeout:
            raise TranslationError(
                f"Local LLM 응답 시간 초과 ({timeout}s).\n"
                f"  • 모델 로딩 중이거나 청크가 너무 클 수 있습니다.\n"
                f"  • 설정에서 max_chars 를 줄이거나 timeout 을 늘리세요."
            )

        if resp.status_code != 200:
            raise TranslationError(
                f"Local LLM 오류 {resp.status_code}: {resp.text[:300]}"
            )
        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise TranslationError(
                f"Local LLM 응답 형식 오류 ({type(e).__name__}): {resp.text[:300]}"
            )
        return _strip_llm_preamble(content or ""), finish_reason


# ──────────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────────

TRANSLATORS = {
    "openai":   OpenAITranslator(),
    "papago":   PapagoTranslator(),
    "google":   GoogleTranslateTranslator(),
    "deepl":    DeepLTranslator(),
    "gemini":   GeminiTranslator(),
    "claude":   ClaudeTranslator(),
    "local":    LocalLLMTranslator(),
}
