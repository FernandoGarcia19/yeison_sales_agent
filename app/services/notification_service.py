"""
Notification service - sends notifications to supervisors/managers.
"""

from typing import Dict, Any, Optional, List
import structlog
from datetime import datetime

from app.integrations.whatsapp.client import send_whatsapp_message

logger = structlog.get_logger()


class NotificationService:
    """Service for sending notifications about sales events."""
    
    @staticmethod
    async def notify_sale_completed(
        supervisor_number: str,
        agent_phone: str,
        customer_phone: str,
        customer_name: Optional[str],
        products: List[Dict[str, Any]],
        lead_info: Optional[Dict[str, Any]] = None,
        conversation_summary: Optional[str] = None
    ) -> bool:
        """
        Notify supervisor/manager about a completed sale.
        
        Args:
            supervisor_number: Supervisor's WhatsApp number (E.164 format)
            agent_phone: Agent's phone number
            customer_phone: Customer's phone number
            customer_name: Customer's name if available
            products: List of purchased products
            lead_info: Optional lead information
            conversation_summary: Optional conversation summary
            
        Returns:
            True if notification was sent successfully
        """
        
        logger.info(
            "sending_sale_notification",
            supervisor=supervisor_number,
            customer=customer_phone
        )
        
        # Build notification message
        message = NotificationService._format_sale_notification(
            customer_phone=customer_phone,
            customer_name=customer_name,
            products=products,
            lead_info=lead_info,
            conversation_summary=conversation_summary
        )
        
        try:
            # Send WhatsApp message to supervisor
            message_sid = await send_whatsapp_message(
                to=supervisor_number,
                body=message,
                from_number=agent_phone
            )
            
            logger.info(
                "sale_notification_sent",
                message_sid=message_sid,
                supervisor=supervisor_number
            )
            
            return True
            
        except Exception as e:
            logger.error(
                "failed_to_send_sale_notification",
                supervisor=supervisor_number,
                error=str(e),
                error_type=type(e).__name__
            )
            return False
    
    @staticmethod
    def _format_sale_notification(
        customer_phone: str,
        customer_name: Optional[str],
        products: List[Dict[str, Any]],
        lead_info: Optional[Dict[str, Any]] = None,
        conversation_summary: Optional[str] = None
    ) -> str:
        """Format a sale notification message."""
        
        # Header
        message_parts = [
            "🎉 *¡VENTA FINALIZADA!* 🎉",
            "",
            "📊 *Detalles de la compra:*",
            ""
        ]
        
        # Customer information
        customer_display = customer_name or customer_phone
        message_parts.append(f"👤 *Cliente:* {customer_display}")
        message_parts.append(f"📱 *Teléfono:* {customer_phone}")
        
        if lead_info:
            if lead_info.get("email"):
                message_parts.append(f"📧 *Email:* {lead_info['email']}")
            if lead_info.get("source"):
                message_parts.append(f"📍 *Fuente:* {lead_info['source']}")
        
        message_parts.append("")
        
        # Products purchased
        if products:
            message_parts.append("🛒 *Productos:*")
            total = 0.0
            
            for i, product in enumerate(products, 1):
                product_name = product.get("product_name", "Producto")
                price = product.get("price", 0)
                quantity = product.get("quantity", 1)
                subtotal = float(price) * quantity
                total += subtotal
                
                message_parts.append(
                    f"{i}. {product_name}\n"
                    f"   💰 ${price:.2f} x {quantity} = ${subtotal:.2f}"
                )
            
            message_parts.append("")
            message_parts.append(f"💵 *TOTAL: ${total:.2f}*")
            message_parts.append("")
        
        # Additional context
        if conversation_summary:
            message_parts.append("💬 *Resumen de conversación:*")
            message_parts.append(conversation_summary)
            message_parts.append("")
        
        # Footer
        message_parts.append(f"⏰ *Fecha:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        message_parts.append("")
        message_parts.append("✅ El lead ha sido marcado como convertido.")
        
        return "\n".join(message_parts)
    
    @staticmethod
    async def notify_handoff_request(
        supervisor_number: str,
        agent_phone: str,
        customer_phone: str,
        customer_name: Optional[str],
        reason: str,
        conversation_context: Optional[str] = None
    ) -> bool:
        """
        Notify supervisor about a handoff request (customer wants to talk to human).
        
        Args:
            supervisor_number: Supervisor's WhatsApp number
            agent_phone: Agent's phone number
            customer_phone: Customer's phone number
            customer_name: Customer's name if available
            reason: Reason for handoff
            conversation_context: Recent conversation context
            
        Returns:
            True if notification was sent successfully
        """
        
        logger.info(
            "sending_handoff_notification",
            supervisor=supervisor_number,
            customer=customer_phone
        )
        
        # Build notification message
        message_parts = [
            "🚨 *SOLICITUD DE ATENCIÓN HUMANA* 🚨",
            "",
            "Un cliente solicita hablar con un humano:",
            "",
            f"👤 *Cliente:* {customer_name or customer_phone}",
            f"📱 *Teléfono:* {customer_phone}",
            f"📝 *Motivo:* {reason}",
            ""
        ]
        
        if conversation_context:
            message_parts.append("💬 *Contexto:*")
            message_parts.append(conversation_context)
            message_parts.append("")
        
        message_parts.append("⏰ Por favor, contacta al cliente lo antes posible.")
        
        message = "\n".join(message_parts)
        
        try:
            message_sid = await send_whatsapp_message(
                to=supervisor_number,
                body=message,
                from_number=agent_phone
            )
            
            logger.info(
                "handoff_notification_sent",
                message_sid=message_sid,
                supervisor=supervisor_number
            )
            
            return True
            
        except Exception as e:
            logger.error(
                "failed_to_send_handoff_notification",
                supervisor=supervisor_number,
                error=str(e)
            )
            return False
    
    @staticmethod
    async def notify_high_value_lead(
        supervisor_number: str,
        agent_phone: str,
        customer_phone: str,
        customer_name: Optional[str],
        lead_score: int,
        products_interested: List[Dict[str, Any]],
        reason: str
    ) -> bool:
        """
        Notify supervisor about a high-value lead that needs attention.
        
        Args:
            supervisor_number: Supervisor's WhatsApp number
            agent_phone: Agent's phone number
            customer_phone: Customer's phone number
            customer_name: Customer's name if available
            lead_score: Lead score
            products_interested: Products the customer is interested in
            reason: Why this is a high-value lead
            
        Returns:
            True if notification was sent successfully
        """
        
        message_parts = [
            "⭐ *LEAD DE ALTO VALOR* ⭐",
            "",
            f"👤 *Cliente:* {customer_name or customer_phone}",
            f"📱 *Teléfono:* {customer_phone}",
            f"🎯 *Score:* {lead_score}/100",
            f"📈 *Razón:* {reason}",
            ""
        ]
        
        if products_interested:
            message_parts.append("💎 *Productos de interés:*")
            for product in products_interested:
                message_parts.append(f"  • {product.get('product_name')}")
            message_parts.append("")
        
        message_parts.append("💡 Considera dar seguimiento personalizado a este lead.")
        
        message = "\n".join(message_parts)
        
        try:
            await send_whatsapp_message(
                to=supervisor_number,
                body=message,
                from_number=agent_phone
            )
            return True
        except Exception as e:
            logger.error("failed_to_send_high_value_lead_notification", error=str(e))
            return False
