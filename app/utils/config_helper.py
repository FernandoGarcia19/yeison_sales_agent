"""
Helper utilities for working with agent configuration.

These helpers make it easy to safely access configuration values
with proper defaults and type safety.
"""

from typing import Any, Optional, Dict, List


class AgentConfigHelper:
    """Helper class to safely access agent configuration values."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize with agent configuration dict."""
        self.config = config or {}
    
    # Agent Info
    def get_agent_name(self) -> str:
        """Get agent name."""
        return self.config.get("agent_info", {}).get("name", "Sales Agent")
    
    def get_agent_type(self) -> str:
        """Get agent type."""
        return self.config.get("agent_info", {}).get("type", "sales")
    
    # Tenant/Business Info
    def get_company_name(self) -> str:
        """Get company name."""
        return self.config.get("tenant_info", {}).get("company_name", "Unknown Company")
    
    def get_industry(self) -> str:
        """Get business industry."""
        return self.config.get("tenant_info", {}).get("industry", "General")
    
    def get_company_description(self) -> str:
        """Get company description."""
        return self.config.get("tenant_info", {}).get("description", "")
    
    def get_contact_info(self) -> Dict[str, Optional[str]]:
        """Get contact information."""
        return self.config.get("tenant_info", {}).get("contact_info", {})
    
    # Personality Settings
    def get_tone(self) -> str:
        """Get communication tone (friendly, professional, casual, formal)."""
        return self.config.get("personality", {}).get("tone", "friendly")
    
    def get_formality_level(self) -> str:
        """Get formality level (casual, semi-formal, formal)."""
        return self.config.get("personality", {}).get("formality_level", "casual")
    
    def get_language(self) -> str:
        """Get response language."""
        return self.config.get("personality", {}).get("language", "es")
    
    def get_emoji_usage(self) -> str:
        """Get emoji usage level (none, minimal, moderate, frequent)."""
        return self.config.get("personality", {}).get("emoji_usage", "moderate")
    
    def get_response_length_preference(self) -> str:
        """Get preferred response length (brief, concise, detailed)."""
        return self.config.get("personality", {}).get("response_length", "concise")
    
    def get_brand_voice(self) -> str:
        """Get brand voice description."""
        return self.config.get("personality", {}).get("brand_voice", "")
    
    def get_custom_phrase(self, phrase_type: str) -> Optional[str]:
        """Get custom phrase (greeting, farewell, thanks, etc.)."""
        return self.config.get("personality", {}).get("custom_phrases", {}).get(phrase_type)
    
    # Sales Process
    def get_qualification_questions(self) -> List[str]:
        """Get lead qualification questions."""
        return self.config.get("sales_process", {}).get("qualification_questions", [])
    
    def is_upsell_enabled(self) -> bool:
        """Check if upselling is enabled."""
        return self.config.get("sales_process", {}).get("upsell_enabled", True)
    
    def is_cross_sell_enabled(self) -> bool:
        """Check if cross-selling is enabled."""
        return self.config.get("sales_process", {}).get("cross_sell_enabled", True)
    
    def has_discount_authority(self) -> bool:
        """Check if agent can offer discounts."""
        return self.config.get("sales_process", {}).get("discount_authority", False)
    
    def get_max_discount_percent(self) -> int:
        """Get maximum discount percentage allowed."""
        return self.config.get("sales_process", {}).get("max_discount_percent", 0)
    
    # Lead Management
    def is_auto_follow_up_enabled(self) -> bool:
        """Check if automatic follow-up is enabled."""
        return self.config.get("lead_management", {}).get("auto_follow_up", True)
    
    def get_follow_up_schedule(self) -> List[int]:
        """Get follow-up schedule (days after last interaction)."""
        return self.config.get("lead_management", {}).get("follow_up_schedule", [1, 3, 7])
    
    def get_follow_up_messages(self) -> List[Dict[str, Any]]:
        """Get configured follow-up messages."""
        return self.config.get("lead_management", {}).get("follow_up_messages", [])
    
    def get_qualification_threshold(self) -> int:
        """Get lead qualification score threshold."""
        return self.config.get("lead_management", {}).get("qualification_score_threshold", 75)
    
    def get_hot_lead_actions(self) -> List[str]:
        """Get actions to take for hot leads."""
        return self.config.get("lead_management", {}).get("hot_lead_actions", [])
    
    def get_lead_scoring_rules(self) -> Dict[str, int]:
        """Get lead scoring rules."""
        return self.config.get("lead_management", {}).get("lead_scoring_rules", {})
    
    # Response Settings
    def get_max_response_length(self) -> int:
        """Get maximum response length in characters."""
        return self.config.get("response_settings", {}).get("max_response_length", 500)
    
    def should_include_product_images(self) -> bool:
        """Check if product images should be included."""
        return self.config.get("response_settings", {}).get("include_product_images", True)
    
    def should_include_pricing(self) -> bool:
        """Check if pricing should be included in responses."""
        return self.config.get("response_settings", {}).get("include_pricing", True)
    
    def should_show_availability(self) -> bool:
        """Check if availability should be shown."""
        return self.config.get("response_settings", {}).get("show_availability", True)
    
    def get_response_delay(self) -> int:
        """Get response delay in seconds (to simulate thinking)."""
        return self.config.get("response_settings", {}).get("response_delay_seconds", 0)
    
    # Business Hours
    def is_business_hours_enabled(self) -> bool:
        """Check if business hours are enforced."""
        return self.config.get("business_hours", {}).get("enabled", False)
    
    def get_timezone(self) -> str:
        """Get business timezone."""
        return self.config.get("business_hours", {}).get("timezone", "America/La_Paz")
    
    def get_business_schedule(self) -> Dict[str, Dict[str, Optional[str]]]:
        """Get weekly business schedule."""
        return self.config.get("business_hours", {}).get("schedule", {})
    
    def get_after_hours_message(self) -> str:
        """Get message to send outside business hours."""
        return self.config.get("business_hours", {}).get(
            "after_hours_message",
            "Gracias por contactarnos. Te responderemos durante nuestro horario de atención."
        )
    
    # Conversation Settings
    def get_context_messages_limit(self) -> int:
        """Get maximum number of messages to include in context."""
        return self.config.get("conversation_settings", {}).get("context_messages_limit", 10)
    
    def get_session_timeout_minutes(self) -> int:
        """Get session timeout in minutes."""
        return self.config.get("conversation_settings", {}).get("session_timeout_minutes", 30)
    
    def get_handoff_keywords(self) -> List[str]:
        """Get keywords that trigger handoff to human."""
        return self.config.get("conversation_settings", {}).get("handoff_to_human_keywords", [])
    
    def is_auto_handoff_enabled(self) -> bool:
        """Check if automatic handoff is enabled."""
        return self.config.get("conversation_settings", {}).get("auto_handoff_enabled", False)


# Example usage:
"""
from app.utils.config_helper import AgentConfigHelper

# In your pipeline stage or service:
config_helper = AgentConfigHelper(context.agent_config)

# Access configuration values safely:
company_name = config_helper.get_company_name()
tone = config_helper.get_tone()
max_discount = config_helper.get_max_discount_percent()

if config_helper.is_upsell_enabled():
    # Show upsell products
    pass

if config_helper.has_discount_authority():
    max_discount = config_helper.get_max_discount_percent()
    # Can offer discounts up to max_discount %
"""
