from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class VoiceConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    model_standard: str = Field(default="eleven_v4", min_length=1, max_length=100)
    model_realtime: str = Field(default="eleven_v4_turbo", min_length=1, max_length=100)
    model_stt: str = Field(default="scribe_v2_realtime", min_length=1, max_length=100)
    language: Literal["auto", "zh", "ja", "en"] = Field(
        default="auto", description="Clone reference recording language; not a TTD request language override")
    personality: str = Field(default="", max_length=1000)
    audio_format: Literal["mp3_44100_128", "pcm_16000", "pcm_24000"] = "mp3_44100_128"


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    voice_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    active: bool = False
    configuration: VoiceConfiguration = Field(default_factory=VoiceConfiguration)


class ConsentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notice_version: Literal["2026-11-08-v1"]
    accepted: Literal[True]


class SpeakInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["none", "thoughtful"] = "none"


class AutoBudgetSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    jobs: StrictInt = Field(ge=0, le=20)
    requests: StrictInt = Field(ge=0, le=40)
    characters: StrictInt = Field(ge=0, le=4000)
    confirm_increase: StrictBool = False


class ScribeBudgetSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    enabled: StrictBool = False
    attempts: StrictInt = Field(ge=0, le=10)
    connections: StrictInt = Field(ge=0, le=10)
    audio_seconds: StrictInt = Field(ge=0, le=150)
    confirm_increase: StrictBool = False
    confirm_enable: StrictBool = False
