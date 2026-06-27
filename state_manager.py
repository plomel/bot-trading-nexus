"""
Bot Trading Nexus - Thread-Safe State Manager
Manages shared state with async locks and memory-efficient buffers.
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Dict, List, Any, Optional, Tuple
from collections import deque
from dataclasses import dataclass, field
import numpy as np

log = logging.getLogger("nexus.state_manager")

@dataclass
class BufferConfig:
    """Configuration for candle buffers."""
    max_size: int = 200
    cleanup_threshold: int = 150
    cleanup_interval: int = 1000  # candles

class ThreadSafeStateManager:
    """Thread-safe state manager with memory-efficient buffers."""
    
    def __init__(self, buffer_config: Optional[BufferConfig] = None):
        """
        Initialize thread-safe state manager.
        
        Args:
            buffer_config: Buffer configuration
        """
        self.buffer_config = buffer_config or BufferConfig()
        
        # Async locks for different resources
        self.positions_lock = asyncio.Lock()
        self.candles_lock = asyncio.Lock()
        self.cooldowns_lock = asyncio.Lock()
        self.signals_lock = asyncio.Lock()
        
        # Shared state
        self._positions: Dict[str, Dict] = {}
        self._candle_buffers: Dict[Tuple[str, str], deque] = {}
        self._cooldowns: Dict[str, float] = {}
        self._last_signals: Dict[str, Tuple[str, float, float]] = {}
        
        # Buffer cleanup tracking
        self._buffer_updates: Dict[Tuple[str, str], int] = {}
        
        # Statistics
        self.stats = {
            'buffer_cleanups': 0,
            'memory_saved_mb': 0.0,
            'lock_contentions': 0
        }
    
    async def get_positions(self) -> Dict[str, Dict]:
        """Get positions safely."""
        async with self.positions_lock:
            return self._positions.copy()
    
    async def update_position(self, symbol: str, position_data: Dict) -> None:
        """Update position safely."""
        async with self.positions_lock:
            self._positions[symbol] = position_data.copy()
    
    async def remove_position(self, symbol: str) -> Optional[Dict]:
        """Remove position safely."""
        async with self.positions_lock:
            return self._positions.pop(symbol, None)
    
    async def clear_positions(self) -> None:
        """Clear all positions safely."""
        async with self.positions_lock:
            self._positions.clear()
    
    async def get_candle_buffer(self, symbol: str, timeframe: str) -> List:
        """Get candle buffer safely."""
        async with self.candles_lock:
            buffer_key = (symbol, timeframe)
            buffer = self._candle_buffers.get(buffer_key, deque(maxlen=self.buffer_config.max_size))
            return list(buffer)
    
    async def update_candle_buffer(self, symbol: str, timeframe: str, candles: List) -> None:
        """Update candle buffer safely with memory management."""
        async with self.candles_lock:
            buffer_key = (symbol, timeframe)
            
            # Initialize buffer if needed
            if buffer_key not in self._candle_buffers:
                self._candle_buffers[buffer_key] = deque(maxlen=self.buffer_config.max_size)
                self._buffer_updates[buffer_key] = 0
            
            buffer = self._candle_buffers[buffer_key]
            
            # Add new candles
            for candle in candles:
                buffer.append(candle)
            
            # Update counter
            self._buffer_updates[buffer_key] += len(candles)
            
            # Check if cleanup is needed
            if self._buffer_updates[buffer_key] >= self.buffer_config.cleanup_interval:
                await self._cleanup_buffer(buffer_key)
    
    async def _cleanup_buffer(self, buffer_key: Tuple[str, str]) -> None:
        """Clean up buffer to save memory."""
        try:
            buffer = self._candle_buffers.get(buffer_key)
            if not buffer or len(buffer) < self.buffer_config.cleanup_threshold:
                return
            
            # Calculate memory savings
            original_size = len(buffer)
            
            # Keep only the most recent candles
            target_size = self.buffer_config.max_size
            while len(buffer) > target_size:
                buffer.popleft()
            
            # Update statistics
            candles_removed = original_size - len(buffer)
            estimated_memory_saved = candles_removed * 6 * 8 / (1024 * 1024)  # 6 floats * 8 bytes each
            self.stats['buffer_cleanups'] += 1
            self.stats['memory_saved_mb'] += estimated_memory_saved
            
            # Reset counter
            self._buffer_updates[buffer_key] = 0
            
            symbol, timeframe = buffer_key
            log.debug(f"Cleaned buffer for {symbol} {timeframe}: removed {candles_removed} candles, "
                     f"saved {estimated_memory_saved:.2f}MB")
            
        except Exception as e:
            log.error(f"Buffer cleanup failed for {buffer_key}: {e}")
    
    async def get_cooldowns(self) -> Dict[str, float]:
        """Get cooldowns safely."""
        async with self.cooldowns_lock:
            return self._cooldowns.copy()
    
    async def update_cooldown(self, symbol: str, expiry_time: float) -> None:
        """Update cooldown safely."""
        async with self.cooldowns_lock:
            self._cooldowns[symbol] = expiry_time
    
    async def remove_expired_cooldowns(self) -> int:
        """Remove expired cooldowns and return count removed."""
        async with self.cooldowns_lock:
            current_time = time.time()
            expired_keys = [k for k, v in self._cooldowns.items() if current_time >= v]
            
            for key in expired_keys:
                del self._cooldowns[key]
            
            return len(expired_keys)
    
    async def get_last_signals(self) -> Dict[str, Tuple[str, float, float]]:
        """Get last signals safely."""
        async with self.signals_lock:
            return self._last_signals.copy()
    
    async def update_last_signal(self, symbol: str, direction: str, score: float, timestamp: float) -> None:
        """Update last signal safely."""
        async with self.signals_lock:
            self._last_signals[symbol] = (direction, score, timestamp)
    
    async def cleanup_all_buffers(self) -> None:
        """Force cleanup of all buffers."""
        async with self.candles_lock:
            for buffer_key in list(self._candle_buffers.keys()):
                await self._cleanup_buffer(buffer_key)
    
    async def get_memory_usage(self) -> Dict[str, Any]:
        """Get memory usage statistics."""
        async with self.candles_lock:
            total_candles = sum(len(buffer) for buffer in self._candle_buffers.values())
            buffer_count = len(self._candle_buffers)
        
        return {
            'total_candles': total_candles,
            'buffer_count': buffer_count,
            'avg_candles_per_buffer': total_candles / max(buffer_count, 1),
            'estimated_memory_mb': total_candles * 6 * 8 / (1024 * 1024),
            'memory_saved_mb': self.stats['memory_saved_mb'],
            'buffer_cleanups': self.stats['buffer_cleanups']
        }
    
    async def get_lock_statistics(self) -> Dict[str, int]:
        """Get lock contention statistics."""
        return {
            'lock_contentions': self.stats['lock_contentions'],
            'positions_locked': len(self._positions),
            'active_cooldowns': len(self._cooldowns),
            'last_signals_count': len(self._last_signals)
        }
    
    def _check_lock_contention(self, lock: asyncio.Lock) -> None:
        """Check and record lock contention."""
        if lock.locked():
            self.stats['lock_contentions'] += 1
            log.debug("Lock contention detected")
    
    async def atomic_position_update(self, symbol: str, update_func: callable) -> Optional[Dict]:
        """
        Perform atomic update on position data.
        
        Args:
            symbol: Position symbol
            update_func: Function that takes current position data and returns updated data
            
        Returns:
            Updated position data or None if position doesn't exist
        """
        async with self.positions_lock:
            if symbol not in self._positions:
                return None
            
            current_data = self._positions[symbol]
            updated_data = update_func(current_data.copy())
            
            if updated_data:
                self._positions[symbol] = updated_data
                return updated_data
            else:
                # If update_func returns None, remove the position
                del self._positions[symbol]
                return None
    
    async def atomic_buffer_operation(self, symbol: str, timeframe: str, operation: callable) -> Any:
        """
        Perform atomic operation on candle buffer.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            operation: Function that takes buffer and returns result
            
        Returns:
            Result of the operation
        """
        async with self.candles_lock:
            buffer_key = (symbol, timeframe)
            buffer = self._candle_buffers.get(buffer_key, deque(maxlen=self.buffer_config.max_size))
            return operation(buffer)
    
    async def get_state_snapshot(self) -> Dict[str, Any]:
        """Get complete state snapshot for backup."""
        async with self.positions_lock:
            async with self.candles_lock:
                async with self.cooldowns_lock:
                    async with self.signals_lock:
                        return {
                            'positions': self._positions.copy(),
                            'candle_buffers': {
                                f"{k[0]}_{k[1]}": list(v) 
                                for k, v in self._candle_buffers.items()
                            },
                            'cooldowns': self._cooldowns.copy(),
                            'last_signals': self._last_signals.copy(),
                            'stats': self.stats.copy(),
                            'timestamp': time.time()
                        }
    
    async def restore_state_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Restore state from snapshot."""
        async with self.positions_lock:
            async with self.candles_lock:
                async with self.cooldowns_lock:
                    async with self.signals_lock:
                        # Restore positions
                        self._positions = snapshot.get('positions', {})
                        
                        # Restore candle buffers
                        buffers_data = snapshot.get('candle_buffers', {})
                        self._candle_buffers.clear()
                        for key_str, buffer_list in buffers_data.items():
                            # Parse key back to tuple
                            parts = key_str.rsplit('_', 1)
                            if len(parts) == 2:
                                symbol, timeframe = parts
                                buffer_key = (symbol, timeframe)
                                self._candle_buffers[buffer_key] = deque(
                                    buffer_list, maxlen=self.buffer_config.max_size
                                )
                        
                        # Restore cooldowns
                        self._cooldowns = snapshot.get('cooldowns', {})
                        
                        # Restore last signals
                        self._last_signals = snapshot.get('last_signals', {})
                        
                        # Restore stats
                        self.stats.update(snapshot.get('stats', {}))
                        
                        log.info("State restored from snapshot successfully")

# Global state manager instance
state_manager = ThreadSafeStateManager()
