from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from starship_shared.schemas import EngineFactV1
from starship_shared.signing import verify_body_v1

from starship_web.slack import (
    format_fact_message,
    get_slack_webhook_url,
    send_slack_message,
)


def _load_dotenv() -> Path | None:
    # Walk up from this file to find repo root .env
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.exists():
            load_dotenv(dotenv_path=candidate, override=True)
            return candidate
    load_dotenv(override=True)
    return None


_dotenv_path = _load_dotenv()

app = FastAPI()


def _setup_logging(log_file: str = "logs/web.log") -> logging.Logger:
    logger = logging.getLogger("starship_web")
    if logger.handlers:
        return logger
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=5
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def _load_engine_ingest_secret() -> str:
    env_secret = os.environ.get("ENGINE_INGEST_SECRET")
    if env_secret:
        return env_secret
    cfg_path = Path(os.environ.get("ENGINE_CONFIG_PATH", "configs/bot.yaml"))
    if not cfg_path.exists():
        return ""
    try:
        import yaml
    except Exception:
        return ""
    try:
        data = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        return ""
    comms = data.get("comms") if isinstance(data, dict) else None
    if not isinstance(comms, dict):
        return ""
    secret = comms.get("engine_ingest_secret")
    return str(secret).strip() if secret else ""


log = _setup_logging()
ENGINE_INGEST_SECRET = _load_engine_ingest_secret()
log.info(
    "Web config: ingest_secret=%s",
    ("set" if ENGINE_INGEST_SECRET else "empty"),
)
log.info("Web config: dotenv_path=%s", _dotenv_path or "not_found")
log.info(
    "Web config: slack_webhook=%s",
    ("set" if get_slack_webhook_url() else "empty"),
)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/engine/ingest")
async def engine_ingest(
    request: Request,
    x_engine_timestamp: str | None = Header(default=None),
    x_engine_signature: str | None = Header(default=None),
):
    body = await request.body()
    if not x_engine_timestamp or not x_engine_signature:
        log.warning("ingest rejected: missing signature headers")
        raise HTTPException(401, "Missing engine signature headers")

    if ENGINE_INGEST_SECRET:
        ok = verify_body_v1(
            ENGINE_INGEST_SECRET, body, x_engine_timestamp, x_engine_signature
        )
        if not ok:
            log.warning("ingest rejected: bad signature")
            raise HTTPException(401, "Bad engine signature")

    payload = await request.json()
    fact = EngineFactV1.model_validate(payload)

    fact_dict = fact.model_dump()
    kind = fact_dict.get("kind")
    log.info(
        "ingest ok kind=%s run_id=%s seq=%s",
        kind,
        fact_dict.get("run_id"),
        fact_dict.get("seq"),
    )
    if kind in ("ENGINE_EVENT", "STRATEGY_SIGNAL"):
        msg = format_fact_message(fact_dict)
        try:
            ok = send_slack_message(msg)
            if not ok:
                log.warning("slack send failed (non-2xx or missing webhook)")
        except Exception:
            log.exception("slack send exception")

    return {"ok": True, "run_id": fact.run_id, "seq": fact.seq}


@app.post("/slack/events")
async def slack_events(request: Request):
    payload = await request.json()

    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload["challenge"]})

    return {"ok": True}
