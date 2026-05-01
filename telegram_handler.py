"""
Bot Trading Nexus - Telegram Command Handler
Handles Telegram bot commands including "barra Nexus stop" functionality.
"""
from __future__ import annotations
import asyncio
import logging
import re
import time
from typing import Optional, Dict, Any, Callable
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import security

log = logging.getLogger("nexus.telegram")

class TelegramCommandHandler:
    """Handles Telegram bot commands for Nexus trading bot."""
    
    def __init__(self, token: str, chat_id: str, nexus_instance=None):
        """
        Initialize Telegram command handler.
        
        Args:
            token: Telegram bot token
            chat_id: Authorized chat ID
            nexus_instance: Reference to main Nexus bot instance
        """
        self.token = token
        self.chat_id = str(chat_id)
        self.nexus_instance = nexus_instance
        self.authorized_chat_id = str(chat_id)
        self.shutdown_requested = False
        self.shutdown_initiated_at = None
        
        # Command patterns
        self.stop_pattern = re.compile(r'(?i)^/nexusstop$')
        self.status_pattern = re.compile(r'(?i)^/nexusstatus$')
        self.positions_pattern = re.compile(r'(?i)^/nexuspositions$')
        self.risk_pattern = re.compile(r'(?i)^/nexusrisk$')
        self.help_pattern = re.compile(r'(?i)^/nexushelp$')
        
        # Initialize bot
        self.application = None
        self.bot = None
        
    async def initialize(self) -> bool:
        """Initialize Telegram bot application."""
        try:
            # Validate token and chat ID
            if not security.validate_telegram_token(self.token):
                log.error("Invalid Telegram token format")
                return False
            
            if not security.validate_telegram_chat_id(self.chat_id):
                log.error("Invalid Telegram chat ID format")
                return False
            
            # Create application
            self.application = Application.builder().token(self.token).build()
            self.bot = self.application.bot
            
            # Add handlers
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            
            log.info("Telegram command handler initialized successfully")
            return True
            
        except Exception as e:
            log.error(f"Failed to initialize Telegram handler: {e}")
            return False
    
    async def start_polling(self) -> None:
        """Start polling for Telegram messages."""
        if not self.application:
            log.error("Telegram application not initialized")
            return
        
        try:
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(drop_pending_updates=True)
            log.info("Telegram polling started")
            
        except Exception as e:
            log.error(f"Failed to start Telegram polling: {e}")
    
    async def stop_polling(self) -> None:
        """Stop Telegram polling."""
        if self.application:
            try:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
                log.info("Telegram polling stopped")
            except Exception as e:
                log.error(f"Error stopping Telegram polling: {e}")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming Telegram messages."""
        if not update.message or not update.message.text:
            return
        
        # Check authorization
        chat_id = str(update.effective_chat.id)
        if chat_id != self.authorized_chat_id:
            log.warning(f"Unauthorized message from chat {chat_id}")
            return
        
        message_text = update.message.text.strip()
        log.info(f"Received Telegram message: {message_text}")
        
        # Process commands
        if self.stop_pattern.match(message_text):
            await self.handle_stop_command(update)
        elif self.status_pattern.match(message_text):
            await self.handle_status_command(update)
        elif self.positions_pattern.match(message_text):
            await self.handle_positions_command(update)
        elif self.risk_pattern.match(message_text):
            await self.handle_risk_command(update)
        elif self.help_pattern.match(message_text):
            await self.handle_help_command(update)
        else:
            await self.handle_unknown_command(update, message_text)
    
    async def handle_stop_command(self, update: Update) -> None:
        """Handle 'barra Nexus stop' command."""
        if self.shutdown_requested:
            await update.message.reply_text(
                "🛑 <b>Shutdown already in progress</b>\n"
                "Please wait for the current shutdown sequence to complete."
            )
            return
        
        self.shutdown_requested = True
        self.shutdown_initiated_at = time.time()
        
        await update.message.reply_text(
            "🚨 <b>NEXUS SHUTDOWN INITIATED</b>\n\n"
            "⏸️ <b>Stopping new entries...</b>\n"
            "🔄 <b>Closing all positions...</b>\n"
            "💾 <b>Saving state...</b>\n\n"
            "⏳ <i>This may take up to 60 seconds...</i>"
        )
        
        try:
            # Initiate shutdown sequence
            if self.nexus_instance:
                await self.nexus_instance.initiate_shutdown()
            else:
                log.error("No nexus instance available for shutdown")
                await update.message.reply_text(
                    "❌ <b>Error: No Nexus instance available</b>\n"
                    "Please check the bot logs."
                )
                
        except Exception as e:
            log.error(f"Error during shutdown: {e}")
            await update.message.reply_text(
                f"❌ <b>Shutdown failed:</b> {str(e)}\n"
                "Please check the bot logs and consider manual intervention."
            )
    
    async def handle_status_command(self, update: Update) -> None:
        """Handle 'barra Nexus status' command."""
        if not self.nexus_instance:
            await update.message.reply_text(
                "❌ <b>Status unavailable:</b> No Nexus instance connected"
            )
            return
        
        try:
            status_data = await self.nexus_instance.get_status()
            
            mode = "📋 PAPER" if status_data.get('paper_mode', False) else "⚡ LIVE"
            positions_count = status_data.get('positions_count', 0)
            circuit_breaker = "🔴 ACTIVE" if status_data.get('circuit_breaker_active', False) else "🟢 OK"
            uptime = status_data.get('uptime', 'Unknown')
            
            message = (
                f"🤖 <b>NEXUS STATUS</b>\n\n"
                f"📊 <b>Mode:</b> {mode}\n"
                f"📈 <b>Open Positions:</b> {positions_count}\n"
                f"🛡️ <b>Circuit Breaker:</b> {circuit_breaker}\n"
                f"⏰ <b>Uptime:</b> {uptime}\n"
                f"💰 <b>Balance:</b> ${status_data.get('balance', 'N/A')}\n"
                f"📊 <b>Daily P&L:</b> {status_data.get('daily_pnl', 'N/A')}"
            )
            
            await update.message.reply_text(message)
            
        except Exception as e:
            log.error(f"Error getting status: {e}")
            await update.message.reply_text(
                f"❌ <b>Status error:</b> {str(e)}"
            )
    
    async def handle_positions_command(self, update: Update) -> None:
        """Handle 'barra Nexus positions' command."""
        if not self.nexus_instance:
            await update.message.reply_text(
                "❌ <b>Positions unavailable:</b> No Nexus instance connected"
            )
            return
        
        try:
            positions_data = await self.nexus_instance.get_positions()
            
            if not positions_data:
                await update.message.reply_text(
                    "📊 <b>OPEN POSITIONS</b>\n\n"
                    "🟢 <i>No positions currently open</i>"
                )
                return
            
            message_parts = ["📊 <b>OPEN POSITIONS</b>\n"]
            
            for pos in positions_data:
                symbol = pos.get('symbol', 'Unknown')
                side = pos.get('side', 'Unknown')
                entry = pos.get('entry', 0)
                sl = pos.get('sl', 0)
                tp = pos.get('tp', 0)
                leverage = pos.get('leverage', 0)
                pnl = pos.get('pnl', 0)
                
                side_emoji = "🟢" if side == "LONG" else "🔴" if side == "SHORT" else "⚪"
                pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➖"
                
                message_parts.append(
                    f"{side_emoji} <b>{symbol}</b> {side} {leverage}x\n"
                    f"💰 Entry: ${entry:.6f}\n"
                    f"🛡️ SL: ${sl:.6f}\n"
                    f"🎯 TP: ${tp:.6f}\n"
                    f"{pnl_emoji} P&L: ${pnl:.4f}\n"
                )
            
            await update.message.reply_text("\n".join(message_parts))
            
        except Exception as e:
            log.error(f"Error getting positions: {e}")
            await update.message.reply_text(
                f"❌ <b>Positions error:</b> {str(e)}"
            )
    
    async def handle_risk_command(self, update: Update) -> None:
        """Handle 'barra Nexus risk' command."""
        if not self.nexus_instance:
            await update.message.reply_text(
                "❌ <b>Risk data unavailable:</b> No Nexus instance connected"
            )
            return
        
        try:
            risk_data = await self.nexus_instance.get_risk_metrics()
            
            daily_loss = risk_data.get('daily_loss', 0)
            daily_loss_cap = risk_data.get('daily_loss_cap', 0)
            risk_per_trade = risk_data.get('risk_per_trade', 0)
            margin_used = risk_data.get('margin_used', 0)
            emergency_count = risk_data.get('emergency_count', 0)
            
            loss_percentage = (daily_loss / daily_loss_cap * 100) if daily_loss_cap > 0 else 0
            loss_emoji = "🟢" if loss_percentage < 50 else "🟡" if loss_percentage < 80 else "🔴"
            
            message = (
                f"🛡️ <b>RISK METRICS</b>\n\n"
                f"{loss_emoji} <b>Daily Loss:</b> {daily_loss:.2f}% / {daily_loss_cap:.2f}%\n"
                f"💰 <b>Risk per Trade:</b> {risk_per_trade:.2f}%\n"
                f"📊 <b>Margin Used:</b> {margin_used:.2f}%\n"
                f"🚨 <b>Emergency Events:</b> {emergency_count}\n"
                f"⏰ <b>Circuit Breaker:</b> {'Active' if risk_data.get('circuit_breaker_active') else 'Inactive'}"
            )
            
            await update.message.reply_text(message)
            
        except Exception as e:
            log.error(f"Error getting risk metrics: {e}")
            await update.message.reply_text(
                f"❌ <b>Risk metrics error:</b> {str(e)}"
            )
    
    async def handle_help_command(self, update: Update) -> None:
        """Handle 'barra Nexus help' command."""
        help_message = (
            "🤖 <b>NEXUS COMMANDS</b>\n\n"
            "🛑 <code>/nexusstop</code>\n"
            "   └─ Stop the bot and close all positions\n\n"
            "📊 <code>/nexusstatus</code>\n"
            "   └─ Show current bot status\n\n"
            "📈 <code>/nexuspositions</code>\n"
            "   └─ List all open positions\n\n"
            "🛡️ <code>/nexusrisk</code>\n"
            "   └─ Show risk metrics and limits\n\n"
            "❓ <code>/nexushelp</code>\n"
            "   └─ Show this help message\n\n"
            "<i>All commands are case-insensitive.</i>"
        )
        
        await update.message.reply_text(help_message)
    
    async def handle_unknown_command(self, update: Update, message_text: str) -> None:
        """Handle unknown commands."""
        await update.message.reply_text(
            f"❓ <b>Unknown command:</b> <code>{message_text}</code>\n\n"
            "Use <code>barra Nexus help</code> to see available commands."
        )
    
    async def send_shutdown_complete(self, success: bool, error_message: Optional[str] = None) -> None:
        """Send shutdown completion notification."""
        if success:
            message = (
                "✅ <b>NEXUS SHUTDOWN COMPLETE</b>\n\n"
                "🔄 All positions closed\n"
                "💾 State saved\n"
                "🛡️ Risk management disabled\n\n"
                "<i>Bot is now safely stopped.</i>"
            )
        else:
            message = (
                "❌ <b>NEXUS SHUTDOWN FAILED</b>\n\n"
                f"🚨 Error: {error_message}\n\n"
                "<i>Please check the logs and consider manual intervention.</i>"
            )
        
        try:
            if self.bot:
                await self.bot.send_message(chat_id=self.authorized_chat_id, text=message)
        except Exception as e:
            log.error(f"Failed to send shutdown notification: {e}")
    
    async def send_error_notification(self, error_message: str) -> None:
        """Send error notification to Telegram."""
        message = (
            f"🚨 <b>NEXUS ERROR</b>\n\n"
            f"❌ {error_message}\n\n"
            "<i>Please check the bot logs.</i>"
        )
        
        try:
            if self.bot:
                await self.bot.send_message(chat_id=self.authorized_chat_id, text=message)
        except Exception as e:
            log.error(f"Failed to send error notification: {e}")
    
    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested."""
        return self.shutdown_requested
    
    def get_shutdown_duration(self) -> Optional[float]:
        """Get duration since shutdown was requested."""
        if self.shutdown_initiated_at:
            return time.time() - self.shutdown_initiated_at
        return None
