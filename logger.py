"""번역 작업 로거 — logs/translation_YYYYMMDD_HHMMSS.log 에 기록"""

import os
import time
import traceback
from datetime import datetime

from config import user_data_dir

LOG_DIR = os.path.join(user_data_dir(), "logs")


class TranslationLogger:
    def __init__(self, api_name: str, total_chars: int, chunk_count: int,
                 src_lang: str = "", tgt_lang: str = ""):
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(LOG_DIR, f"translation_{ts}.log")
        self.start_time = time.time()
        self._file = open(self.path, "w", encoding="utf-8")

        self._write_raw("=" * 60)
        self._write_raw(" Joy4_Novel 번역 세션 로그")
        self._write_raw("=" * 60)
        self.info(f"시작 시각  : {datetime.now().isoformat()}")
        self.info(f"API        : {api_name}")
        self.info(f"언어       : {src_lang} → {tgt_lang}")
        self.info(f"원본 길이  : {total_chars:,}자")
        self.info(f"청크 수    : {chunk_count}")
        self._write_raw("-" * 60)

    def _write_raw(self, line: str):
        self._file.write(line + "\n")
        self._file.flush()

    def _stamp(self):
        return datetime.now().strftime("%H:%M:%S")

    def info(self, msg: str):
        self._write_raw(f"[{self._stamp()}] {msg}")

    def warn(self, msg: str):
        self._write_raw(f"[{self._stamp()}] [WARN]  {msg}")

    def error(self, msg: str, exc: BaseException = None):
        self._write_raw(f"[{self._stamp()}] [ERROR] {msg}")
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            for line in tb.rstrip().splitlines():
                self._write_raw(f"              | {line}")

    def chunk_start(self, idx: int, total: int, chars: int):
        self.info(f"[청크 {idx}/{total}] 요청 시작 ({chars:,}자)")

    def chunk_ok(self, idx: int, total: int, elapsed: float, chars: int):
        self.info(f"[청크 {idx}/{total}] ✔ 성공  {elapsed:6.2f}초  →  {chars:,}자")

    def chunk_fail(self, idx: int, total: int, elapsed: float, exc: BaseException):
        self._write_raw(
            f"[{self._stamp()}] [FAIL] [청크 {idx}/{total}] {elapsed:6.2f}초 경과 후 실패"
        )
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        for line in tb.rstrip().splitlines():
            self._write_raw(f"              | {line}")

    def finish(self, success: bool, result_chars: int, ok_count: int, total: int):
        elapsed = time.time() - self.start_time
        self._write_raw("-" * 60)
        self.info(f"결과       : {'성공' if success else '중단/실패'}")
        self.info(f"성공 청크  : {ok_count}/{total}")
        self.info(f"결과 길이  : {result_chars:,}자")
        self.info(f"총 소요    : {elapsed:.1f}초 ({elapsed/60:.1f}분)")
        self.info(f"종료 시각  : {datetime.now().isoformat()}")
        self._write_raw("=" * 60)
        self._file.close()

    def close(self):
        try:
            self._file.close()
        except Exception:
            pass
