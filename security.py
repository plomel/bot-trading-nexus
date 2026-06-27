"""
Bot Trading Nexus - Security Utilities
Provides encryption, validation, and security utilities for the trading bot.
"""
from __future__ import annotations
import base64
import hashlib
import json
import logging
import os
import re
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from typing import Any, Dict, Optional

log = logging.getLogger("nexus.security")

class SecurityManager:
    """Manages encryption, decryption, and input validation."""
    
    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize security manager.
        
        Args:
            encryption_key: Optional custom encryption key. If None, uses environment variable.
        """
        self.encryption_key = encryption_key or os.getenv("NEXUS_ENCRYPTION_KEY")
        if not self.encryption_key:
            log.warning("NEXUS_ENCRYPTION_KEY not set — generating ephemeral key (existing encrypted data will be lost)")
            self.encryption_key = os.urandom(32).hex()
        if isinstance(self.encryption_key, str):
            self.encryption_key = self.encryption_key.encode()
        
        # Derive encryption key
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'nexus_salt_2024',
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.encryption_key))
        self.cipher = Fernet(key)
    
    def encrypt_data(self, data: str) -> str:
        """Encrypt sensitive data."""
        try:
            encrypted = self.cipher.encrypt(data.encode())
            return base64.urlsafe_b64encode(encrypted).decode()
        except Exception as e:
            log.error(f"Encryption failed: {e}")
            raise
    
    def decrypt_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data."""
        try:
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted = self.cipher.decrypt(encrypted_bytes)
            return decrypted.decode()
        except Exception as e:
            log.error(f"Decryption failed: {e}")
            raise
    
    def validate_api_key(self, api_key: str) -> bool:
        """Validate Binance API key format."""
        if not api_key or not isinstance(api_key, str):
            return False
        # Binance API keys are typically 64 characters alphanumeric
        return bool(re.match(r'^[A-Za-z0-9]{64}$', api_key.strip()))
    
    def validate_secret_key(self, secret_key: str) -> bool:
        """Validate Binance secret key format."""
        if not secret_key or not isinstance(secret_key, str):
            return False
        # Binance secret keys are typically 64 characters alphanumeric
        return bool(re.match(r'^[A-Za-z0-9]{64}$', secret_key.strip()))
    
    def validate_telegram_token(self, token: str) -> bool:
        """Validate Telegram bot token format."""
        if not token or not isinstance(token, str):
            return False
        # Telegram tokens:数字:字符串 (e.g., 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11)
        # Allow variable length after colon, typically 35-46 characters
        return bool(re.match(r'^\d+:[A-Za-z0-9_-]{35,46}$', token.strip()))
    
    def validate_telegram_chat_id(self, chat_id: str) -> bool:
        """Validate Telegram chat ID format."""
        if not chat_id or not isinstance(chat_id, str):
            return False
        # Chat IDs are numeric (can be negative for groups)
        return bool(re.match(r'^-?\d+$', chat_id.strip()))
    
    def sanitize_input(self, input_str: str, max_length: int = 1000) -> str:
        """Sanitize user input to prevent injection attacks."""
        if not isinstance(input_str, str):
            return ""
        
        # Remove potentially dangerous characters
        sanitized = re.sub(r'[<>"\'\x00-\x1f\x7f-\x9f]', '', input_str)
        
        # Limit length
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length]
        
        return sanitized.strip()
    
    def validate_symbol(self, symbol: str) -> bool:
        """Validate trading symbol format."""
        if not symbol or not isinstance(symbol, str):
            return False
        
        # Allow alphanumeric symbols and common separators
        return bool(re.match(r'^[A-Za-z0-9/:-]+$', symbol.strip()))
    
    def validate_numeric_input(self, value: Any, min_val: Optional[float] = None, 
                            max_val: Optional[float] = None) -> bool:
        """Validate numeric input with optional bounds."""
        try:
            num_val = float(value)
            if min_val is not None and num_val < min_val:
                return False
            if max_val is not None and num_val > max_val:
                return False
            return True
        except (ValueError, TypeError):
            return False
    
    def create_secure_hash(self, data: str) -> str:
        """Create a secure hash of data for verification."""
        return hashlib.sha256(data.encode()).hexdigest()
    
    def verify_hash(self, data: str, expected_hash: str) -> bool:
        """Verify data against expected hash."""
        return self.create_secure_hash(data) == expected_hash

# Global security manager instance
security = SecurityManager()

def encrypt_sensitive_data(data: Dict[str, str]) -> Dict[str, str]:
    """Encrypt sensitive configuration data."""
    encrypted = {}
    for key, value in data.items():
        if "SECRET" in key.upper() or "KEY" in key.upper() and "TOKEN" not in key.upper():
            try:
                encrypted[key] = security.encrypt_data(value)
            except Exception as e:
                log.error(f"Failed to encrypt {key}: {e}")
                encrypted[key] = value  # Fallback to unencrypted
        else:
            encrypted[key] = value
    return encrypted

def decrypt_sensitive_data(data: Dict[str, str]) -> Dict[str, str]:
    """Decrypt sensitive configuration data."""
    decrypted = {}
    for key, value in data.items():
        if "SECRET" in key.upper() or "KEY" in key.upper() and "TOKEN" not in key.upper():
            try:
                decrypted[key] = security.decrypt_data(value)
            except Exception as e:
                log.error(f"Failed to decrypt {key}: {e}")
                decrypted[key] = value  # Fallback to unencrypted
        else:
            decrypted[key] = value
    return decrypted

def validate_configuration(config: Dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate entire configuration dictionary."""
    errors = []
    
    # Validate API keys
    if "BINANCE_API_KEY" in config:
        if not security.validate_api_key(config["BINANCE_API_KEY"]):
            errors.append("Invalid BINANCE_API_KEY format")
    
    if "BINANCE_SECRET" in config:
        if not security.validate_secret_key(config["BINANCE_SECRET"]):
            errors.append("Invalid BINANCE_SECRET format")
    
    # Validate Telegram settings
    if "TELEGRAM_TOKEN" in config:
        if not security.validate_telegram_token(config["TELEGRAM_TOKEN"]):
            errors.append("Invalid TELEGRAM_TOKEN format")
    
    if "TELEGRAM_CHAT_ID" in config:
        if not security.validate_telegram_chat_id(config["TELEGRAM_CHAT_ID"]):
            errors.append("Invalid TELEGRAM_CHAT_ID format")
    
    # Validate numeric settings
    numeric_settings = {
        "MIN_SCORE": (0, 100),
        "DEFAULT_LEVERAGE": (1, 125),
        "MAX_LEVERAGE": (1, 125),
        "MAX_RISK_PER_TRADE": (0.1, 10),
        "MAX_DAILY_LOSS": (1, 50),
        "MAX_MARGIN_PCT": (1, 20),
        "ENTRY_COOLDOWN_SEC": (10, 3600),
        "MAX_POSITIONS": (1, 20),
        "FUNDING_MAX_PCT": (0.01, 1),
    }
    
    for setting, (min_val, max_val) in numeric_settings.items():
        if setting in config:
            if not security.validate_numeric_input(config[setting], min_val, max_val):
                errors.append(f"Invalid {setting}: must be between {min_val} and {max_val}")
    
    return len(errors) == 0, errors
