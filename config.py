import json
import os
import sys


def user_data_dir() -> str:
    """사용자 설정/로그를 저장할 디렉터리.
    - PyInstaller 빌드본: %APPDATA%\\Joy4_Novel (쓰기 가능 보장)
    - 개발 실행(.py):      스크립트 폴더 (포터블)
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "Joy4_Novel")
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(d, exist_ok=True)
    return d


CONFIG_FILE = os.path.join(user_data_dir(), "config.json")

DEFAULT_CONFIG = {
    "selected_api": "openai",
    "source_lang": "자동감지",
    "target_lang": "한국어",
    "translation_prompt": "",   # 번역 시 참고할 추가 지시사항
    "user_dictionary": "",      # 한 줄에 "원문=번역" 형식의 고유명사 사전
    "download_dir": "",         # 소설 다운로드 저장 폴더 (빈 값이면 ~/Downloads)
    "apis": {
        "openai": {
            "api_keys": [],
            "model": "gpt-4o-mini",
        },
        "papago": {
            "client_id": "",
            "client_secret": "",
        },
        "google": {
            "api_keys": [],
        },
        "deepl": {
            "api_keys": [],
            "use_free": True,
        },
        "gemini": {
            "api_keys": [],
            "model": "gemini-2.5-flash",
        },
        "claude": {
            "path": "",          # 빈 값이면 자동 탐색 (Claude Desktop 번들 → PATH → npm 전역)
            "model": "haiku",    # haiku / sonnet / opus 또는 전체 모델 ID
            "oauth_tokens": [],  # `claude setup-token` 으로 발급한 장기 토큰. 비우면 시스템 로그인 사용
        },
        "pixiv": {
            "session_id": "",
        },
    },
}

# 단일 api_key 를 api_keys 리스트로 이관 (구버전 호환)
_MULTIKEY_APIS = ("openai", "google", "deepl", "gemini")

# Google이 v1beta에서 내린 Gemini 모델 → 후속 모델 매핑
_GEMINI_MODEL_MIGRATIONS = {
    "gemini-1.5-flash":    "gemini-2.5-flash",
    "gemini-1.5-flash-8b": "gemini-2.5-flash-lite",
    "gemini-1.5-pro":      "gemini-2.5-pro",
    "gemini-1.0-pro":      "gemini-2.5-pro",
    "gemini-pro":          "gemini-2.5-pro",
}


def load() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Merge missing keys from default
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)
    for api_key, defaults in DEFAULT_CONFIG["apis"].items():
        if api_key not in cfg["apis"]:
            cfg["apis"][api_key] = defaults.copy()
        else:
            merged = defaults.copy()
            merged.update(cfg["apis"][api_key])
            cfg["apis"][api_key] = merged

    # 구버전 단일 api_key 를 api_keys 리스트로 이관
    for api_id in _MULTIKEY_APIS:
        sub = cfg["apis"].get(api_id, {})
        old_key = sub.pop("api_key", "")
        if isinstance(sub.get("api_keys"), str):
            sub["api_keys"] = [k.strip() for k in sub["api_keys"].splitlines() if k.strip()]
        if not sub.get("api_keys") and isinstance(old_key, str) and old_key.strip():
            sub["api_keys"] = [old_key.strip()]

    # Claude OAuth 토큰 — 문자열로 저장된 경우 줄 단위 리스트로 정규화
    cl = cfg["apis"].get("claude", {})
    toks = cl.get("oauth_tokens")
    if isinstance(toks, str):
        cl["oauth_tokens"] = [t.strip() for t in toks.splitlines() if t.strip()]
    elif isinstance(toks, list):
        cl["oauth_tokens"] = [str(t).strip() for t in toks if str(t).strip()]
    else:
        cl["oauth_tokens"] = []

    # 폐기된 Gemini 모델 자동 교체 (v1beta에서 404 반환)
    gem = cfg["apis"].get("gemini", {})
    cur_model = (gem.get("model") or "").strip()
    if cur_model in _GEMINI_MODEL_MIGRATIONS:
        gem["model"] = _GEMINI_MODEL_MIGRATIONS[cur_model]

    # 더 이상 지원하지 않는 API 항목 제거 (구버전 호환)
    valid_apis = set(DEFAULT_CONFIG["apis"].keys())
    for api_id in list(cfg["apis"].keys()):
        if api_id not in valid_apis:
            cfg["apis"].pop(api_id, None)
    if cfg.get("selected_api") not in valid_apis:
        cfg["selected_api"] = DEFAULT_CONFIG["selected_api"]
    return cfg


def save(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
