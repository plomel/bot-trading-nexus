"""
Bot Trading Nexus - Safe Shutdown Manager
Handles graceful shutdown sequence including position closing and state preservation.
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional, Dict, Any, List
from enum import Enum

log = logging.getLogger("nexus.shutdown")

class ShutdownState(Enum):
    """Shutdown process states."""
    IDLE = "idle"
    STOPPING_ENTRIES = "stopping_entries"
    CLOSING_POSITIONS = "closing_positions"
    CANCELLING_ORDERS = "cancelling_orders"
    SAVING_STATE = "saving_state"
    COMPLETED = "completed"
    FAILED = "failed"

class ShutdownManager:
    """Manages safe shutdown sequence for Nexus trading bot."""
    
    def __init__(self, exchange, positions: Dict, telegram_handler=None):
        """
        Initialize shutdown manager.
        
        Args:
            exchange: CCXT exchange instance
            positions: Current positions dictionary
            telegram_handler: Telegram handler for notifications
        """
        self.exchange = exchange
        self.positions = positions
        self.telegram_handler = telegram_handler
        self.shutdown_state = ShutdownState.IDLE
        self.shutdown_start_time = None
        self.shutdown_timeout = 120  # 2 minutes max shutdown time
        self.shutdown_task = None
        
        # Shutdown statistics
        self.stats = {
            'total_positions': 0,
            'closed_positions': 0,
            'cancelled_orders': 0,
            'errors': [],
            'warnings': []
        }
    
    async def initiate_shutdown(self) -> bool:
        """
        Initiate the shutdown sequence.
        
        Returns:
            True if shutdown initiated successfully, False otherwise
        """
        if self.shutdown_state != ShutdownState.IDLE:
            log.warning(f"Shutdown already in progress: {self.shutdown_state}")
            return False
        
        self.shutdown_start_time = time.time()
        self.shutdown_state = ShutdownState.STOPPING_ENTRIES
        self.stats['total_positions'] = len(self.positions)
        
        log.info("=== NEXUS SHUTDOWN SEQUENCE INITIATED ===")
        
        # Start shutdown in background task
        self.shutdown_task = asyncio.create_task(self._execute_shutdown())
        
        return True
    
    async def _execute_shutdown(self) -> None:
        """Execute the complete shutdown sequence."""
        try:
            # Step 1: Stop new entries (already handled by setting state)
            await self._step_stop_entries()
            
            # Step 2: Close all positions
            await self._step_close_positions()
            
            # Step 3: Cancel all pending orders
            await self._step_cancel_orders()
            
            # Step 4: Save final state
            await self._step_save_state()
            
            # Step 5: Mark as completed
            self.shutdown_state = ShutdownState.COMPLETED
            log.info("=== NEXUS SHUTDOWN COMPLETED SUCCESSFULLY ===")
            
            # Send completion notification
            if self.telegram_handler:
                await self.telegram_handler.send_shutdown_complete(True)
                
        except Exception as e:
            self.shutdown_state = ShutdownState.FAILED
            error_msg = f"Shutdown failed: {str(e)}"
            log.error(error_msg, exc_info=True)
            self.stats['errors'].append(error_msg)
            
            # Send failure notification
            if self.telegram_handler:
                await self.telegram_handler.send_shutdown_complete(False, error_msg)
    
    async def _step_stop_entries(self) -> None:
        """Step 1: Stop new position entries."""
        log.info("Step 1: Stopping new position entries...")
        
        # This is handled by the main bot checking shutdown state
        # Just log and update state
        self.shutdown_state = ShutdownState.CLOSING_POSITIONS
        
        log.info("✓ New entries stopped")
    
    async def _step_close_positions(self) -> None:
        """Step 2: Close all open positions."""
        log.info("Step 2: Closing all open positions...")
        
        if not self.positions:
            log.info("No positions to close")
            self.shutdown_state = ShutdownState.CANCELLING_ORDERS
            return
        
        # Get current positions from exchange
        try:
            live_positions = await self.exchange.fetch_positions()
            active_positions = [p for p in live_positions if float(p.get("contracts") or 0) > 0]
            
        except Exception as e:
            log.error(f"Failed to fetch positions: {e}")
            self.stats['errors'].append(f"Failed to fetch positions: {e}")
            # Continue with local positions
            
            active_positions = []
        
        # Close each position
        close_tasks = []
        for symbol, pos_data in self.positions.items():
            close_tasks.append(self._close_position(symbol, pos_data, active_positions))
        
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        
        self.shutdown_state = ShutdownState.CANCELLING_ORDERS
        log.info(f"✓ Positions closed: {self.stats['closed_positions']}/{self.stats['total_positions']}")
    
    async def _close_position(self, symbol: str, pos_data: Dict, live_positions: List[Dict]) -> None:
        """
        Close a single position.
        
        Args:
            symbol: Trading symbol
            pos_data: Position data from local state
            live_positions: Live positions from exchange
        """
        try:
            # Check if position exists in exchange
            live_pos = next((p for p in live_positions if p["symbol"] == symbol), None)
            
            if not live_pos:
                log.info(f"Position {symbol} not found in exchange (already closed)")
                self.stats['closed_positions'] += 1
                return
            
            # Determine close side
            side = pos_data.get('side', 'LONG')
            close_side = "sell" if side == "LONG" else "buy"
            qty = pos_data.get('qty', 0)
            
            log.info(f"Closing position {symbol}: {side} {qty} @ market")
            
            # Create market close order
            order = await self.exchange.create_order(
                symbol, "market", close_side, qty,
                params={"reduceOnly": True}
            )
            
            log.info(f"Position {symbol} closed successfully: {order.get('id', 'unknown')}")
            self.stats['closed_positions'] += 1
            
        except Exception as e:
            error_msg = f"Failed to close position {symbol}: {e}"
            log.error(error_msg)
            self.stats['errors'].append(error_msg)
            
            # Try to cancel any remaining orders for this symbol
            try:
                await self._cancel_symbol_orders(symbol)
            except Exception as cancel_error:
                log.error(f"Failed to cancel orders for {symbol}: {cancel_error}")
    
    async def _step_cancel_orders(self) -> None:
        """Step 3: Cancel all pending orders."""
        log.info("Step 3: Cancelling all pending orders...")
        
        try:
            # Get all open orders
            open_orders = await self.exchange.fetch_open_orders()
            
            if not open_orders:
                log.info("No pending orders to cancel")
                self.shutdown_state = ShutdownState.SAVING_STATE
                return
            
            # Group orders by symbol
            orders_by_symbol = {}
            for order in open_orders:
                symbol = order['symbol']
                if symbol not in orders_by_symbol:
                    orders_by_symbol[symbol] = []
                orders_by_symbol[symbol].append(order)
            
            # Cancel orders by symbol
            cancel_tasks = []
            for symbol, orders in orders_by_symbol.items():
                cancel_tasks.append(self._cancel_symbol_orders(symbol, orders))
            
            if cancel_tasks:
                await asyncio.gather(*cancel_tasks, return_exceptions=True)
            
            log.info(f"✓ Orders cancelled: {self.stats['cancelled_orders']}")
            
        except Exception as e:
            error_msg = f"Failed to cancel orders: {e}"
            log.error(error_msg)
            self.stats['errors'].append(error_msg)
        
        self.shutdown_state = ShutdownState.SAVING_STATE
    
    async def _cancel_symbol_orders(self, symbol: str, orders: Optional[List[Dict]] = None) -> None:
        """
        Cancel all orders for a specific symbol.
        
        Args:
            symbol: Trading symbol
            orders: Optional list of orders (if None, will fetch from exchange)
        """
        try:
            if orders is None:
                orders = await self.exchange.fetch_open_orders(symbol)
            
            if not orders:
                return
            
            # Cancel all orders for this symbol
            for order in orders:
                try:
                    await self.exchange.cancel_order(order['id'], symbol)
                    self.stats['cancelled_orders'] += 1
                    log.debug(f"Cancelled order {order['id']} for {symbol}")
                except Exception as e:
                    # Order might already be filled/cancelled
                    log.debug(f"Failed to cancel order {order['id']} for {symbol}: {e}")
            
        except Exception as e:
            log.error(f"Failed to cancel orders for {symbol}: {e}")
    
    async def _step_save_state(self) -> None:
        """Step 4: Save final state."""
        log.info("Step 4: Saving final state...")
        
        try:
            # Import state module
            import state as st
            
            # Save empty positions (all should be closed)
            st.save_positions({})
            
            # Save shutdown statistics
            shutdown_data = {
                'shutdown_time': time.time(),
                'shutdown_state': self.shutdown_state.value,
                'stats': self.stats,
                'duration': time.time() - self.shutdown_start_time if self.shutdown_start_time else 0
            }
            
            # Save to history
            st.append_history({
                'type': 'shutdown',
                'data': shutdown_data
            })
            
            log.info("✓ Final state saved successfully")
            
        except Exception as e:
            error_msg = f"Failed to save state: {e}"
            log.error(error_msg)
            self.stats['errors'].append(error_msg)
    
    def is_shutdown_complete(self) -> bool:
        """Check if shutdown is complete."""
        return self.shutdown_state in [ShutdownState.COMPLETED, ShutdownState.FAILED]
    
    def is_shutdown_in_progress(self) -> bool:
        """Check if shutdown is in progress."""
        return self.shutdown_state != ShutdownState.IDLE
    
    def get_shutdown_status(self) -> Dict[str, Any]:
        """Get current shutdown status."""
        duration = None
        if self.shutdown_start_time:
            duration = time.time() - self.shutdown_start_time
        
        return {
            'state': self.shutdown_state.value,
            'duration': duration,
            'timeout': self.shutdown_timeout,
            'stats': self.stats.copy(),
            'is_complete': self.is_shutdown_complete(),
            'is_in_progress': self.is_shutdown_in_progress()
        }
    
    async def wait_for_shutdown(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for shutdown to complete.
        
        Args:
            timeout: Maximum time to wait (seconds)
            
        Returns:
            True if shutdown completed successfully, False if timed out or failed
        """
        if timeout is None:
            timeout = self.shutdown_timeout
        
        start_time = time.time()
        
        while not self.is_shutdown_complete():
            if time.time() - start_time > timeout:
                log.warning(f"Shutdown timeout after {timeout}s")
                return False
            
            await asyncio.sleep(1)
        
        return self.shutdown_state == ShutdownState.COMPLETED
    
    def force_shutdown(self) -> None:
        """Force immediate shutdown (emergency use only)."""
        log.warning("=== FORCED SHUTDOWN INITIATED ===")
        self.shutdown_state = ShutdownState.FAILED
        
        if self.shutdown_task and not self.shutdown_task.done():
            self.shutdown_task.cancel()
        
        # Save emergency state
        try:
            import state as st
            st.save_positions({})
            st.append_history({
                'type': 'emergency_shutdown',
                'timestamp': time.time(),
                'reason': 'forced_shutdown'
            })
        except Exception as e:
            log.error(f"Failed to save emergency state: {e}")
    
    def add_warning(self, warning: str) -> None:
        """Add a warning to the shutdown log."""
        self.stats['warnings'].append(warning)
        log.warning(f"Shutdown warning: {warning}")
    
    def add_error(self, error: str) -> None:
        """Add an error to the shutdown log."""
        self.stats['errors'].append(error)
        log.error(f"Shutdown error: {error}")
