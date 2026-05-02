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
        "local": {
            # OpenAI 호환 엔드포인트 — koboldcpp / LM Studio / Ollama / 자체 서버
            "base_url": "http://localhost:5001/v1",
            "api_key":  "sk-local",                 # 더미. 일부 클라이언트가 요구해서 유지
            "model":    "Gemma-4-E4B-Uncensored",   # koboldcpp는 무시 · LM Studio 등은 식별자 사용
            "system_prompt": (
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
            ),
            # 0.1은 결정적이라 echo 패턴에 빠지면 못 나옴. 0.4 정도면
            # 정상 청크는 그대로 잘 나오고 echo 청크는 다른 길로 새는 가능성 ↑.
            "temperature":   0.4,
            # 1.05는 사실상 페널티 없는 수준. 비명 등 반복 입력에서 모델이 같은
            # 문자 무한 생성하는 runaway 루프에 빠짐. 1.1이면 정상 번역 품질에는
            # 영향 거의 없으면서 반복 lock-in을 막음.
            "repeat_penalty": 1.1,
            "max_tokens":    8192,          # 응답 최대 토큰 (안 보내면 koboldcpp가 1024로 잘라버림)
            # Gemma 등 system role 미지원 모델 호환 — system 지시를 user 메시지 앞에
            # 병합해서 단일 user turn으로 전송. False로 두면 OpenAI 표준대로 분리 전송.
            "merge_system_into_user": True,
            # 번역 후 동일 모델로 검수, 실패 시 temperature를 올려가며 재시도.
            "verify_enabled":      True,
            "verify_max_attempts": 3,
            "max_chars":     4000,
            "timeout":       180,
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
