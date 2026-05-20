"""Configuration loader: merges config.yaml with environment variables (.env)."""
import os
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


class Underlying(BaseModel):
    name: str
    scrip: int
    segment: str


class RiskCfg(BaseModel):
    sl_pct: float = 0.30
    rr: float = 2.0


class RulesCfg(BaseModel):
    delta_min: float = 0.35
    delta_max: float = 0.55
    oi_change_pct_min: float = 20.0
    price_change_pct_min: float = 5.0
    short_cover_oi_drop_pct: float = 15.0


class Settings(BaseModel):
    underlyings: List[Underlying]
    poll_interval_seconds: int = 5
    risk: RiskCfg = RiskCfg()
    rules: RulesCfg = RulesCfg()
    signal_dedup_window_seconds: int = 300
    max_signals_in_store: int = 500
    dhan_client_id: str = ""
    dhan_access_token: str = ""


def load_settings() -> Settings:
    cfg_path = ROOT / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    raw["dhan_client_id"] = os.getenv("DHAN_CLIENT_ID", "")
    raw["dhan_access_token"] = os.getenv("DHAN_ACCESS_TOKEN", "")
    return Settings(**raw)


settings = load_settings()
