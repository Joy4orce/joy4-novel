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


def _post_with_retry(url, *, max_retries=3, backoff=1.5, **kwargs):
    """requests.post 의 일시적 실패(타임아웃/연결끊김)를 자동 재시도.
    HTTP 에러 응답(4xx/5xx)은 그대로 반환 — 호출자가 처리."""
    last_err = None
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            return requests.post(url, **kwargs)
        except _TRANSIENT_EXCS as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(delay)
                delay = min(delay * backoff, 10.0)
            else:
                raise
    # 도달 불가
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


# API별 청크당 최대 문자 수 (요청 타임아웃 방지 및 진행률 가시성)
MAX_CHARS = {
    "openai":   6000,
    "papago":   4500,
    "google":   4500,
    "deepl":    4000,
    "gemini":   6000,
    "claude":   6000,
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
        prompt = (f"Translate the following text from {src} to {tgt}. "
                  f"Return only the translated text without any explanation.\n\n"
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
            return parts[0]["text"].strip()

        return self._rot.run(keys, call)


# ──────────────────────────────────────────────────────────────────────────────
# Claude CLI (구독 사용)
# ──────────────────────────────────────────────────────────────────────────────

def _can_execute(path: str) -> bool:
    """파일을 실제로 실행해 보고 동작하면 True. os.path.isfile 우회용
    (일부 Windows 환경에서 stat은 실패해도 CreateProcess는 성공함)."""
    if not path:
        return False
    try:
        r = subprocess.run([path, "--version"], capture_output=True, timeout=15)
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

    def translate(self, text, source_lang_name, target_lang_name, cfg):
        override = (cfg.get("path") or "").strip()
        path = _find_claude_cli(override)
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
                  f"Translate the user's text from {src} to {tgt}. "
                  f"Return only the translated text without any explanation, "
                  f"preface, or commentary.\n\n"
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
            return (result.stdout or "").strip()

        tokens = _claude_tokens_of(cfg)
        if not tokens:
            # 토큰 미설정 — 시스템에 로그인된 계정 그대로 사용
            return run_once("")
        return self._rot.run(tokens, run_once)


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
}
