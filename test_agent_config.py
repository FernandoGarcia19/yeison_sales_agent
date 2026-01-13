"""
Test script to demonstrate agent configuration loading.

Shows how the configuration is retrieved, cached, and accessed.
"""

import asyncio
from app.core.database import get_session_factory
from app.services.pipeline.stages.context_builder import ContextBuilderStage
from app.schemas.pipeline import PipelineContext, IntentType
from app.utils.config_helper import AgentConfigHelper


async def test_agent_config_loading():
    """Test loading agent configuration."""
    
    print("\n" + "="*60)
    print("🤖 Testing Agent Configuration Loading")
    print("="*60 + "\n")
    
    # Create a test context (normally created by previous stages)
    context = PipelineContext(
        message_sid="TEST001",
        sender_phone="+591766990995",
        recipient_phone="+14155238886",
        message_body="Hola, ¿qué productos tienes?",
        tenant_id=1,  # Would be set by identifier stage
        agent_instance_id=1,  # Would be set by identifier stage
        conversation_id=1,  # Would be set by identifier stage
        intent=IntentType.PRODUCT_INQUIRY
    )
    
    # Create context builder
    builder = ContextBuilderStage()
    
    try:
        # Process to load all context (including agent config)
        result_context = await builder.process(context)
        
        print("✅ Context loaded successfully!\n")
        
        # Show what was loaded
        print(f"📋 Agent Config Loaded: {'Yes' if result_context.agent_config else 'No'}")
        print(f"📜 Conversation History: {len(result_context.conversation_history)} messages")
        print(f"📦 Products Found: {len(result_context.relevant_products)}")
        print(f"👤 Lead Info: {'Yes' if result_context.lead_info else 'No'}")
        
        if result_context.agent_config:
            print("\n" + "-"*60)
            print("🎯 Agent Configuration Details:")
            print("-"*60 + "\n")
            
            # Use the helper to access config values
            config = AgentConfigHelper(result_context.agent_config)
            
            print(f"Agent Name: {config.get_agent_name()}")
            print(f"Company: {config.get_company_name()}")
            print(f"Industry: {config.get_industry()}")
            print(f"Tone: {config.get_tone()}")
            print(f"Formality: {config.get_formality_level()}")
            print(f"Language: {config.get_language()}")
            print(f"Emoji Usage: {config.get_emoji_usage()}")
            print(f"Response Length: {config.get_response_length_preference()}")
            
            print(f"\n💰 Sales Settings:")
            print(f"  Upsell Enabled: {config.is_upsell_enabled()}")
            print(f"  Cross-sell Enabled: {config.is_cross_sell_enabled()}")
            print(f"  Discount Authority: {config.has_discount_authority()}")
            print(f"  Max Discount: {config.get_max_discount_percent()}%")
            
            print(f"\n📞 Lead Management:")
            print(f"  Auto Follow-up: {config.is_auto_follow_up_enabled()}")
            print(f"  Follow-up Schedule: {config.get_follow_up_schedule()} days")
            print(f"  Qualification Threshold: {config.get_qualification_threshold()}")
            
            print(f"\n⏰ Business Hours:")
            print(f"  Enabled: {config.is_business_hours_enabled()}")
            print(f"  Timezone: {config.get_timezone()}")
            
            # Show custom phrases if configured
            greeting = config.get_custom_phrase("greeting")
            if greeting:
                print(f"\n💬 Custom Greeting:")
                print(f"  {greeting}")
            
            # Show full config structure (first level only)
            print(f"\n📊 Configuration Sections:")
            for section in result_context.agent_config.keys():
                print(f"  • {section}")
        
        print("\n" + "="*60)
        print("✅ Test completed successfully!")
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}\n")
        import traceback
        traceback.print_exc()


async def show_config_structure():
    """Show the expected configuration structure."""
    
    print("\n" + "="*60)
    print("📖 Agent Configuration Structure")
    print("="*60 + "\n")
    
    from app.services.pipeline.stages.context_builder import ContextBuilderStage
    
    builder = ContextBuilderStage()
    default_config = builder._get_default_agent_config()
    
    def print_structure(data, indent=0):
        """Recursively print config structure."""
        spacing = "  " * indent
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict):
                    print(f"{spacing}📁 {key}:")
                    print_structure(value, indent + 1)
                elif isinstance(value, list):
                    print(f"{spacing}📋 {key}: [{len(value)} items]")
                else:
                    value_str = str(value)
                    if len(value_str) > 50:
                        value_str = value_str[:47] + "..."
                    print(f"{spacing}• {key}: {value_str}")
    
    print_structure(default_config)
    
    print("\n" + "="*60 + "\n")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "structure":
        # Show structure only
        asyncio.run(show_config_structure())
    else:
        # Run full test
        print("\n💡 Note: This test requires a database with agent_instance data.")
        print("   If no agent is found, you'll see the default configuration.\n")
        print("   To see just the structure: python test_agent_config.py structure\n")
        
        asyncio.run(test_agent_config_loading())
