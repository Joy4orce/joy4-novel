"""
Joy4_Novel - 다중 AI API 번역기
지원: ChatGPT, Naver Papago, Google Translate, DeepL, Google Gemini, Claude, Local LLM
"""

import os
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading

import requests

import config as cfg_module
from translators import TRANSLATORS, LANG_CODES, TranslationError, MAX_CHARS, chunk_text
from crawler import detect_crawler, crawl_novel, sanitize_filename, CrawlerError
from logger import TranslationLogger
import splitter
import progress as progress_mod

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _AppBase = TkinterDnD.Tk
except Exception:
    _AppBase = tk.Tk
    DND_FILES = None

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except Exception:
    HAS_PIL = False


def _make_btn_icon(size: int, circle_color: str, arrow_color: str) -> "ImageTk.PhotoImage":
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill=circle_color)
    # 오른쪽 방향 삼각형 (▶)
    m = size // 5
    pts = [
        (m + 2,          m),
        (m + 2,          size - m),
        (size - m + 2,   size // 2),
    ]
    d.polygon(pts, fill=arrow_color)
    return ImageTk.PhotoImage(img)

# ──────────────────────────────────────────────────────────────────────────────
# API 목록 및 표시 이름
# ──────────────────────────────────────────────────────────────────────────────

API_LIST = [
    ("openai",   "ChatGPT (OpenAI)"),
    ("papago",   "Naver Papago"),
    ("google",   "Google Translate"),
    ("deepl",    "DeepL"),
    ("gemini",   "Google Gemini"),
    ("claude",   "Claude (CLI / 구독)"),
    ("local",    "Local LLM"),
]
API_ID_BY_DISPLAY = {v: k for k, v in API_LIST}
API_DISPLAY_BY_ID = {k: v for k, v in API_LIST}

# 설정창 사이드바에는 번역 API + 크롤러 사이트 설정도 함께 표시
SETTINGS_SIDEBAR = API_LIST + [("pixiv", "Pixiv (크롤러)")]

LANGUAGES = ["자동감지", "한국어", "영어", "일본어", "중국어(간체)", "중국어(번체)",
             "프랑스어", "독일어", "스페인어", "러시아어"]

FONT_MAIN  = ("Malgun Gothic", 10)
FONT_LARGE = ("Malgun Gothic", 11)
FONT_TITLE = ("Malgun Gothic", 13, "bold")

BG      = "#1e1e2e"
BG2     = "#2a2a3e"
BG3     = "#313145"
ACCENT  = "#7c6af7"
FG      = "#e0e0f0"
FG2     = "#a0a0c0"
BTN_BG  = "#7c6af7"
BTN_FG  = "#ffffff"
BORDER  = "#3a3a5a"


# ──────────────────────────────────────────────────────────────────────────────
# 설정 다이얼로그
# ──────────────────────────────────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg: dict):
        super().__init__(parent)
        self.cfg = cfg
        self.result = None

        self.title("API 설정")
        self.geometry("640x680")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()

        self._vars = {}
        self._panels = {}
        self._sidebar_btns = {}
        self._current = None

        self._build_ui()
        self._load_values()
        self._select("openai")

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"640x680+{x}+{y}")

    # ── UI 구성 ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # 사이드바
        sidebar = tk.Frame(self, bg=BG2, width=130)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="API 선택", font=("Malgun Gothic", 9),
                 bg=BG2, fg=FG2).pack(pady=(12, 6))

        for api_id, display in SETTINGS_SIDEBAR:
            btn = tk.Button(sidebar, text=display, font=("Malgun Gothic", 9),
                            bg=BG2, fg=FG, relief="flat", anchor="w",
                            padx=12, pady=6, cursor="hand2",
                            command=lambda a=api_id: self._select(a))
            btn.pack(fill="x", padx=4, pady=1)
            self._sidebar_btns[api_id] = btn

        # 오른쪽 영역
        right = tk.Frame(self, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        # 패널 컨테이너
        self._panel_container = tk.Frame(right, bg=BG)
        self._panel_container.pack(fill="both", expand=True, padx=16, pady=12)

        # 각 API 패널 미리 생성
        self._panels["openai"]   = self._make_panel_openai()
        self._panels["papago"]   = self._make_panel_papago()
        self._panels["google"]   = self._make_panel_google()
        self._panels["deepl"]    = self._make_panel_deepl()
        self._panels["gemini"]   = self._make_panel_gemini()
        self._panels["claude"]   = self._make_panel_claude()
        self._panels["local"]    = self._make_panel_local()
        self._panels["pixiv"]    = self._make_panel_pixiv()

        # 하단 버튼
        btn_bar = tk.Frame(right, bg=BG2)
        btn_bar.pack(side="bottom", fill="x", padx=0, pady=0)
        tk.Button(btn_bar, text="저장", font=FONT_MAIN,
                  bg=BTN_BG, fg=BTN_FG, relief="flat", padx=20, pady=7,
                  cursor="hand2", command=self._save).pack(side="right", padx=(6, 12), pady=8)
        tk.Button(btn_bar, text="취소", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=20, pady=7,
                  cursor="hand2", command=self.destroy).pack(side="right", pady=8)

    def _select(self, api_id: str):
        if self._current:
            self._panels[self._current].pack_forget()
            self._sidebar_btns[self._current].configure(bg=BG2, fg=FG)
        self._panels[api_id].pack(fill="both", expand=True)
        self._sidebar_btns[api_id].configure(bg=ACCENT, fg=BTN_FG)
        self._current = api_id

    # ── 패널 생성 헬퍼 ────────────────────────────────────────────────────────

    def _make_panel(self, title, hint_text):
        f = tk.Frame(self._panel_container, bg=BG)
        tk.Label(f, text=title, font=FONT_TITLE, bg=BG, fg=FG,
                 anchor="w").pack(fill="x", pady=(0, 8))
        return f

    def _row(self, parent, label, var_key, show="", placeholder=""):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=5)
        tk.Label(row, text=label, font=FONT_MAIN, bg=BG, fg=FG2,
                 width=14, anchor="w").pack(side="left")
        var = tk.StringVar()
        e = tk.Entry(row, textvariable=var, show=show, font=FONT_MAIN,
                     bg=BG3, fg=FG, insertbackground=FG,
                     relief="flat", bd=6, width=30)
        e.pack(side="left", fill="x", expand=True)
        if placeholder:
            tk.Label(row, text=placeholder, font=("Malgun Gothic", 8),
                     bg=BG, fg=FG2).pack(side="left", padx=(6, 0))
        self._vars[var_key] = var
        return var

    def _keys_row(self, parent, label, var_key, height=4):
        """여러 줄 입력 — 한 줄에 API 키 하나. 제한 시 다음 키로 자동 전환."""
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", pady=(5, 2))
        head = tk.Frame(wrap, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text=label, font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(side="left")
        tk.Label(head, text="  (한 줄에 하나씩 · 한도 초과 시 다음 키로 자동 전환)",
                 font=("Malgun Gothic", 8), bg=BG, fg=FG2).pack(side="left")
        txt = tk.Text(wrap, font=("Consolas", 9), bg=BG3, fg=FG,
                      insertbackground=FG, relief="flat", bd=0,
                      height=height, wrap="none", undo=True)
        txt.pack(fill="x", pady=(3, 0))
        self._vars[var_key] = txt
        return txt

    def _get_val(self, var_key):
        v = self._vars[var_key]
        if isinstance(v, tk.Text):
            return v.get("1.0", "end")
        return v.get()

    def _set_val(self, var_key, value):
        v = self._vars[var_key]
        if isinstance(v, tk.Text):
            v.delete("1.0", "end")
            if isinstance(value, list):
                value = "\n".join(str(x) for x in value)
            v.insert("1.0", value or "")
        else:
            v.set(value)

    def _parse_keys(self, var_key) -> list:
        raw = self._get_val(var_key)
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _check_row(self, parent, label, var_key):
        var = tk.BooleanVar()
        cb = tk.Checkbutton(parent, text=label, variable=var, font=FONT_MAIN,
                            bg=BG, fg=FG, selectcolor=BG3,
                            activebackground=BG, activeforeground=FG,
                            relief="flat", cursor="hand2")
        cb.pack(anchor="w", pady=4)
        self._vars[var_key] = var

    def _hint(self, parent, text):
        tk.Label(parent, text=text, font=("Malgun Gothic", 8),
                 bg=BG, fg=FG2, anchor="w").pack(fill="x", pady=(4, 0))

    # ── 각 API 패널 ──────────────────────────────────────────────────────────

    def _make_panel_openai(self):
        f = self._make_panel("ChatGPT (OpenAI)", "")
        self._keys_row(f, "API Keys", "openai_api_keys")
        self._row(f, "Model",   "openai_model",   placeholder="예: gpt-4o-mini")
        self._hint(f, "platform.openai.com/api-keys 에서 발급")
        return f

    def _make_panel_papago(self):
        f = self._make_panel("Naver Papago", "")
        self._row(f, "Client ID",     "papago_client_id",     show="*")
        self._row(f, "Client Secret", "papago_client_secret", show="*")
        self._hint(f, "developers.naver.com 에서 앱 등록 후 발급")
        return f

    def _make_panel_google(self):
        f = self._make_panel("Google Translate", "")
        self._keys_row(f, "API Keys", "google_api_keys", height=6)
        self._hint(f, "Google Cloud Console > Cloud Translation API 에서 발급\n"
                      "무료 할당량이 소진된 키는 자동으로 다음 키로 전환됩니다.")
        return f

    def _make_panel_deepl(self):
        f = self._make_panel("DeepL", "")
        self._keys_row(f, "API Keys", "deepl_api_keys")
        self._check_row(f, "무료 플랜 사용 (api-free.deepl.com)", "deepl_use_free")
        self._hint(f, "deepl.com/pro-api 에서 발급  |  무료: DeepL API Free")
        return f

    def _make_panel_gemini(self):
        f = self._make_panel("Google Gemini", "")
        self._keys_row(f, "API Keys", "gemini_api_keys")
        self._row(f, "Model",   "gemini_model",   placeholder="예: gemini-2.5-flash")
        self._hint(f, "aistudio.google.com/app/apikey 에서 발급")
        return f

    def _make_panel_claude(self):
        f = self._make_panel("Claude (CLI / 구독)", "")
        self._row(f, "claude 경로", "claude_path",
                  placeholder="비워두면 자동 탐색 (Claude Desktop 번들 우선)")
        self._row(f, "Model",       "claude_model",
                  placeholder="haiku / sonnet / opus")
        self._keys_row(f, "OAuth Tokens", "claude_oauth_tokens", height=3)
        self._hint(f,
                   "여러 구독(Pro · Team 등)을 함께 쓰려면 각 계정에서 `claude setup-token` 실행 후\n"
                   "출력된 토큰을 한 줄씩 붙여넣으세요. 비워두면 시스템에 로그인된 계정을 사용합니다.\n"
                   "모델은 별칭(haiku 등) 또는 전체 ID(claude-haiku-4-5) 사용 가능.")
        return f

    def _make_panel_local(self):
        f = self._make_panel("Local LLM (OpenAI 호환)", "")
        self._row(f, "Endpoint URL", "local_base_url",
                  placeholder="예: http://localhost:5001/v1")
        self._row(f, "API Key", "local_api_key", show="*",
                  placeholder="더미 OK (예: sk-local)")
        self._row(f, "Model 이름", "local_model",
                  placeholder="koboldcpp는 무시 · LM Studio는 모델 ID")

        # Temperature + Repeat Penalty 한 줄로 (좁은 입력칸 2개)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", pady=5)
        tk.Label(row, text="Temperature", font=FONT_MAIN, bg=BG, fg=FG2,
                 width=14, anchor="w").pack(side="left")
        var_t = tk.StringVar()
        tk.Entry(row, textvariable=var_t, font=FONT_MAIN, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", bd=6, width=8
                 ).pack(side="left")
        self._vars["local_temperature"] = var_t
        tk.Label(row, text="    Repeat Penalty", font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(side="left", padx=(20, 6))
        var_r = tk.StringVar()
        tk.Entry(row, textvariable=var_r, font=FONT_MAIN, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", bd=6, width=8
                 ).pack(side="left")
        self._vars["local_repeat_penalty"] = var_r

        # Max Tokens — 응답 길이 한도 (안 보내면 koboldcpp가 1024로 잘라버림)
        row2 = tk.Frame(f, bg=BG)
        row2.pack(fill="x", pady=5)
        tk.Label(row2, text="Max Tokens", font=FONT_MAIN, bg=BG, fg=FG2,
                 width=14, anchor="w").pack(side="left")
        var_mt = tk.StringVar()
        tk.Entry(row2, textvariable=var_mt, font=FONT_MAIN, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", bd=6, width=8
                 ).pack(side="left")
        tk.Label(row2, text="  응답 최대 토큰 (기본 8192) · 짧으면 번역이 중간에 잘림",
                 font=("Malgun Gothic", 8), bg=BG, fg=FG2, anchor="w"
                 ).pack(side="left", padx=(8, 0))
        self._vars["local_max_tokens"] = var_mt

        # Frequency Penalty — OpenAI 표준 누적 빈도 페널티 (runaway 차단)
        row_fp = tk.Frame(f, bg=BG)
        row_fp.pack(fill="x", pady=5)
        tk.Label(row_fp, text="Freq Penalty", font=FONT_MAIN, bg=BG, fg=FG2,
                 width=14, anchor="w").pack(side="left")
        var_fp = tk.StringVar()
        tk.Entry(row_fp, textvariable=var_fp, font=FONT_MAIN, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", bd=6, width=8
                 ).pack(side="left")
        tk.Label(row_fp, text="  반복 토큰 누적 페널티 (0~2.0, 기본 0.5) · runaway 방지",
                 font=("Malgun Gothic", 8), bg=BG, fg=FG2, anchor="w"
                 ).pack(side="left", padx=(8, 0))
        self._vars["local_frequency_penalty"] = var_fp

        # 검수 (Verify) — 동일 모델로 번역 검수, 실패 시 재시도
        row3 = tk.Frame(f, bg=BG)
        row3.pack(fill="x", pady=5)
        var_v = tk.BooleanVar()
        tk.Checkbutton(row3, text="검수 활성화 (실패 시 재번역)", variable=var_v,
                       font=FONT_MAIN, bg=BG, fg=FG, selectcolor=BG3,
                       activebackground=BG, activeforeground=FG,
                       relief="flat", cursor="hand2"
                       ).pack(side="left")
        self._vars["local_verify_enabled"] = var_v
        tk.Label(row3, text="  ·  최대 시도", font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(side="left", padx=(20, 6))
        var_va = tk.StringVar()
        tk.Entry(row3, textvariable=var_va, font=FONT_MAIN, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", bd=6, width=4
                 ).pack(side="left")
        tk.Label(row3, text="회", font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(side="left", padx=(4, 0))
        self._vars["local_verify_max_attempts"] = var_va

        # System Prompt (멀티라인) — 라벨 + 우측에 "기본값 복원" 버튼
        sp_head = tk.Frame(f, bg=BG)
        sp_head.pack(fill="x", pady=(10, 3))
        tk.Label(sp_head, text="System Prompt", font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(side="left")
        tk.Button(sp_head, text="기본값 복원", font=("Malgun Gothic", 9),
                  bg=BG3, fg=FG, relief="flat", padx=10, pady=2,
                  cursor="hand2", command=self._reset_local_system_prompt
                  ).pack(side="right")
        sp = tk.Text(f, font=FONT_MAIN, bg=BG3, fg=FG, insertbackground=FG,
                     relief="flat", bd=6, height=5, wrap="word", undo=True)
        sp.pack(fill="x")
        self._vars["local_system_prompt"] = sp

        # 연결 테스트 버튼 + 상태 라벨
        test_row = tk.Frame(f, bg=BG)
        test_row.pack(fill="x", pady=(10, 0))
        tk.Button(test_row, text="연결 테스트", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=14, pady=4,
                  cursor="hand2", command=self._test_local_connection
                  ).pack(side="left")
        self._local_test_label = tk.Label(test_row, text="", font=("Malgun Gothic", 9),
                                          bg=BG, fg=FG2, anchor="w")
        self._local_test_label.pack(side="left", padx=(10, 0))

        self._hint(f,
                   "koboldcpp / LM Studio / Ollama / 자체 OpenAI 호환 서버 모두 지원.\n"
                   "검수 활성화 시 청크당 ~+25% 시간 소요 · 재시도마다 temperature 점진 상승.")
        return f

    def _reset_local_system_prompt(self):
        """System Prompt 텍스트 영역을 DEFAULT_CONFIG의 기본값으로 되돌림.
        이미 저장된 사용자 커스텀 prompt를 새 기본값으로 갱신하고 싶을 때 사용."""
        default_prompt = cfg_module.DEFAULT_CONFIG["apis"]["local"]["system_prompt"]
        self._set_val("local_system_prompt", default_prompt)

    def _test_local_connection(self):
        """현재 입력된 endpoint/api_key로 /v1/models 호출해 응답 확인."""
        base_url = self._vars["local_base_url"].get().strip().rstrip("/")
        api_key  = self._vars["local_api_key"].get().strip()
        if not base_url:
            self._local_test_label.configure(
                text="Endpoint URL이 비어있습니다.", fg="#ff8888")
            return
        self._local_test_label.configure(text="연결 중...", fg=FG2)

        def worker():
            try:
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                r = requests.get(f"{base_url}/models", headers=headers, timeout=5)
                if r.status_code == 200:
                    try:
                        data = r.json()
                        n = len(data.get("data", []))
                        msg = f"✓ 연결 성공 (모델 {n}개 응답)"
                    except Exception:
                        msg = "✓ 연결 성공 (응답 200, 파싱 불가)"
                    color = "#88ff88"
                else:
                    msg = f"✗ HTTP {r.status_code}: {r.text[:80]}"
                    color = "#ff8888"
            except requests.exceptions.ConnectionError:
                msg = "✗ 서버 응답 없음 (실행 중인지 확인)"
                color = "#ff8888"
            except requests.exceptions.Timeout:
                msg = "✗ 응답 시간 초과 (5초)"
                color = "#ff8888"
            except Exception as e:
                msg = f"✗ {type(e).__name__}: {str(e)[:80]}"
                color = "#ff8888"
            self.after(0, lambda: self._local_test_label.configure(text=msg, fg=color))

        threading.Thread(target=worker, daemon=True).start()

    def _make_panel_pixiv(self):
        f = self._make_panel("Pixiv (크롤러 로그인)", "")
        self._row(f, "Session ID", "pixiv_session_id", show="*",
                  placeholder="PHPSESSID 쿠키 값")
        self._hint(f,
                   "브라우저에서 pixiv.net 로그인 후 개발자도구 > Application > Cookies\n"
                   "의 PHPSESSID 값(예: 12345678_abcdef...)을 복사해 붙여넣으세요.\n"
                   "비공개/R-18/팔로잉 전용 작품 접근에 사용됩니다.")
        return f

    # ── 값 로드 / 저장 ────────────────────────────────────────────────────────

    def _load_values(self):
        a = self.cfg["apis"]
        self._set_val("openai_api_keys", a["openai"].get("api_keys", []))
        self._vars["openai_model"].set(a["openai"].get("model", "gpt-4o-mini"))

        self._vars["papago_client_id"].set(a["papago"].get("client_id", ""))
        self._vars["papago_client_secret"].set(a["papago"].get("client_secret", ""))

        self._set_val("google_api_keys", a["google"].get("api_keys", []))

        self._set_val("deepl_api_keys", a["deepl"].get("api_keys", []))
        self._vars["deepl_use_free"].set(a["deepl"].get("use_free", True))

        self._set_val("gemini_api_keys", a["gemini"].get("api_keys", []))
        self._vars["gemini_model"].set(a["gemini"].get("model", "gemini-2.5-flash"))

        self._vars["claude_path"].set(a.get("claude", {}).get("path", ""))
        self._vars["claude_model"].set(a.get("claude", {}).get("model", "haiku"))
        self._set_val("claude_oauth_tokens", a.get("claude", {}).get("oauth_tokens", []))

        loc = a.get("local", {})
        local_defaults = cfg_module.DEFAULT_CONFIG["apis"]["local"]
        self._vars["local_base_url"].set(
            loc.get("base_url", local_defaults["base_url"]))
        self._vars["local_api_key"].set(
            loc.get("api_key",  local_defaults["api_key"]))
        self._vars["local_model"].set(
            loc.get("model",    local_defaults["model"]))
        self._vars["local_temperature"].set(
            str(loc.get("temperature", local_defaults["temperature"])))
        self._vars["local_repeat_penalty"].set(
            str(loc.get("repeat_penalty", local_defaults["repeat_penalty"])))
        self._vars["local_max_tokens"].set(
            str(loc.get("max_tokens", local_defaults["max_tokens"])))
        self._vars["local_frequency_penalty"].set(
            str(loc.get("frequency_penalty", local_defaults["frequency_penalty"])))
        self._vars["local_verify_enabled"].set(
            bool(loc.get("verify_enabled", local_defaults["verify_enabled"])))
        self._vars["local_verify_max_attempts"].set(
            str(loc.get("verify_max_attempts", local_defaults["verify_max_attempts"])))
        self._set_val("local_system_prompt",
            loc.get("system_prompt", local_defaults["system_prompt"]))

        self._vars["pixiv_session_id"].set(a.get("pixiv", {}).get("session_id", ""))

    def _save(self):
        a = self.cfg["apis"]
        a["openai"]["api_keys"] = self._parse_keys("openai_api_keys")
        a["openai"].pop("api_key", None)
        a["openai"]["model"]    = self._vars["openai_model"].get().strip() or "gpt-4o-mini"

        a["papago"]["client_id"]     = self._vars["papago_client_id"].get().strip()
        a["papago"]["client_secret"] = self._vars["papago_client_secret"].get().strip()

        a["google"]["api_keys"] = self._parse_keys("google_api_keys")
        a["google"].pop("api_key", None)

        a["deepl"]["api_keys"] = self._parse_keys("deepl_api_keys")
        a["deepl"].pop("api_key", None)
        a["deepl"]["use_free"] = self._vars["deepl_use_free"].get()

        a["gemini"]["api_keys"] = self._parse_keys("gemini_api_keys")
        a["gemini"].pop("api_key", None)
        a["gemini"]["model"]    = self._vars["gemini_model"].get().strip() or "gemini-2.5-flash"

        a.setdefault("claude", {})
        a["claude"].pop("api_key", None)
        a["claude"].pop("api_keys", None)
        a["claude"]["path"]  = self._vars["claude_path"].get().strip()
        a["claude"]["model"] = self._vars["claude_model"].get().strip() or "haiku"
        a["claude"]["oauth_tokens"] = self._parse_keys("claude_oauth_tokens")

        a.setdefault("local", {})
        local_defaults = cfg_module.DEFAULT_CONFIG["apis"]["local"]
        a["local"]["base_url"] = (self._vars["local_base_url"].get().strip()
                                  or local_defaults["base_url"])
        a["local"]["api_key"]  = self._vars["local_api_key"].get().strip()
        a["local"]["model"]    = (self._vars["local_model"].get().strip()
                                  or local_defaults["model"])
        try:
            a["local"]["temperature"] = float(
                self._vars["local_temperature"].get().strip()
                or str(local_defaults["temperature"]))
        except ValueError:
            a["local"]["temperature"] = local_defaults["temperature"]
        try:
            a["local"]["repeat_penalty"] = float(
                self._vars["local_repeat_penalty"].get().strip()
                or str(local_defaults["repeat_penalty"]))
        except ValueError:
            a["local"]["repeat_penalty"] = local_defaults["repeat_penalty"]
        try:
            a["local"]["max_tokens"] = int(
                self._vars["local_max_tokens"].get().strip()
                or str(local_defaults["max_tokens"]))
        except ValueError:
            a["local"]["max_tokens"] = local_defaults["max_tokens"]
        try:
            a["local"]["frequency_penalty"] = float(
                self._vars["local_frequency_penalty"].get().strip()
                or str(local_defaults["frequency_penalty"]))
        except ValueError:
            a["local"]["frequency_penalty"] = local_defaults["frequency_penalty"]
        a["local"]["verify_enabled"] = bool(self._vars["local_verify_enabled"].get())
        try:
            a["local"]["verify_max_attempts"] = max(1, int(
                self._vars["local_verify_max_attempts"].get().strip()
                or str(local_defaults["verify_max_attempts"])))
        except ValueError:
            a["local"]["verify_max_attempts"] = local_defaults["verify_max_attempts"]
        a["local"]["system_prompt"] = self._get_val("local_system_prompt").strip()
        # max_chars / timeout 은 설정 파일에서 직접 편집 (UI 노출 X)
        a["local"].setdefault("max_chars", 4000)
        a["local"].setdefault("timeout", 180)

        a.setdefault("pixiv", {})["session_id"] = self._vars["pixiv_session_id"].get().strip()

        cfg_module.save(self.cfg)
        self.result = "saved"
        self.destroy()


# ──────────────────────────────────────────────────────────────────────────────
# 번역 프롬프트 / 사용자 사전 다이얼로그
# ──────────────────────────────────────────────────────────────────────────────

class PromptDialog(tk.Toplevel):
    def __init__(self, parent, cfg: dict):
        super().__init__(parent)
        self.cfg = cfg
        self.result = None

        self.title("번역 프롬프트 · 사용자 사전")
        self.geometry("640x680")
        self.configure(bg=BG)
        self.grab_set()

        self._build_ui()
        self._load_values()

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _build_ui(self):
        tk.Label(self, text="📝 번역 프롬프트 · 사용자 사전", font=FONT_TITLE,
                 bg=BG, fg=FG, anchor="w").pack(fill="x", padx=16, pady=(14, 8))

        # 번역 지시사항
        tk.Label(self, text="번역 지시사항 (프롬프트)", font=FONT_MAIN,
                 bg=BG, fg=FG2, anchor="w").pack(fill="x", padx=16)
        tk.Label(self,
                 text="예) 독백은 반말로 번역 · 캐릭터 말투는 남성적으로 · 의성어는 원문 유지",
                 font=("Malgun Gothic", 8), bg=BG, fg=FG2, anchor="w"
                 ).pack(fill="x", padx=16, pady=(0, 3))
        prompt_box = tk.Frame(self, bg=BG3)
        prompt_box.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        self._prompt_text = tk.Text(prompt_box, font=FONT_MAIN, bg=BG3, fg=FG,
                                    insertbackground=FG, relief="flat", bd=0,
                                    wrap="word", undo=True, height=6)
        self._prompt_text.pack(fill="both", expand=True, padx=6, pady=6)

        # 사용자 사전
        tk.Label(self, text="사용자 사전 (고유명사 매핑)", font=FONT_MAIN,
                 bg=BG, fg=FG2, anchor="w").pack(fill="x", padx=16)
        tk.Label(self,
                 text='한 줄에 하나씩 "원문=번역" 형식 · 예) 古明地 こいし=코메이지 코이시  |  # 은 주석',
                 font=("Malgun Gothic", 8), bg=BG, fg=FG2, anchor="w"
                 ).pack(fill="x", padx=16, pady=(0, 3))
        dict_box = tk.Frame(self, bg=BG3)
        dict_box.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        self._dict_text = tk.Text(dict_box, font=("Consolas", 10), bg=BG3, fg=FG,
                                  insertbackground=FG, relief="flat", bd=0,
                                  wrap="none", undo=True, height=10)
        self._dict_text.pack(fill="both", expand=True, padx=6, pady=6)

        # 하단 버튼
        btn_bar = tk.Frame(self, bg=BG)
        btn_bar.pack(fill="x", padx=16, pady=(0, 14))
        tk.Button(btn_bar, text="저장", font=FONT_MAIN,
                  bg=BTN_BG, fg=BTN_FG, relief="flat", padx=22, pady=7,
                  cursor="hand2", command=self._save
                  ).pack(side="right", padx=(6, 0))
        tk.Button(btn_bar, text="취소", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=18, pady=7,
                  cursor="hand2", command=self.destroy
                  ).pack(side="right")
        tk.Button(btn_bar, text="비우기", font=FONT_MAIN,
                  bg=BG3, fg=FG2, relief="flat", padx=14, pady=7,
                  cursor="hand2", command=self._clear_all
                  ).pack(side="left")

    def _load_values(self):
        self._prompt_text.delete("1.0", "end")
        self._prompt_text.insert("1.0", self.cfg.get("translation_prompt", ""))
        self._dict_text.delete("1.0", "end")
        self._dict_text.insert("1.0", self.cfg.get("user_dictionary", ""))

    def _clear_all(self):
        if messagebox.askyesno("확인", "프롬프트와 사전을 모두 비울까요?", parent=self):
            self._prompt_text.delete("1.0", "end")
            self._dict_text.delete("1.0", "end")

    def _save(self):
        self.cfg["translation_prompt"] = self._prompt_text.get("1.0", "end").strip()
        self.cfg["user_dictionary"]    = self._dict_text.get("1.0", "end").strip()
        cfg_module.save(self.cfg)
        self.result = "saved"
        self.destroy()


# ──────────────────────────────────────────────────────────────────────────────
# 소설 크롤러 다이얼로그
# ──────────────────────────────────────────────────────────────────────────────

class CrawlerDialog(tk.Toplevel):
    def __init__(self, parent, app_cfg=None):
        super().__init__(parent)
        self._app_cfg = app_cfg or {}
        self.title("일본어 소설 다운로드")
        self.geometry("680x560")
        self.configure(bg=BG)
        self.grab_set()

        self._running = False
        self._cancel  = False

        saved_dir = (self._app_cfg.get("download_dir") or "").strip()
        if saved_dir and os.path.isdir(saved_dir):
            default_dir = saved_dir
        else:
            default_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            if not os.path.isdir(default_dir):
                default_dir = os.path.expanduser("~")

        self._url_var    = tk.StringVar()
        self._dir_var    = tk.StringVar(value=default_dir)
        self._site_var   = tk.StringVar(value="")
        self._progress   = tk.DoubleVar(value=0)

        self._build_ui()
        self._url_var.trace_add("write", self._on_url_change)
        self._dir_var.trace_add("write", self._on_dir_change)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # 헤더
        tk.Label(self, text="📚 일본어 소설 다운로드", font=FONT_TITLE,
                 bg=BG, fg=FG, anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(self,
                 text="지원: 小説家になろう · ノクターン · ハーメルン · カクヨム · pixiv",
                 font=("Malgun Gothic", 8), bg=BG, fg=FG2, anchor="w"
                 ).pack(fill="x", padx=16, pady=(0, 10))

        # URL
        tk.Label(self, text="목차 URL", font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(fill="x", padx=16)
        tk.Entry(self, textvariable=self._url_var, font=FONT_MAIN,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat", bd=6
                 ).pack(fill="x", padx=16, pady=(2, 2))
        tk.Label(self, textvariable=self._site_var, font=("Malgun Gothic", 8),
                 bg=BG, fg="#80e080", anchor="w").pack(fill="x", padx=16, pady=(0, 10))

        # 저장 폴더
        tk.Label(self, text="저장 폴더", font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(fill="x", padx=16)
        dir_row = tk.Frame(self, bg=BG)
        dir_row.pack(fill="x", padx=16, pady=(2, 10))
        tk.Entry(dir_row, textvariable=self._dir_var, font=FONT_MAIN,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat", bd=6
                 ).pack(side="left", fill="x", expand=True)
        tk.Button(dir_row, text="찾아보기...", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=10, pady=3,
                  cursor="hand2", command=self._browse
                  ).pack(side="right", padx=(6, 0))

        # 진행바
        self._pb = ttk.Progressbar(self, variable=self._progress, maximum=100,
                                   mode="determinate")
        self._pb.pack(fill="x", padx=16, pady=(8, 4))

        # 로그
        tk.Label(self, text="진행 상황", font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(fill="x", padx=16, pady=(4, 2))
        log_box = tk.Frame(self, bg=BG3)
        log_box.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self._log = tk.Text(log_box, font=("Consolas", 9), bg=BG3, fg=FG,
                            relief="flat", bd=0, state="disabled",
                            wrap="word", height=10)
        self._log.pack(fill="both", expand=True, padx=6, pady=6)

        # 버튼 바
        btn_bar = tk.Frame(self, bg=BG)
        btn_bar.pack(fill="x", padx=16, pady=(0, 14))
        tk.Button(btn_bar, text="닫기", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=18, pady=7,
                  cursor="hand2", command=self._on_close
                  ).pack(side="right", padx=(6, 0))
        self._start_btn = tk.Button(btn_bar, text="▶ 다운로드 시작",
                                    font=("Malgun Gothic", 11, "bold"),
                                    bg=BTN_BG, fg=BTN_FG, relief="flat",
                                    padx=20, pady=7, cursor="hand2",
                                    command=self._on_start_click)
        self._start_btn.pack(side="right")

    # ── 이벤트 ────────────────────────────────────────────────────────────────

    def _on_url_change(self, *_):
        url = self._url_var.get().strip()
        if not url:
            self._site_var.set("")
            return
        c = detect_crawler(url)
        if c:
            self._site_var.set(f"✓ 인식됨: {c.name}")
        else:
            self._site_var.set("✗ 지원하지 않는 사이트입니다.")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self._dir_var.get(), parent=self)
        if d:
            self._dir_var.set(d)

    def _on_dir_change(self, *_):
        new_dir = self._dir_var.get().strip()
        if self._app_cfg.get("download_dir") == new_dir:
            return
        self._app_cfg["download_dir"] = new_dir
        try:
            cfg_module.save(self._app_cfg)
        except Exception:
            pass

    def _log_write(self, msg):
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _on_start_click(self):
        if self._running:
            self._cancel = True
            self._start_btn.configure(text="취소 중...", state="disabled")
            return

        url = self._url_var.get().strip()
        out_dir = self._dir_var.get().strip()

        if not url:
            messagebox.showerror("오류", "URL을 입력하세요.", parent=self)
            return
        if not detect_crawler(url):
            messagebox.showerror("오류", "지원하지 않는 사이트입니다.", parent=self)
            return
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("오류", "올바른 저장 폴더를 선택하세요.", parent=self)
            return

        self._running = True
        self._cancel = False
        self._progress.set(0)
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        self._start_btn.configure(text="■ 취소", bg="#8b5a5a")

        threading.Thread(target=self._run, args=(url, out_dir), daemon=True).start()

    def _run(self, url, out_dir):
        def progress(cur, total, msg):
            self.after(0, self._apply_progress, cur, total, msg)

        def should_cancel():
            return self._cancel

        try:
            site_cfgs = {
                "pixiv": self._app_cfg.get("apis", {}).get("pixiv", {}),
            }
            title, text, ok, total = crawl_novel(
                url, progress_cb=progress, cancel_cb=should_cancel,
                site_cfgs=site_cfgs,
            )

            if self._cancel:
                self.after(0, self._finish_cancelled)
                return

            filename = sanitize_filename(title) + ".txt"
            out_path = os.path.join(out_dir, filename)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)

            self.after(0, self._finish_done, out_path, ok, total, len(text))
        except CrawlerError as e:
            self.after(0, self._finish_error, str(e))
        except Exception as e:
            self.after(0, self._finish_error, f"예기치 않은 오류: {e}")

    # ── 완료/에러 핸들 ────────────────────────────────────────────────────────

    def _apply_progress(self, cur, total, msg):
        if total > 0:
            self._progress.set(100 * cur / total)
        self._log_write(msg)

    def _reset_start_btn(self):
        self._start_btn.configure(text="▶ 다운로드 시작", bg=BTN_BG, state="normal")
        self._running = False

    def _finish_done(self, path, ok, total, char_count):
        self._progress.set(100)
        self._log_write("")
        self._log_write(f"✅ 저장 완료: {path}")
        self._log_write(f"   성공 {ok}/{total}편  ·  총 {char_count:,}자")
        self._reset_start_btn()
        messagebox.showinfo(
            "완료",
            f"소설을 저장했습니다.\n\n{path}\n\n성공: {ok}/{total}편",
            parent=self,
        )

    def _finish_cancelled(self):
        self._log_write("")
        self._log_write("⏹ 사용자가 취소했습니다.")
        self._reset_start_btn()

    def _finish_error(self, msg):
        self._log_write("")
        self._log_write(f"✗ 오류: {msg}")
        self._reset_start_btn()
        messagebox.showerror("오류", msg, parent=self)

    def _on_close(self):
        if self._running:
            if not messagebox.askyesno("확인",
                                       "다운로드가 진행 중입니다.\n취소하고 닫을까요?",
                                       parent=self):
                return
            self._cancel = True
        self.destroy()


# ──────────────────────────────────────────────────────────────────────────────
# 소설 분할 다이얼로그
# ──────────────────────────────────────────────────────────────────────────────

class SplitDialog(tk.Toplevel):
    def __init__(self, parent, app_cfg=None):
        super().__init__(parent)
        self._app_cfg = app_cfg or {}
        self.title("소설 분할")
        self.geometry("620x500")
        self.configure(bg=BG)
        self.grab_set()

        self._file_var  = tk.StringVar()
        self._mode_var  = tk.StringVar(value="episodes")  # episodes | size
        self._n_ep_var  = tk.StringVar(value="20")        # 에피소드별 N편
        self._kb_var    = tk.StringVar(value="500")       # 용량별 KB
        self._info_var  = tk.StringVar(value="파일을 선택하세요.")

        self._header = ""
        self._episodes = []

        self._build_ui()
        self._file_var.trace_add("write", self._on_file_change)
        self._mode_var.trace_add("write", lambda *_: self._refresh_preview())
        self._n_ep_var.trace_add("write", lambda *_: self._refresh_preview())
        self._kb_var.trace_add("write", lambda *_: self._refresh_preview())

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _build_ui(self):
        # 헤더
        tk.Label(self, text="✂ 소설 분할", font=FONT_TITLE,
                 bg=BG, fg=FG, anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(self,
                 text="다운로드한 소설을 에피소드별 또는 용량별로 여러 파일로 나눕니다.",
                 font=("Malgun Gothic", 8), bg=BG, fg=FG2, anchor="w"
                 ).pack(fill="x", padx=16, pady=(0, 10))

        # 파일 선택
        tk.Label(self, text="원본 파일", font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(fill="x", padx=16)
        file_row = tk.Frame(self, bg=BG)
        file_row.pack(fill="x", padx=16, pady=(2, 10))
        tk.Entry(file_row, textvariable=self._file_var, font=FONT_MAIN,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat", bd=6
                 ).pack(side="left", fill="x", expand=True)
        tk.Button(file_row, text="찾아보기...", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=10, pady=3,
                  cursor="hand2", command=self._browse
                  ).pack(side="right", padx=(6, 0))

        # 분할 모드
        tk.Label(self, text="분할 방식", font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(fill="x", padx=16, pady=(4, 2))

        mode_box = tk.Frame(self, bg=BG)
        mode_box.pack(fill="x", padx=16, pady=(0, 4))

        # 에피소드별
        ep_row = tk.Frame(mode_box, bg=BG)
        ep_row.pack(fill="x", pady=2)
        tk.Radiobutton(ep_row, text="에피소드별", variable=self._mode_var,
                       value="episodes", font=FONT_MAIN, bg=BG, fg=FG,
                       selectcolor=BG3, activebackground=BG, activeforeground=FG,
                       cursor="hand2"
                       ).pack(side="left")
        tk.Entry(ep_row, textvariable=self._n_ep_var, font=FONT_MAIN, width=6,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat", bd=4
                 ).pack(side="left", padx=(8, 4))
        tk.Label(ep_row, text="편씩 묶어 분할", font=FONT_MAIN,
                 bg=BG, fg=FG2).pack(side="left")

        # 용량별
        sz_row = tk.Frame(mode_box, bg=BG)
        sz_row.pack(fill="x", pady=2)
        tk.Radiobutton(sz_row, text="용량별   ", variable=self._mode_var,
                       value="size", font=FONT_MAIN, bg=BG, fg=FG,
                       selectcolor=BG3, activebackground=BG, activeforeground=FG,
                       cursor="hand2"
                       ).pack(side="left")
        tk.Entry(sz_row, textvariable=self._kb_var, font=FONT_MAIN, width=6,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat", bd=4
                 ).pack(side="left", padx=(8, 4))
        tk.Label(sz_row, text="KB 이내로 묶음 (에피소드 경계에서만 끊김)",
                 font=FONT_MAIN, bg=BG, fg=FG2).pack(side="left")

        # 미리보기
        tk.Label(self, text="미리보기", font=FONT_MAIN, bg=BG, fg=FG2,
                 anchor="w").pack(fill="x", padx=16, pady=(10, 2))
        info_box = tk.Frame(self, bg=BG3)
        info_box.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        tk.Label(info_box, textvariable=self._info_var, font=("Consolas", 9),
                 bg=BG3, fg=FG, anchor="nw", justify="left", wraplength=560
                 ).pack(fill="both", expand=True, padx=8, pady=6)

        # 버튼 바
        btn_bar = tk.Frame(self, bg=BG)
        btn_bar.pack(fill="x", padx=16, pady=(0, 14))
        tk.Button(btn_bar, text="닫기", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=18, pady=7,
                  cursor="hand2", command=self.destroy
                  ).pack(side="right", padx=(6, 0))
        self._run_btn = tk.Button(btn_bar, text="✂ 분할 실행",
                                  font=("Malgun Gothic", 11, "bold"),
                                  bg=BTN_BG, fg=BTN_FG, relief="flat",
                                  padx=20, pady=7, cursor="hand2",
                                  command=self._on_run, state="disabled")
        self._run_btn.pack(side="right")

    # ── 이벤트 ────────────────────────────────────────────────────────────────

    def _browse(self):
        initdir = self._app_cfg.get("download_dir") or os.path.expanduser("~")
        f = filedialog.askopenfilename(
            initialdir=initdir, parent=self,
            filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")],
        )
        if f:
            self._file_var.set(f)

    def _on_file_change(self, *_):
        path = self._file_var.get().strip()
        if not path or not os.path.isfile(path):
            self._header = ""
            self._episodes = []
            self._info_var.set("파일을 선택하세요.")
            self._run_btn.configure(state="disabled")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            self._info_var.set(f"파일을 읽을 수 없습니다: {e}")
            self._run_btn.configure(state="disabled")
            return
        self._header, self._episodes = splitter.parse_novel(text)
        if not self._episodes:
            self._info_var.set(
                "이 파일에서 에피소드 구분(◆ 제N話)을 찾지 못했습니다.\n"
                "단편 또는 본 프로그램이 다운로드한 파일이 아닐 수 있습니다."
            )
            self._run_btn.configure(state="disabled")
            return
        self._refresh_preview()
        self._run_btn.configure(state="normal")

    def _refresh_preview(self):
        if not self._episodes:
            return
        total_eps = len(self._episodes)
        total_chars = sum(len(l) + len(b) for l, b in self._episodes)
        mode = self._mode_var.get()
        try:
            if mode == "episodes":
                n = max(1, int(self._n_ep_var.get() or "1"))
                num_parts = (total_eps + n - 1) // n
                detail = f"{n}편씩 → {num_parts}개 파일"
            else:
                kb = max(1, int(self._kb_var.get() or "1"))
                # 시뮬레이션 — 실제 분할 로직 그대로 호출하면 비용이 높을 수 있어 추정값으로 충분
                limit = kb * 1024
                cnt = 1
                cur = 0
                for label, body in self._episodes:
                    sz = len(label) + len(body) + 2
                    if cur and cur + sz > limit:
                        cnt += 1
                        cur = 0
                    cur += sz
                detail = f"≤ {kb}KB → 약 {cnt}개 파일"
        except ValueError:
            detail = "(숫자를 올바르게 입력하세요)"
        self._info_var.set(
            f"파일: {os.path.basename(self._file_var.get())}\n"
            f"에피소드: {total_eps}편 / 본문 합계: {total_chars:,}자\n"
            f"분할: {detail}"
        )

    def _on_run(self):
        if not self._episodes:
            return
        mode = self._mode_var.get()
        try:
            if mode == "episodes":
                n = max(1, int(self._n_ep_var.get() or "1"))
                parts = splitter.split_by_episodes(self._header, self._episodes, n)
            else:
                kb = max(1, int(self._kb_var.get() or "1"))
                parts = splitter.split_by_size(self._header, self._episodes, kb * 1024)
        except ValueError:
            messagebox.showerror("오류", "숫자를 올바르게 입력하세요.", parent=self)
            return

        try:
            saved = splitter.save_parts(parts, self._file_var.get())
        except Exception as e:
            messagebox.showerror("오류", f"파일 저장 실패: {e}", parent=self)
            return

        out_dir = os.path.dirname(saved[0]) if saved else ""
        messagebox.showinfo(
            "완료",
            f"{len(saved)}개 파일로 분할 저장했습니다.\n\n"
            f"위치: {out_dir}\n"
            f"파일명: {os.path.basename(saved[0])} ~ {os.path.basename(saved[-1])}",
            parent=self,
        )


# ──────────────────────────────────────────────────────────────────────────────
# 메인 윈도우
# ──────────────────────────────────────────────────────────────────────────────

class App(_AppBase):
    def __init__(self):
        super().__init__()
        self.cfg = cfg_module.load()
        self.title("Joy4_Novel")
        self.geometry("960x780")
        self.minsize(720, 620)
        self.configure(bg=BG)

        self._translating = False
        self._cancel_translate = False
        self._dropped_file_path = None

        self._setup_style()
        self._build_ui()

    # ── tkinter 스타일 ────────────────────────────────────────────────────────

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        # Notebook
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG3, foreground=FG2,
                        padding=[10, 4], font=FONT_MAIN)
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", BTN_FG)])

        # Combobox
        style.configure("TCombobox", fieldbackground=BG3, background=BG3,
                        foreground=FG, arrowcolor=FG2, borderwidth=0,
                        selectbackground=ACCENT, selectforeground=BTN_FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", BG3)],
                  selectbackground=[("readonly", BG3)],
                  selectforeground=[("readonly", FG)])

        # Separator
        style.configure("TSeparator", background=BORDER)

        # Progressbar
        style.configure("TProgressbar",
                        troughcolor=BG3, background=ACCENT,
                        borderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT)

    # ── UI 구성 ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── 상단 바 ──────────────────────────────────────────────────────────
        topbar = tk.Frame(self, bg=BG2, pady=8)
        topbar.pack(fill="x", padx=0, pady=0)

        tk.Label(topbar, text="🌐 Joy4_Novel", font=FONT_TITLE,
                 bg=BG2, fg=FG).pack(side="left", padx=16)

        # API 선택
        tk.Label(topbar, text="API:", font=FONT_MAIN, bg=BG2, fg=FG2).pack(side="left", padx=(20, 4))
        self._api_var = tk.StringVar(value=API_DISPLAY_BY_ID.get(self.cfg["selected_api"], "ChatGPT (OpenAI)"))
        api_cb = ttk.Combobox(topbar, textvariable=self._api_var, width=20,
                              values=[v for _, v in API_LIST], state="readonly", font=FONT_MAIN)
        api_cb.pack(side="left")
        self._api_var.trace_add("write", self._on_api_change)

        # 설정 버튼
        tk.Button(topbar, text="⚙ API 설정", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=12, pady=4,
                  cursor="hand2", command=self._open_settings).pack(side="right", padx=(0, 16))

        # 소설 다운로드 버튼
        tk.Button(topbar, text="📚 소설 다운로드", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=12, pady=4,
                  cursor="hand2", command=self._open_crawler).pack(side="right", padx=(0, 6))

        # 소설 분할 버튼
        tk.Button(topbar, text="✂ 소설 분할", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=12, pady=4,
                  cursor="hand2", command=self._open_splitter).pack(side="right", padx=(0, 6))

        # 번역 프롬프트 버튼
        tk.Button(topbar, text="📝 번역 프롬프트", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=12, pady=4,
                  cursor="hand2", command=self._open_prompt).pack(side="right", padx=(0, 6))

        # ── 언어 선택 바 ──────────────────────────────────────────────────────
        langbar = tk.Frame(self, bg=BG, pady=6)
        langbar.pack(fill="x", padx=16)

        tk.Label(langbar, text="원본 언어:", font=FONT_MAIN, bg=BG, fg=FG2).pack(side="left")
        self._src_lang_var = tk.StringVar(value=self.cfg.get("source_lang", "자동감지"))
        src_cb = ttk.Combobox(langbar, textvariable=self._src_lang_var, width=14,
                               values=LANGUAGES, state="readonly", font=FONT_MAIN)
        src_cb.pack(side="left", padx=(4, 16))

        tk.Label(langbar, text="번역 언어:", font=FONT_MAIN, bg=BG, fg=FG2).pack(side="left")
        self._tgt_lang_var = tk.StringVar(value=self.cfg.get("target_lang", "한국어"))
        tgt_cb = ttk.Combobox(langbar, textvariable=self._tgt_lang_var, width=14,
                               values=LANGUAGES[1:], state="readonly", font=FONT_MAIN)
        tgt_cb.pack(side="left", padx=4)

        # 교환 버튼
        tk.Button(langbar, text="⇄", font=("Malgun Gothic", 12),
                  bg=BG3, fg=FG, relief="flat", padx=8, pady=2,
                  cursor="hand2", command=self._swap_langs).pack(side="left", padx=8)

        # ── 텍스트 영역 ───────────────────────────────────────────────────────
        text_frame = tk.Frame(self, bg=BG)
        text_frame.pack(fill="both", expand=True, padx=16, pady=(0, 4))
        text_frame.columnconfigure(0, weight=1)
        text_frame.columnconfigure(1, weight=1)
        text_frame.rowconfigure(0, weight=1)

        # 원본
        src_box = tk.Frame(text_frame, bg=BG3)
        src_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        src_hdr = tk.Frame(src_box, bg=BG3)
        src_hdr.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(src_hdr, text="원본 텍스트", font=FONT_MAIN, bg=BG3, fg=FG2,
                 anchor="w").pack(side="left")

        # 파일 드롭 정보 표시줄 (파일이 로드됐을 때만 표시)
        self._file_bar = tk.Frame(src_box, bg="#2e3a2e")
        self._file_bar_label = tk.Label(self._file_bar, text="", font=("Malgun Gothic", 8),
                                        bg="#2e3a2e", fg="#80e080", anchor="w")
        self._file_bar_label.pack(side="left", padx=8, pady=3, fill="x", expand=True)
        tk.Button(self._file_bar, text="✕", font=("Malgun Gothic", 8),
                  bg="#2e3a2e", fg="#80e080", relief="flat", padx=6, pady=1,
                  cursor="hand2", command=self._clear_file).pack(side="right", padx=4)

        self._src_text = tk.Text(src_box, font=FONT_LARGE, bg=BG3, fg=FG,
                                 insertbackground=FG, relief="flat", bd=0,
                                 wrap="word", undo=True)
        self._src_text.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._src_text.bind("<Control-Return>", lambda e: self._translate())

        # 드래그 앤 드롭 등록
        if DND_FILES:
            self._src_text.drop_target_register(DND_FILES)
            self._src_text.dnd_bind("<<Drop>>", self._on_file_drop)
            src_box.drop_target_register(DND_FILES)
            src_box.dnd_bind("<<Drop>>", self._on_file_drop)

        # 번역 결과
        tgt_box = tk.Frame(text_frame, bg=BG3)
        tgt_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        tgt_hdr = tk.Frame(tgt_box, bg=BG3)
        tgt_hdr.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(tgt_hdr, text="번역 결과", font=FONT_MAIN, bg=BG3, fg=FG2,
                 anchor="w").pack(side="left")
        tk.Button(tgt_hdr, text="복사", font=("Malgun Gothic", 8),
                  bg=BG2, fg=FG2, relief="flat", padx=6, pady=1,
                  cursor="hand2", command=self._copy_result).pack(side="right")
        self._tgt_text = tk.Text(tgt_box, font=FONT_LARGE, bg=BG3, fg=FG,
                                  insertbackground=FG, relief="flat", bd=0,
                                  wrap="word", state="disabled")
        self._tgt_text.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # ── 번역 버튼 바 ──────────────────────────────────────────────────────
        btn_bar = tk.Frame(self, bg=BG2, height=56)
        btn_bar.pack(fill="x")
        btn_bar.pack_propagate(False)

        tk.Button(btn_bar, text="✕ 지우기", font=FONT_MAIN,
                  bg=BG3, fg=FG2, relief="flat", padx=12, pady=6,
                  cursor="hand2", command=self._clear).place(x=12, rely=0.5, anchor="w")

        if HAS_PIL:
            self._icon_normal = _make_btn_icon(20, "#ffffff", BTN_BG)
            self._icon_busy   = _make_btn_icon(20, "#aaaaaa", "#555577")
            self._translate_btn = tk.Button(
                btn_bar,
                image=self._icon_normal, text="  번역 실행", compound="left",
                font=("Malgun Gothic", 11, "bold"),
                bg=BTN_BG, fg=BTN_FG, activebackground=ACCENT,
                relief="flat", padx=18, pady=7,
                cursor="hand2", command=self._translate,
            )
        else:
            self._icon_normal = self._icon_busy = None
            self._translate_btn = tk.Button(
                btn_bar, text="▶  번역 실행",
                font=("Malgun Gothic", 11, "bold"),
                bg=BTN_BG, fg=BTN_FG, relief="flat",
                padx=18, pady=7, cursor="hand2",
                command=self._translate,
            )
        self._translate_btn.place(relx=0.5, rely=0.5, anchor="center")

        # ── 상태 바 ──────────────────────────────────────────────────────────
        self._statusbar = tk.Frame(self, bg=BG2, pady=4)
        self._statusbar.pack(fill="x", side="bottom")
        self._status_var = tk.StringVar(value="준비됨  |  Ctrl+Enter 로 번역")
        tk.Label(self._statusbar, textvariable=self._status_var, font=("Malgun Gothic", 9),
                 bg=BG2, fg=FG2, anchor="w").pack(side="left", padx=12)

        # ── 번역 진행 바 (번역 중에만 표시) ───────────────────────────────────
        self._progress_var  = tk.DoubleVar(value=0)
        self._progress_text = tk.StringVar(value="")
        self._failure_var   = tk.StringVar(value="")
        self._failed_chunks = []  # 실패한 청크 번호 누적
        self._progress_frame = tk.Frame(self, bg=BG2)
        inner = tk.Frame(self._progress_frame, bg=BG2)
        inner.pack(fill="x", padx=12, pady=(4, 0))
        tk.Label(inner, textvariable=self._progress_text, font=("Malgun Gothic", 9),
                 bg=BG2, fg=FG).pack(side="left")
        ttk.Progressbar(inner, variable=self._progress_var, maximum=100,
                        mode="determinate").pack(side="left", fill="x",
                                                 expand=True, padx=10)
        fail_row = tk.Frame(self._progress_frame, bg=BG2)
        fail_row.pack(fill="x", padx=12, pady=(1, 4))
        tk.Label(fail_row, textvariable=self._failure_var,
                 font=("Malgun Gothic", 9), bg=BG2, fg="#e88080",
                 anchor="w").pack(side="left", fill="x", expand=True)
        self._failure_more_btn = tk.Button(
            fail_row, text="더보기", font=("Malgun Gothic", 8),
            bg=BG3, fg=FG, relief="flat", padx=8, pady=1,
            cursor="hand2", command=self._show_failure_details,
        )
        # 9개 이상일 때만 _refresh_failure_label 에서 표시

    # ── 이벤트 핸들러 ────────────────────────────────────────────────────────

    def _on_api_change(self, *_):
        api_id = API_ID_BY_DISPLAY.get(self._api_var.get(), "openai")
        self.cfg["selected_api"] = api_id
        cfg_module.save(self.cfg)

    def _open_settings(self):
        dlg = SettingsDialog(self, self.cfg)
        self.wait_window(dlg)
        if dlg.result == "saved":
            self.cfg = cfg_module.load()
            self._status_var.set("설정이 저장되었습니다.")

    def _open_crawler(self):
        CrawlerDialog(self, app_cfg=self.cfg)

    def _open_splitter(self):
        SplitDialog(self, app_cfg=self.cfg)

    def _open_prompt(self):
        dlg = PromptDialog(self, self.cfg)
        self.wait_window(dlg)
        if dlg.result == "saved":
            self.cfg = cfg_module.load()
            p_len = len(self.cfg.get("translation_prompt", ""))
            d_lines = sum(1 for l in self.cfg.get("user_dictionary", "").splitlines()
                          if l.strip() and not l.strip().startswith("#") and "=" in l)
            self._status_var.set(
                f"번역 프롬프트 저장됨  |  지시사항 {p_len}자 · 사전 {d_lines}개 항목"
            )

    def _swap_langs(self):
        src = self._src_lang_var.get()
        tgt = self._tgt_lang_var.get()
        if src in LANGUAGES[1:]:
            self._src_lang_var.set(tgt)
            self._tgt_lang_var.set(src)
        else:
            self._tgt_lang_var.set(src if src in LANGUAGES[1:] else "영어")

    def _on_file_drop(self, event):
        raw = event.data.strip()
        # tkinterdnd2는 공백 포함 경로를 {}로 감쌈
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        # 여러 파일이 드롭된 경우 첫 번째만
        path = raw.split("} {")[0].strip("{}")

        if not path.lower().endswith(".txt"):
            self._status_var.set("txt 파일만 지원합니다.")
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            try:
                with open(path, "r", encoding="cp949") as f:
                    content = f.read()
            except Exception as e:
                messagebox.showerror("파일 오류", f"파일을 읽을 수 없습니다:\n{e}", parent=self)
                return
        except Exception as e:
            messagebox.showerror("파일 오류", f"파일을 읽을 수 없습니다:\n{e}", parent=self)
            return

        self._dropped_file_path = path
        self._src_text.delete("1.0", "end")
        self._src_text.insert("end", content)
        self._set_result("")

        fname = os.path.basename(path)
        self._file_bar_label.configure(text=f"📄 {fname}  —  번역 후 같은 폴더에 저장됩니다")
        self._file_bar.pack(fill="x", before=self._src_text)
        self._status_var.set(f"파일 로드 완료: {fname}  ({len(content):,}자)")

    def _clear_file(self):
        self._dropped_file_path = None
        self._file_bar.pack_forget()
        self._file_bar_label.configure(text="")

    def _translate_title(self, title: str) -> str:
        """파일명(제목)을 현재 선택된 번역기로 단발 번역.
        검수·사용자 사전·추가 지시는 모두 OFF — 짧은 제목에 과한 비용 안 발생.
        실패 시 원본 그대로 반환 (저장은 계속 진행)."""
        title = (title or "").strip()
        if not title:
            return title
        try:
            api_id      = API_ID_BY_DISPLAY.get(self._api_var.get(), "openai")
            translator  = TRANSLATORS[api_id]
            codes       = LANG_CODES.get(api_id, {})
            src_display = self._src_lang_var.get()
            tgt_display = self._tgt_lang_var.get()
            src_code    = codes.get(src_display, src_display)
            tgt_code    = codes.get(tgt_display, tgt_display)
            api_cfg = dict(self.cfg["apis"].get(api_id, {}))
            api_cfg["_prompt"]        = ""
            api_cfg["_dictionary"]    = ""
            api_cfg["verify_enabled"] = False
            out = translator.translate(title, src_code, tgt_code, api_cfg)
            return ((out or "").strip()) or title
        except Exception:
            # 제목 번역이 실패해도 본문 저장은 막지 말 것
            return title

    def _save_translated_file(self, result: str) -> str:
        path = self._dropped_file_path
        dir_name = os.path.dirname(path)
        base, ext = os.path.splitext(os.path.basename(path))
        translated_title = self._translate_title(base)
        # 제목 번역 결과가 빈 문자열이거나 sanitize 후 빈 문자열이면 원본으로 폴백
        safe_title = (
            sanitize_filename(translated_title)
            or sanitize_filename(base)
            or "novel"
        )
        out_path = os.path.join(dir_name, f"[Ai 번역]{safe_title}{ext}")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(result)
        return out_path

    def _clear(self):
        self._src_text.delete("1.0", "end")
        self._set_result("")
        self._clear_file()
        self._status_var.set("지워졌습니다.")

    def _copy_result(self):
        text = self._tgt_text.get("1.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._status_var.set("클립보드에 복사되었습니다.")

    def _set_result(self, text):
        self._tgt_text.configure(state="normal")
        self._tgt_text.delete("1.0", "end")
        if text:
            self._tgt_text.insert("end", text)
        self._tgt_text.configure(state="disabled")

    def _set_busy(self, busy: bool):
        self._translating = busy
        if self._icon_normal:
            icon = self._icon_busy if busy else self._icon_normal
            self._translate_btn.configure(
                image=icon,
                text="  ■ 취소" if busy else "  번역 실행",
                state="normal",  # 번역 중에도 클릭 가능 (취소용)
                bg="#8b5a5a" if busy else BTN_BG,
            )
        else:
            self._translate_btn.configure(
                state="normal",
                text="■ 취소" if busy else "▶  번역 실행",
                bg="#8b5a5a" if busy else BTN_BG,
            )

    def _show_progress(self, show: bool):
        if show:
            self._progress_frame.pack(fill="x", side="bottom",
                                      before=self._statusbar)
        else:
            self._progress_frame.pack_forget()

    # ── 번역 실행 ─────────────────────────────────────────────────────────────

    def _translate(self):
        # 번역 중이면 취소 토글
        if self._translating:
            self._cancel_translate = True
            self._status_var.set("취소 요청됨 — 현재 청크 완료 후 중단합니다.")
            return

        text = self._src_text.get("1.0", "end").strip()
        if not text:
            return

        api_id      = API_ID_BY_DISPLAY.get(self._api_var.get(), "openai")
        src_display = self._src_lang_var.get()
        tgt_display = self._tgt_lang_var.get()

        codes      = LANG_CODES.get(api_id, {})
        src_code   = codes.get(src_display, src_display)
        tgt_code   = codes.get(tgt_display, tgt_display)

        translator = TRANSLATORS[api_id]
        api_cfg    = dict(self.cfg["apis"].get(api_id, {}))
        api_cfg["_prompt"]     = self.cfg.get("translation_prompt", "")
        api_cfg["_dictionary"] = self.cfg.get("user_dictionary", "")

        max_chars = MAX_CHARS.get(api_id, 4000)
        chunks = chunk_text(text, max_chars)
        total = len(chunks)

        # 진행 저장은 드롭된 파일 모드에서만 적용
        source_path = self._dropped_file_path
        state = None
        if source_path:
            existing = progress_mod.load(source_path)
            if existing and progress_mod.matches(existing, text, total, max_chars):
                ok, fail, pending = progress_mod.summary(existing)
                msg = (
                    f"이전 번역 진행 파일이 발견됐습니다.\n\n"
                    f"  성공:   {ok} / {total}\n"
                    f"  실패:   {fail}\n"
                    f"  미수행: {pending}\n\n"
                    f"[예]    이어서 진행 (성공한 청크는 재번역하지 않음)\n"
                    f"[아니오] 처음부터 다시 시작\n"
                    f"[취소]  번역 취소"
                )
                ans = messagebox.askyesnocancel("이어서 진행", msg, parent=self)
                if ans is None:
                    return
                if ans:
                    state = existing
                else:
                    state = progress_mod.make_state(
                        text, total, api_id, src_display, tgt_display, max_chars
                    )
                    progress_mod.save(source_path, state)
            else:
                state = progress_mod.make_state(
                    text, total, api_id, src_display, tgt_display, max_chars
                )
                progress_mod.save(source_path, state)

        self._cancel_translate = False
        self._set_busy(True)
        self._set_result("")
        self._show_progress(True)
        self._progress_var.set(0)
        self._progress_text.set(f"0% ({total}개 청크)")
        # 이어서 진행 시 이전에 실패한 청크 번호를 미리 채워 표시
        if state:
            self._failed_chunks = [
                c.get("i") for c in state.get("chunks", [])
                if c.get("status") == "fail" and isinstance(c.get("i"), int)
            ]
        else:
            self._failed_chunks = []
        self._refresh_failure_label()
        self._status_var.set(
            f"{API_DISPLAY_BY_ID[api_id]} 번역 시작 — {len(text):,}자 → {total}개 청크"
        )

        def run():
            logger = TranslationLogger(
                api_name=API_DISPLAY_BY_ID[api_id],
                total_chars=len(text),
                chunk_count=total,
                src_lang=src_display,
                tgt_lang=tgt_display,
            )

            results = [None] * total
            ok_count = 0

            for i, chunk in enumerate(chunks, 1):
                if self._cancel_translate:
                    logger.info(f"사용자 취소 (청크 {i}/{total} 직전)")
                    break

                # 캐시 hit — 이전 실행에서 성공한 청크
                if state and state["chunks"][i - 1].get("status") == "ok":
                    cached = state["chunks"][i - 1].get("text") or ""
                    results[i - 1] = cached
                    ok_count += 1
                    logger.info(f"[청크 {i}/{total}] 캐시 사용 ({len(cached):,}자)")
                    self.after(0, self._progress_tick, i, total, "캐시")
                    continue

                self.after(0, self._progress_tick, i, total, "전송 중")
                logger.chunk_start(i, total, len(chunk))
                t0 = time.time()

                try:
                    r = translator.translate(chunk, src_code, tgt_code, api_cfg)
                    elapsed = time.time() - t0
                    logger.chunk_ok(i, total, elapsed, len(r))
                    results[i - 1] = r
                    ok_count += 1
                    if state:
                        progress_mod.update_chunk(state, i, "ok", r)
                        progress_mod.save(source_path, state)
                except Exception as e:
                    elapsed = time.time() - t0
                    logger.chunk_fail(i, total, elapsed, e)
                    err = str(e)[:500]
                    results[i - 1] = f"\n\n[청크 {i} 번역 실패: {err}]\n\n"
                    if state:
                        progress_mod.update_chunk(state, i, "fail", err)
                        progress_mod.save(source_path, state)
                    self.after(0, self._add_failure, i)

                self.after(0, self._progress_tick, i, total, "완료")

            # 미실행으로 남은 자리(취소 등)는 빈 문자열로
            results = [r if r is not None else "" for r in results]
            full = "\n\n".join(results)
            cancelled = self._cancel_translate
            logger.finish(success=not cancelled and ok_count == total,
                          result_chars=len(full),
                          ok_count=ok_count, total=total)

            # 모두 성공 시 진행 파일 정리
            if state and not cancelled and ok_count == total:
                progress_mod.remove(source_path)

            self.after(0, self._on_translate_done,
                       full, ok_count, total, cancelled, logger.path)

        threading.Thread(target=run, daemon=True).start()

    def _progress_tick(self, cur: int, total: int, phase: str):
        pct = 100 * cur / total if total else 0
        self._progress_var.set(pct)
        self._progress_text.set(f"{pct:5.1f}%  ({cur}/{total}  {phase})")
        self._status_var.set(f"번역 진행 중  —  청크 {cur}/{total}")

    def _add_failure(self, chunk_no: int):
        if chunk_no not in self._failed_chunks:
            self._failed_chunks.append(chunk_no)
        self._refresh_failure_label()

    def _refresh_failure_label(self):
        failed = sorted(self._failed_chunks)
        n = len(failed)
        if n == 0:
            self._failure_var.set("")
        elif n <= 8:
            nums = ", ".join(f"#{i}" for i in failed)
            self._failure_var.set(f"⚠ 실패 {n}개 — 청크 {nums}")
        else:
            head = ", ".join(f"#{i}" for i in failed[:6])
            self._failure_var.set(
                f"⚠ 실패 {n}개 — 청크 {head}, ... 외 {n - 6}개"
            )
        # 더보기 버튼은 9개 이상일 때만 노출
        try:
            if n >= 9:
                self._failure_more_btn.pack(side="right", padx=(6, 0))
            else:
                self._failure_more_btn.pack_forget()
        except (tk.TclError, AttributeError):
            pass

    def _show_failure_details(self):
        if not self._failed_chunks:
            return
        win = tk.Toplevel(self)
        win.title("실패 청크 목록")
        win.configure(bg=BG)
        win.transient(self)

        failed = sorted(self._failed_chunks)
        n = len(failed)

        tk.Label(win, text=f"⚠ 실패 청크 {n}개", font=FONT_TITLE,
                 bg=BG, fg="#e88080", anchor="w").pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(win, text="번역 결과 화면에서 '청크 N 번역 실패' 로 검색하면 해당 위치를 찾을 수 있습니다.",
                 font=("Malgun Gothic", 8), bg=BG, fg=FG2, anchor="w",
                 wraplength=380, justify="left").pack(fill="x", padx=14, pady=(0, 8))

        body = tk.Frame(win, bg=BG3)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        txt = tk.Text(body, font=("Consolas", 10), bg=BG3, fg=FG,
                      relief="flat", bd=0, wrap="word", height=10, width=44)
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert("end", ", ".join(f"#{i}" for i in failed))
        txt.configure(state="disabled")

        btns = tk.Frame(win, bg=BG)
        btns.pack(fill="x", padx=14, pady=(0, 12))

        def copy_to_clipboard():
            self.clipboard_clear()
            self.clipboard_append(", ".join(str(i) for i in failed))
            self.update()  # 클립보드 동기화

        tk.Button(btns, text="번호만 복사", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=10, pady=4,
                  cursor="hand2", command=copy_to_clipboard
                  ).pack(side="left")
        tk.Button(btns, text="닫기", font=FONT_MAIN,
                  bg=BG3, fg=FG, relief="flat", padx=14, pady=4,
                  cursor="hand2", command=win.destroy
                  ).pack(side="right")

        # 부모 창 중앙에 배치
        win.update_idletasks()
        w, h = win.winfo_width(), win.winfo_height()
        x = self.winfo_rootx() + (self.winfo_width()  - w) // 2
        y = self.winfo_rooty() + (self.winfo_height() - h) // 2
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        win.grab_set()

    def _on_translate_done(self, result: str, ok: int, total: int,
                           cancelled: bool, log_path: str):
        self._set_result(result)
        self._set_busy(False)
        self._show_progress(False)
        self.cfg["source_lang"] = self._src_lang_var.get()
        self.cfg["target_lang"] = self._tgt_lang_var.get()
        cfg_module.save(self.cfg)

        log_name = os.path.basename(log_path)

        # 드롭된 파일 번역 → 같은 폴더에 저장
        if self._dropped_file_path and result.strip():
            try:
                out_path = self._save_translated_file(result)
                self._file_bar_label.configure(text=f"✅ 저장 완료: {out_path}")
                base_msg = f"파일 저장 완료 → {os.path.basename(out_path)}"
            except Exception as e:
                base_msg = f"파일 저장 실패: {e}"
        else:
            base_msg = f"결과 {len(result):,}자"

        # 진행 파일이 남아 있는지 (= 드롭된 파일 + 미완료)
        resume_hint = ""
        if self._dropped_file_path and (cancelled or ok < total):
            pp = progress_mod.progress_path(self._dropped_file_path)
            if os.path.isfile(pp):
                resume_hint = (
                    "\n\n진행 파일이 저장되어 있습니다 — 같은 파일을 다시 번역하면 "
                    "이어서 진행 여부를 묻습니다.\n"
                    f"진행 파일: {os.path.basename(pp)}"
                )

        if cancelled:
            self._status_var.set(f"⏹ 취소됨  |  {ok}/{total}청크  |  로그: {log_name}")
            if resume_hint:
                messagebox.showinfo(
                    "중단됨",
                    f"번역이 사용자에 의해 중단됐습니다 ({ok}/{total} 완료).{resume_hint}",
                    parent=self,
                )
        elif ok < total:
            self._status_var.set(
                f"⚠ 부분 실패  |  {ok}/{total}청크  |  {base_msg}  |  로그: {log_name}"
            )
            messagebox.showwarning(
                "일부 청크 실패",
                f"일부 청크의 번역이 실패했습니다.\n"
                f"성공 {ok}/{total}\n\n자세한 내용은 로그를 확인해 주세요:\n{log_path}"
                + resume_hint,
                parent=self,
            )
        else:
            self._status_var.set(
                f"✅ 번역 완료  |  {total}청크  |  {base_msg}  |  로그: {log_name}"
            )


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
