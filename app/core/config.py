from pydantic_settings import BaseSettings
from typing import Optional
import os

class Settings(BaseSettings):
    # App settings
    app_name: str = "Yeison Sales Agent"
    version: str = "1.0.0"
    debug: bool = False
    
    # WhatsApp/Twilio settings
    whatsapp_verify_token: Optional[str] = None
    whatsapp_access_token: Optional[str] = None
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_phone_number: Optional[str] = None
    
    # Database settings
    database_url: Optional[str] = None
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    
    # Redis settings  
    redis_url: Optional[str] = None
    redis_ttl: int = 3600  # 1 hour default TTL for cached data
    
    # Message batching settings
    batch_enabled: bool = True  # Enable/disable message batching
    batch_window_seconds: int = 3  # Wait time to collect multiple messages
    batch_max_messages: int = 5  # Maximum messages per batch (fire early if reached)
    
    # AI/LLM settings
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4-turbo-preview"
    openai_temperature: float = 0.7
    openai_max_tokens: int = 500
    
    # OpenRouter settings (for testing, compatible with OpenAI SDK)
    use_openrouter: bool = False  # Set to True to use OpenRouter instead of OpenAI
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "openai/gpt-3.5-turbo"  # Model to use with OpenRouter
    
    # Main Backend API settings (for mutations)
    backend_api_url: Optional[str] = None
    backend_api_key: Optional[str] = None
    backend_api_timeout: int = 30
    
    # Telegram settings
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # Cloudflare R2 Storage
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "yeison-storage"

    class Config:
        env_file = ".env"
        case_sensitive = False

# Global settings instance
settings = Settings()