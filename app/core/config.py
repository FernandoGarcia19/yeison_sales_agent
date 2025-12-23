from pydantic_settings import BaseSettings
from typing import Optional
import os

class Settings(BaseSettings):
    # App settings
    app_name: str = "Yeison Sales Agent"
    version: str = "1.0.0"
    debug: bool = False
    
    # WhatsApp settings
    whatsapp_verify_token: Optional[str] = None
    whatsapp_access_token:  Optional[str] = None
    
    # Database settings
    database_url:  Optional[str] = None
    
    # Redis settings  
    redis_url: Optional[str] = None
    
    class Config:
        env_file = ".env"
        case_sensitive = False

# Global settings instance
settings = Settings()