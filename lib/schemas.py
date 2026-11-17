"""控制面写接口的 Pydantic 请求模型（只覆盖结构化程度高的端点）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AuthTokenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["issue", "clear"] = "issue"


class LiteLLMModelRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["add", "delete"] = "add"
    model_name: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    api_base: str | None = Field(default=None, max_length=500)
    key_env: str = Field(default="INFERENCE_API_KEY", max_length=64)
    id: str | None = Field(default=None, max_length=64)
