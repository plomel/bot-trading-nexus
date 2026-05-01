#!/usr/bin/env python3
"""
Test script for Nexus Telegram commands
Tests the command patterns and basic functionality.
"""
import asyncio
import logging
import re
from telegram_handler import TelegramCommandHandler

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

class MockNexusInstance:
    """Mock Nexus instance for testing."""
    
    def __init__(self):
        self.start_time = asyncio.get_event_loop().time()
        self.positions = {
            '1000PEPE/USDT:USDT': {
                'symbol': '1000PEPE/USDT:USDT',
                'side': 'LONG',
                'entry': 0.00001234,
                'sl': 0.00001100,
                'tp': 0.00001400,
                'leverage': 10,
                'qty': 1000000,
                'opened': self.start_time - 3600
            }
        }
        self.shutdown_requested = False
    
    async def initiate_shutdown(self):
        """Mock shutdown initiation."""
        log.info("Mock shutdown initiated")
        self.shutdown_requested = True
        
        # Simulate shutdown process
        await asyncio.sleep(2)
        log.info("Mock shutdown completed")
    
    async def get_status(self):
        """Get mock status."""
        uptime = asyncio.get_event_loop().time() - self.start_time
        return {
            'paper_mode': True,
            'positions_count': len(self.positions),
            'circuit_breaker_active': False,
            'uptime': f"{int(uptime//3600)}h {int((uptime%3600)//60)}m",
            'balance': "1000.00",
            'daily_pnl': "2.5%"
        }
    
    async def get_positions(self):
        """Get mock positions."""
        positions_list = []
        for symbol, pos_data in self.positions.items():
            positions_list.append({
                'symbol': symbol.replace('1000', '').replace('/USDT:USDT', ''),
                'side': pos_data['side'],
                'entry': pos_data['entry'],
                'sl': pos_data['sl'],
                'tp': pos_data['tp'],
                'leverage': pos_data['leverage'],
                'pnl': 150.25
            })
        return positions_list
    
    async def get_risk_metrics(self):
        """Get mock risk metrics."""
        return {
            'daily_loss': 1.2,
            'daily_loss_cap': 8.0,
            'risk_per_trade': 1.0,
            'margin_used': 2.5,
            'emergency_count': 0,
            'circuit_breaker_active': False
        }

async def test_command_patterns():
    """Test command pattern matching."""
    log.info("Testing command patterns...")
    
    # Create mock handler
    handler = TelegramCommandHandler(
        token="test_token",
        chat_id="123456789",
        nexus_instance=MockNexusInstance()
    )
    
    # Test command patterns
    test_commands = [
        "/nexusstop",
        "/NEXUSSTOP", 
        "/NexusStop",
        "/nexusstatus",
        "/nexuspositions",
        "/nexusrisk",
        "/nexushelp",
        "invalid command",
        "/nexusunknown"
    ]
    
    for command in test_commands:
        log.info(f"Testing command: '{command}'")
        
        # Test pattern matching
        if handler.stop_pattern.match(command):
            log.info("  ✓ Matches STOP pattern")
        elif handler.status_pattern.match(command):
            log.info("  ✓ Matches STATUS pattern")
        elif handler.positions_pattern.match(command):
            log.info("  ✓ Matches POSITIONS pattern")
        elif handler.risk_pattern.match(command):
            log.info("  ✓ Matches RISK pattern")
        elif handler.help_pattern.match(command):
            log.info("  ✓ Matches HELP pattern")
        else:
            log.info("  ✗ No pattern match")

async def test_mock_responses():
    """Test mock command responses."""
    log.info("\nTesting mock command responses...")
    
    # Create mock handler with nexus instance
    mock_nexus = MockNexusInstance()
    handler = TelegramCommandHandler(
        token="test_token",
        chat_id="123456789",
        nexus_instance=mock_nexus
    )
    
    # Test status command
    try:
        status = await mock_nexus.get_status()
        log.info(f"Status response: {status}")
    except Exception as e:
        log.error(f"Status test failed: {e}")
    
    # Test positions command
    try:
        positions = await mock_nexus.get_positions()
        log.info(f"Positions response: {positions}")
    except Exception as e:
        log.error(f"Positions test failed: {e}")
    
    # Test risk metrics
    try:
        risk = await mock_nexus.get_risk_metrics()
        log.info(f"Risk metrics response: {risk}")
    except Exception as e:
        log.error(f"Risk metrics test failed: {e}")
    
    # Test shutdown (but don't actually run it)
    log.info("Shutdown command test - would initiate shutdown sequence")

async def test_telegram_validation():
    """Test Telegram token and chat ID validation."""
    log.info("\nTesting Telegram validation...")
    
    from security import security
    
    # Test token validation
    valid_tokens = [
        "123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        "987654321:XYZ-ABC9876mnJkl-abc12X9y2u987rf22"
    ]
    
    invalid_tokens = [
        "invalid_token",
        "123456789",
        "123456789:short",
        "123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11-extra"
    ]
    
    for token in valid_tokens:
        is_valid = security.validate_telegram_token(token)
        log.info(f"Token '{token[:20]}...' valid: {is_valid}")
    
    for token in invalid_tokens:
        is_valid = security.validate_telegram_token(token)
        log.info(f"Token '{token}' valid: {is_valid}")
    
    # Test chat ID validation
    valid_chat_ids = ["123456789", "-987654321", "0"]
    invalid_chat_ids = ["abc", "123.456", "", "12345678901234567890"]
    
    for chat_id in valid_chat_ids:
        is_valid = security.validate_telegram_chat_id(chat_id)
        log.info(f"Chat ID '{chat_id}' valid: {is_valid}")
    
    for chat_id in invalid_chat_ids:
        is_valid = security.validate_telegram_chat_id(chat_id)
        log.info(f"Chat ID '{chat_id}' valid: {is_valid}")

async def main():
    """Run all tests."""
    log.info("=== NEXUS TELEGRAM COMMANDS TEST ===\n")
    
    await test_command_patterns()
    await test_mock_responses()
    await test_telegram_validation()
    
    log.info("\n=== TESTS COMPLETED ===")
    log.info("\nTo use Telegram commands:")
    log.info("1. Ensure TELEGRAM_TOKEN and TELEGRAM_CHAT_ID are set in .env.nexus")
    log.info("2. Start the bot: python nexus.py --paper")
    log.info("3. Send commands to your Telegram bot:")
    log.info("   - '/nexusstop' (stops the bot)")
    log.info("   - '/nexusstatus' (shows bot status)")
    log.info("   - '/nexuspositions' (lists open positions)")
    log.info("   - '/nexusrisk' (shows risk metrics)")
    log.info("   - '/nexushelp' (shows help)")

if __name__ == "__main__":
    asyncio.run(main())
