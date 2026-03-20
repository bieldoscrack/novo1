from __future__ import annotations
from typing import List, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Wallet
    private_key: str = ""
    funder_address: str = ""
    signature_type: int = 0

    # Polymarket
    clob_api_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    data_api_url: str = "https://data-api.polymarket.com"
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/"
    chain_id: int = 137

    # Binance
    binance_ws_url: str = (
        "wss://stream.binance.com:9443/stream"
        "?streams=btcusdt@trade/ethusdt@trade/solusdt@trade"
    )

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Strategy
    initial_capital: float = 500.0
    pair_cost_threshold: float = 0.98
    copy_ratio: float = 0.3
    max_exposure_pct: float = 0.50
    min_reserve_pct: float = 0.20
    stop_loss_daily_pct: float = 0.05
    stop_loss_pause_hours: int = 6

    # Copy wallets
    copy_wallets: str = ""

    # Infra
    dashboard_port: int = 8080
    simulation_mode: bool = True
    log_level: str = "INFO"

    @property
    def copy_wallet_list(self) -> List[str]:
        if not self.copy_wallets:
            return []
        return [w.strip() for w in self.copy_wallets.split(",") if w.strip()]

    @property
    def has_wallet(self) -> bool:
        return bool(self.private_key)


settings = Settings()
