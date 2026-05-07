from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from sqlalchemy import select, update
from app.core.database import get_session_factory
from app.models import InventoryTenant, ConfigurationTenant, AgentInstance, Lead, SalesConversation
from app.core.redis_client import cache_get, cache_set, build_inventory_cache_key, build_agent_cache_key

class GetTenantInventoryInput(BaseModel):
    tenant_id: int = Field(..., description="The unique identifier of the tenant/business.")
    query: str = Field(..., description="The product name, description, or category the user is asking about. E.g., 'zapatos', 'laptop'.")

@tool("get_tenant_inventory", args_schema=GetTenantInventoryInput)
async def get_tenant_inventory(tenant_id: int, query: str) -> List[Dict[str, Any]]:
    """
    Retrieves the available products and their inventory for a specific tenant.
    Use this tool ONLY when the user is asking about product availability, prices, or descriptions.
    It returns a list of products matching the query that have quantity > 0.
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        # In a real scenario we'd use similarity search. For now, simple ILIKE matching
        search_pattern = f"%{query}%"
        from sqlalchemy import or_
        stmt = select(InventoryTenant).where(
            InventoryTenant.tenant_id == tenant_id,
            InventoryTenant.active == True,
            or_(InventoryTenant.quantity > 0, InventoryTenant.quantity.is_(None)),
            (InventoryTenant.product_name.ilike(search_pattern) |
             InventoryTenant.description.ilike(search_pattern))
        ).limit(5)
        
        result = await db.execute(stmt)
        products = result.scalars().all()
        
        return [
            {
                "id": p.id,
                "name": p.product_name,
                "price": str(p.price),
                "quantity": p.quantity,
                "description": p.description
            }
            for p in products
        ]


class GetTenantPoliciesInput(BaseModel):
    tenant_id: int = Field(..., description="The unique identifier of the tenant/business.")
    agent_instance_id: int = Field(..., description="The ID of the current agent instance.")

@tool("get_tenant_policies", args_schema=GetTenantPoliciesInput)
async def get_tenant_policies(tenant_id: int, agent_instance_id: int) -> Dict[str, Any]:
    """
    Retrieves the business policies, business hours, contact info, sales process, and rules for a tenant.
    Use this tool when the user queries about delivery rules, locations, contact info, 
    business hours, or general company information.
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        # Load agent instance
        agent_stmt = select(AgentInstance).where(AgentInstance.id == agent_instance_id)
        result = await db.execute(agent_stmt)
        agent = result.scalar_one_or_none()
        
        # Load tenant config
        tenant_stmt = select(ConfigurationTenant).where(
            ConfigurationTenant.tenant_id == tenant_id,
            ConfigurationTenant.active == True
        )
        tenant_result = await db.execute(tenant_stmt)
        tenant_config = tenant_result.scalar_one_or_none()

        policies = {
            "business_hours": {},
            "delivery_rules": {},
            "operations": {},
            "contact_info": {}
        }
        
        if tenant_config:
            if tenant_config.operations:
                policies["operations"] = tenant_config.operations
                policies["business_hours"] = tenant_config.operations.get("business_hours", {})
            if tenant_config.contact:
                policies["contact_info"] = tenant_config.contact
        
        if agent and agent.configuration:
            agent_op = agent.configuration.get("operations_info", {})
            if agent_op:
                policies["sales_process"] = agent_op.get("sales_process")
                policies["delivery_rules"] = agent_op.get("delivery_rules", "Standard delivery applies.")
                
        return policies


class GetLeadInfoInput(BaseModel):
    tenant_id: int = Field(..., description="The unique identifier of the tenant/business.")
    phone: str = Field(..., description="The phone number of the user in international format.")

@tool("get_lead_info", args_schema=GetLeadInfoInput)
async def get_lead_info(tenant_id: int, phone: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves lead profile information for a given phone number.
    Use this tool to find out if the user has past interactions, their name, or their lead score.
    Do not use this if the user hasn't provided a phone number implicitly.
    """
    clean_phone = phone.replace("whatsapp:", "").replace("+", "")
    
    session_factory = get_session_factory()
    async with session_factory() as db:
        stmt = select(Lead).where(
            Lead.tenant_id == tenant_id,
            Lead.phone.contains(clean_phone),
            Lead.active == True
        )
        
        result = await db.execute(stmt)
        lead = result.scalar_one_or_none()
        
        if not lead:
            return None
            
        return {
            "id": lead.id,
            "name": lead.name,
            "email": lead.email,
            "phone": lead.phone,
            "status": lead.status,
            "score": lead.score
        }


class GetCheckoutRequirementsInput(BaseModel):
    agent_instance_id: int = Field(..., description="The ID of the current agent instance.")

@tool("get_checkout_requirements", args_schema=GetCheckoutRequirementsInput)
async def get_checkout_requirements(agent_instance_id: int) -> str:
    """
    Retrieves the list of specific data points that must be collected from the user before proceeding to payment or checkout.
    Call this tool when the user indicates intent to purchase to know what information to request.
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        stmt = select(AgentInstance).where(AgentInstance.id == agent_instance_id)
        result = await db.execute(stmt)
        agent = result.scalar_one_or_none()
        
        if not agent or not agent.configuration:
            return "El usuario debe proporcionar: Nombre completo, Dirección de entrega, NIT"
            
        checkout_reqs = agent.configuration.get("checkout_requirements")
        if checkout_reqs:
            if isinstance(checkout_reqs, list):
                return ", ".join(checkout_reqs)
            return str(checkout_reqs)
            
        return "El usuario debe proporcionar: Nombre completo, Dirección de entrega, NIT"


class SaveCheckoutDataInput(BaseModel):
    conversation_id: int = Field(..., description="The ID of the current sales conversation.")
    collected_data: Dict[str, Any] = Field(..., description="The structured data collected from the user based on the checkout requirements.")

@tool("save_checkout_data", args_schema=SaveCheckoutDataInput)
async def save_checkout_data(conversation_id: int, collected_data: Dict[str, Any]) -> str:
    """
    Saves the structured checkout information collected from the user directly to the conversation.
    Use this after all checkout requirements have been collected.
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        stmt = select(SalesConversation).where(SalesConversation.id == conversation_id)
        result = await db.execute(stmt)
        conversation = result.scalar_one_or_none()
        
        if conversation:
            cart = dict(conversation.cart_contents) if conversation.cart_contents else {}
            cart["checkout_data"] = collected_data
            conversation.cart_contents = cart
            await db.commit()
            return "Checkout data successfully saved."
            
        return "Failed to save checkout data: Conversation not found."

# List of all available tools for easy binding
AVAILABLE_TOOLS = [get_tenant_inventory, get_tenant_policies, get_lead_info, get_checkout_requirements, save_checkout_data]
