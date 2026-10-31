"""Cold Gateway settings; never part of QQ personality or expression configuration."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from companion.gateway_contract import Contract, ModelAlias


class GatewayConfig(Contract):
    enabled: bool = Field(default=False, strict=True)
    bind_host: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    port: int = Field(default=8780, ge=1024, le=65535, strict=True)
    aliases: tuple[ModelAlias, ...] = Field(default=(), max_length=64)
    wait_seconds: float = Field(default=120, ge=0, le=120, allow_inf_nan=False)
    execution_seconds: int = Field(default=120, ge=10, le=600, strict=True)
    receipt_seconds: int = Field(default=3600, ge=60, le=86400, strict=True)
    pending_per_client: int = Field(default=16, ge=1, le=64, strict=True)

    @model_validator(mode="after")
    def one_approved_route(self):
        names = [item.model_alias for item in self.aliases]
        if len(names) != len(set(names)):
            raise ValueError("GATEWAY_DUPLICATE_ALIAS")
        if any(item.route_id != "approved_default" for item in self.aliases):
            raise ValueError("GATEWAY_ROUTE_NOT_IMPLEMENTED")
        if self.receipt_seconds <= self.execution_seconds:
            raise ValueError("GATEWAY_RECEIPT_RETENTION_TOO_SHORT")
        return self

    @classmethod
    def load(cls):
        path = Path(os.getenv("COMPANION_GATEWAY_CONFIG", "gateway.toml"))
        return cls.model_validate(tomllib.loads(path.read_text("utf-8")) if path.exists() else {})
