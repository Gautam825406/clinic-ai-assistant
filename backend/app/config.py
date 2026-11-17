"""Settings — everything secret comes from the environment, never from code.

PRD Sec 15: secrets in env / secret manager only. Empty value = simulated
mode for that provider, so the app runs locally with no credentials.
"""
import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    app_env: str = _env("APP_ENV", "dev")
    demo_mode: bool = _env("DEMO_MODE", "true").lower() in ("1", "true", "yes")
    database_url: str = _env("DATABASE_URL", "sqlite:///./data/clinic.db")
    redis_url: str = _env("REDIS_URL", "")
    groq_api_key: str = _env("GROQ_API_KEY", "")
    groq_stt_model: str = _env("GROQ_STT_MODEL", "whisper-large-v3-turbo")
    groq_llm_model: str = _env("GROQ_LLM_MODEL", "openai/gpt-oss-120b")
    telephony_provider: str = _env("TELEPHONY_PROVIDER", "simulated")
    telephony_api_key: str = _env("TELEPHONY_API_KEY", "")
    telephony_api_secret: str = _env("TELEPHONY_API_SECRET", "")
    telephony_number: str = _env("TELEPHONY_NUMBER", "")
    whatsapp_provider: str = _env("WHATSAPP_PROVIDER", "simulated")
    whatsapp_api_token: str = _env("WHATSAPP_API_TOKEN", "")
    whatsapp_phone_number_id: str = _env("WHATSAPP_PHONE_NUMBER_ID", "")
    payment_gateway: str = _env("PAYMENT_GATEWAY", "cashfree")
    # CORS: comma-separated origins allowed to call the API (the Vercel
    # frontend URL). Empty = '*' in DEMO_MODE only; in prod with no value,
    # no cross-origin calls are allowed.
    frontend_origin: str = _env("FRONTEND_ORIGIN", "")
    cashfree_app_id: str = _env("CASHFREE_APP_ID", "")
    cashfree_secret_key: str = _env("CASHFREE_SECRET_KEY", "")
    cashfree_env: str = _env("CASHFREE_ENV", "SANDBOX")
    payment_webhook_secret: str = _env("PAYMENT_WEBHOOK_SECRET", "")
    otp_provider: str = _env("OTP_PROVIDER", "simulated")

    @property
    def simulated_telephony(self) -> bool:
        return self.telephony_provider.lower() == "simulated" or not self.telephony_api_key

    @property
    def simulated_whatsapp(self) -> bool:
        return self.whatsapp_provider.lower() == "simulated" or not self.whatsapp_api_token

    @property
    def simulated_payments(self) -> bool:
        return not self.cashfree_app_id or not self.cashfree_secret_key

    @property
    def simulated_stt(self) -> bool:
        return not self.groq_api_key


settings = Settings()
