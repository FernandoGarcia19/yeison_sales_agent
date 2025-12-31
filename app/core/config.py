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
    
    class Config:
        env_file = ".env"
        case_sensitive = False

# Global settings instance
settings = Settings()