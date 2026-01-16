"""
Example agent configuration for reference.

This shows the NEW SPLIT structure:
- Agent-specific configuration stored in agent_instance.configuration
- Business-level configuration stored in configuration_tenant table

After the schema migration, agent_instance.configuration only stores
agent-specific settings (personality, sales process, integrations).
Business information is now stored in configuration_tenant.
"""

# =============================================================================
# AGENT-SPECIFIC CONFIGURATION (agent_instance.configuration JSONB field)
# =============================================================================

EXAMPLE_AGENT_CONFIG = {
    # Basic agent information
    "agent_info": {
        "name": "Yeison - Vendedor Virtual",
        "version": "1.0.0",
        "type": "sales"
    },
    
    # Integration settings
    "integrations": {
        "whatsapp_number": "591766990995"
    },
    
    # Personality and communication style
    "personality": {
        "tone": "friendly",                    # friendly, professional, casual, formal
        "formality_level": "casual",           # casual, semi-formal, formal
        "language": "es",                      # es (Spanish)
        "greeting_style": "warm",              # warm, professional, brief
        "emoji_usage": "moderate",             # none, minimal, moderate, frequent
        "response_length": "concise",          # brief, concise, detailed
        "brand_voice": "Somos una tienda familiar con más de 20 años de experiencia. Nos preocupamos por la calidad y la satisfacción del cliente.",
        "custom_phrases": {
            "greeting": "¡Hola! Soy Yeison, tu asesor virtual de Zapatería El Paso 👞",
            "farewell": "¡Gracias por tu tiempo! Estamos aquí para lo que necesites 😊",
            "thanks": "¡De nada! Es un placer ayudarte"
        }
    },
    
    # Sales process configuration (managed elsewhere - kept empty)
    "sales_process": {},
    
    # Lead management (managed elsewhere - kept empty)
    "lead_management": {},
    
    # Product catalog (managed through separate table - kept empty)
    "product_catalog": {},
    
    # Response behavior settings
    "response_settings": {
        "max_response_length": 500,
        "include_product_images": True,
        "include_pricing": True,
        "show_availability": True,
        "response_delay_seconds": 0,           # Simulate typing delay
        "typing_indicator_duration": 2         # Show typing indicator for N seconds
    },
    
    # Conversation management
    "conversation_settings": {
        "context_messages_limit": 10,
        "session_timeout_minutes": 30,
        "handoff_to_human_keywords": [
            "hablar con persona",
            "agente humano",
            "representante",
            "gerente"
        ],
        "auto_handoff_enabled": False
    }
}


# =============================================================================
# TENANT/BUSINESS CONFIGURATION (configuration_tenant table JSONB fields)
# =============================================================================

EXAMPLE_TENANT_CONFIG = {
    # Business JSONB field
    "business": {
        "company_name": "Zapatería El Paso",
        "industry": "Calzado y Accesorios",
        "company_size": "pequeña",               # pequeña, mediana, grande, enterprise
        "website": "www.zapaterialpaso.com",
        "location": "La Paz, Bolivia",
        "year_founded": "2003",
        "description": "Venta de zapatos premium y accesorios de cuero"
    },
    
    # Contact JSONB field
    "contact": {
        "contact_name": "María Gonzales",
        "contact_role": "Gerente de Ventas",
        "contact_email": "ventas@elpaso.com",
        "contact_phone": "591-77123456"
    },
    
    # Products JSONB field
    "products": {
        "unique_selling_points": "Calidad premium, diseños exclusivos, garantía de satisfacción, atención personalizada",
        "target_audience": "Profesionales de 25-45 años que buscan calzado de calidad para uso diario y ocasiones especiales",
        "payment_methods": "Efectivo, transferencia bancaria, tarjetas de crédito/débito, QR (pago móvil)"
    },
    
    # Operations JSONB field
    "operations": {
        "sales_process": "Consulta inicial → Identificación de necesidades → Presentación de opciones → Prueba (si es presencial) → Cierre de venta → Seguimiento post-venta",
        "common_questions": "¿Tienen mi talla? ¿Hacen envíos? ¿Cuál es la garantía? ¿Aceptan devoluciones? ¿Tienen catálogo actualizado?",
        "objections": "Precio alto: Destacar calidad y durabilidad. No es mi talla: Ofrecer pedido especial. Prefiero ver en persona: Invitar a la tienda o enviar fotos detalladas.",
        "closing_techniques": "Crear urgencia (stock limitado), ofrecer promoción del día, facilitar proceso de compra, garantía de satisfacción",
        "business_hours": {
            "enabled": True,
            "timezone": "America/La_Paz",
            "schedule": {
                "monday": {"open": "09:00", "close": "19:00"},
                "tuesday": {"open": "09:00", "close": "19:00"},
                "wednesday": {"open": "09:00", "close": "19:00"},
                "thursday": {"open": "09:00", "close": "19:00"},
                "friday": {"open": "09:00", "close": "19:00"},
                "saturday": {"open": "10:00", "close": "18:00"},
                "sunday": {"open": None, "close": None}
            },
            "after_hours_message": "Gracias por contactarnos. Nuestro horario de atención es de lunes a viernes de 9:00 a 19:00 y sábados de 10:00 a 18:00. Te responderemos en cuanto abramos. 🕐"
        },
        "response_time": "Inmediato (automatizado 24/7)",
        "languages": "Español",
        "competitors": "Zapatería Central, Calzados Miranda, importadoras online",
        "additional_context": "Somos una empresa familiar con más de 20 años en el mercado. Priorizamos la calidad sobre el volumen de ventas."
    }
}


# =============================================================================
# MINIMAL CONFIGURATION EXAMPLES
# =============================================================================

MINIMAL_AGENT_CONFIG = {
    "agent_info": {
        "name": "Asistente de Ventas",
        "version": "1.0.0",
        "type": "sales"
    },
    "personality": {
        "tone": "friendly",
        "language": "es"
    },
    "integrations": {
        "whatsapp_number": "591766990995"
    },
    "sales_process": {},
    "lead_management": {},
    "product_catalog": {}
}

MINIMAL_TENANT_CONFIG = {
    "business": {
        "company_name": "Mi Tienda",
        "industry": "Retail",
        "description": "Venta de productos diversos"
    },
    "contact": {
        "contact_email": "contacto@mitienda.com"
    }
}


# =============================================================================
# SQL USAGE EXAMPLES
# =============================================================================
        "include_product_images": True,
        "include_pricing": True,
        "show_availability": True,
        "response_delay_seconds": 0,           # Simulate typing delay
        "typing_indicator_duration": 2         # Show typing indicator for N seconds
    },
    
    # Business hours configuration
    "business_hours": {
        "enabled": True,
        "timezone": "America/La_Paz",
        "schedule": {
            "monday": {"open": "09:00", "close": "19:00"},
            "tuesday": {"open": "09:00", "close": "19:00"},
            "wednesday": {"open": "09:00", "close": "19:00"},
            "thursday": {"open": "09:00", "close": "19:00"},
            "friday": {"open": "09:00", "close": "19:00"},
            "saturday": {"open": "10:00", "close": "18:00"},
            "sunday": {"open": None, "close": None}  # Closed
        },
        "after_hours_message": "Gracias por contactarnos. Nuestro horario de atención es de lunes a viernes de 9:00 a 19:00 y sábados de 10:00 a 18:00. Te responderemos en cuanto abramos. 🕐"
    },
    
    # Conversation management
    "conversation_settings": {
        "context_messages_limit": 10,
        "session_timeout_minutes": 30,
        "handoff_to_human_keywords": [
            "hablar con persona",
            "agente humano",
            "representante",
            "gerente"
        ],
        "auto_handoff_enabled": False
    }
}


# =============================================================================
# SQL USAGE EXAMPLES
# =============================================================================

# How to use in SQL:
"""
-- 1. Create an agent instance with agent-specific configuration
INSERT INTO agent_instance (tenant_id, phone_number, agent_type, configuration, active)
VALUES (
    1,
    '+14155238886',
    'sales',
    '{
        "agent_info": {"name": "Yeison", "version": "1.0.0", "type": "sales"},
        "personality": {"tone": "friendly", "language": "es"},
        "integrations": {"whatsapp_number": "591766990995"}
    }'::jsonb,
    true
);

-- 2. Create tenant business configuration
INSERT INTO configuration_tenant (tenant_id, business, contact, products, operations, active, is_completed)
VALUES (
    1,
    '{
        "company_name": "Zapatería El Paso",
        "industry": "Calzado y Accesorios",
        "description": "Venta de zapatos premium"
    }'::jsonb,
    '{
        "contact_name": "María Gonzales",
        "contact_email": "ventas@elpaso.com",
        "contact_phone": "591-77123456"
    }'::jsonb,
    '{
        "unique_selling_points": "Calidad premium, diseños exclusivos",
        "target_audience": "Profesionales 25-45 años",
        "payment_methods": "Efectivo, tarjetas, QR"
    }'::jsonb,
    '{
        "sales_process": "Consulta → Presentación → Cierre",
        "common_questions": "¿Tienen mi talla? ¿Hacen envíos?",
        "business_hours": {"enabled": true, "timezone": "America/La_Paz"}
    }'::jsonb,
    true,
    true
);

-- 3. Update just the personality settings in agent config
UPDATE agent_instance
SET configuration = configuration || '{"personality": {"tone": "professional", "formality_level": "formal"}}'::jsonb
WHERE id = 1;

-- 4. Update business information in tenant config
UPDATE configuration_tenant
SET business = business || '{"company_size": "mediana", "year_founded": "2003"}'::jsonb
WHERE tenant_id = 1;

-- 5. Query agents with specific personality tone
SELECT * FROM agent_instance
WHERE configuration->'personality'->>'tone' = 'friendly';

-- 6. Get tenant with complete configuration
SELECT 
    t.id,
    t.name,
    ct.business->>'company_name' as company,
    ct.contact->>'contact_email' as email,
    ct.is_completed
FROM tenant t
LEFT JOIN configuration_tenant ct ON t.id = ct.tenant_id
WHERE ct.active = true;

-- 7. Find tenants missing business configuration
SELECT t.id, t.name, t.email
FROM tenant t
LEFT JOIN configuration_tenant ct ON t.id = ct.tenant_id
WHERE ct.id IS NULL OR ct.is_completed = false;
"""
