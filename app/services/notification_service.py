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
    
    @staticmethod
    async def send_qr_payment_request(
        customer_phone: str,
        agent_phone: str,
        qr_image_url: str,
        customer_name: Optional[str] = None
    ) -> bool:
        """
        Send QR code payment request to customer.
        
        Args:
            customer_phone: Customer's WhatsApp number
            agent_phone: Agent's phone number
            qr_image_url: URL of the QR code image (PNG/JPG)
            customer_name: Optional customer name
            
        Returns:
            True if message was sent successfully
        """
        
        logger.info(
            "sending_qr_payment_request",
            customer=customer_phone,
            qr_url=qr_image_url,
            agent=agent_phone
        )
        
        # Build predefined message
        greeting = f"¡Hola {customer_name}! " if customer_name else "¡Hola! "
        message = (
            f"{greeting}"
            f"Tu compra está casi lista. 🎉\n\n"
            f"Por favor, escanea el siguiente código QR con tu billetera digital "
            f"o aplicación de pago para completar tu compra:\n\n"
            f"👇 Escanea el QR 👇"
        )
        
        try:
            # Send message with QR image
            logger.info(
                "qr_request_before_send",
                customer=customer_phone,
                message_length=len(message),
                has_media_url=bool(qr_image_url),
                media_url_length=len(qr_image_url) if qr_image_url else 0
            )
            
            message_sid = await send_whatsapp_message(
                to=customer_phone,
                body=message,
                from_number=agent_phone,
                media_url=qr_image_url
            )
            
            logger.info(
                "qr_payment_request_sent",
                message_sid=message_sid,
                customer=customer_phone,
                qr_url=qr_image_url
            )
            
            return True
            
        except Exception as e:
            logger.error(
                "failed_to_send_qr_payment_request",
                customer=customer_phone,
                error=str(e),
                error_type=type(e).__name__,
                qr_url=qr_image_url
            )
            return False
    
    @staticmethod
    async def forward_payment_proof_to_supervisor(
        supervisor_number: str,
        agent_phone: str,
        customer_phone: str,
        customer_name: Optional[str],
        products: List[Dict[str, Any]],
        proof_media_url: str,
        lead_info: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Forward payment proof from customer to supervisor.
        
        Args:
            supervisor_number: Supervisor's WhatsApp number
            agent_phone: Agent's phone number
            customer_phone: Customer's phone number
            customer_name: Customer's name if available
            products: List of purchased products
            proof_media_url: URL of payment proof (image/document)
            lead_info: Optional lead information
            
        Returns:
            True if notification was sent successfully
        """
        
        logger.info(
            "forwarding_payment_proof",
            supervisor=supervisor_number,
            customer=customer_phone,
            proof_url=proof_media_url
        )
        
        # Build predefined message for supervisor
        message_parts = [
            "📦 *COMPRA CONFIRMADA CON COMPROBANTE* 📦",
            "",
            "📊 *Detalles de la compra:*",
            ""
        ]
        
        # Customer information
        customer_display = customer_name or customer_phone
        message_parts.append(f"👤 *Cliente:* {customer_display}")
        message_parts.append(f"📱 *Teléfono:* {customer_phone}")
        
        if lead_info:
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
        
        # Proof info
        message_parts.append("📎 *Comprobante de pago:* Ver archivo adjunto")
        message_parts.append("")
        message_parts.append(f"⏰ *Fecha:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        message_parts.append("")
        message_parts.append("✅ El lead ha sido marcado como convertido.")
        
        message = "\n".join(message_parts)
        
        try:
            # Send message with proof attachment
            message_sid = await send_whatsapp_message(
                to=supervisor_number,
                body=message,
                from_number=agent_phone,
                media_url=proof_media_url
            )
            
            logger.info(
                "payment_proof_forwarded",
                message_sid=message_sid,
                supervisor=supervisor_number
            )
            
            return True
            
        except Exception as e:
            logger.error(
                "failed_to_forward_payment_proof",
                supervisor=supervisor_number,
                error=str(e)
            )
            return False

