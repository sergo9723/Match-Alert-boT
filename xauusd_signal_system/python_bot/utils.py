# -*- coding: utf-8 -*-
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("utils")

LOG_FILE = os.environ.get("SIGNAL_LOG_FILE", "signals_log.jsonl")


def log_signal(setup: dict, confirmed: bool, reason: str) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "setup": setup,
        "ai_confirmed": confirmed,
        "ai_reason": reason,
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"Не смог записать в журнал сигналов: {e}")
