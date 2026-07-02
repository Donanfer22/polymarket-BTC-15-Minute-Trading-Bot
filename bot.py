import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import math
from decimal import Decimal
import time
from dataclasses import dataclass
from typing import List, Optional, Dict
import random

# Add project to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


try:
    from patch_gamma_markets import apply_gamma_markets_patch, verify_patch
    patch_applied = apply_gamma_markets_patch()
    if patch_applied:
        verify_patch()
    else:
        print("ERROR: Failed to apply gamma_market patch")
        sys.exit(1)
except ImportError as e:
    print(f"ERROR: Could not import patch module: {e}")
    print("Make sure patch_gamma_markets.py is in the same directory")
    sys.exit(1)

# Now import Nautilus
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.adapters.polymarket import POLYMARKET
from nautilus_trader.adapters.polymarket import (
    PolymarketDataClientConfig,
    PolymarketExecClientConfig,
)
from nautilus_trader.adapters.polymarket.factories import (
    PolymarketLiveDataClientFactory,
    PolymarketLiveExecClientFactory,
)
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.identifiers import InstrumentId, ClientOrderId
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.data import QuoteTick

from dotenv import load_dotenv
from loguru import logger
import redis

# Import our phases
from core.strategy_brain.signal_processors.spike_detector import SpikeDetectionProcessor
from core.strategy_brain.signal_processors.sentiment_processor import SentimentProcessor
from core.strategy_brain.signal_processors.divergence_processor import PriceDivergenceProcessor
from core.strategy_brain.signal_processors.orderbook_processor import OrderBookImbalanceProcessor
from core.strategy_brain.signal_processors.tick_velocity_processor import TickVelocityProcessor
from core.strategy_brain.signal_processors.deribit_pcr_processor import DeribitPCRProcessor
from core.strategy_brain.fusion_engine.signal_fusion import get_fusion_engine
from execution.risk_engine import get_risk_engine
from monitoring.performance_tracker import get_performance_tracker
from monitoring.grafana_exporter import get_grafana_exporter
from feedback.learning_engine import get_learning_engine
load_dotenv()
# from patch_market_orders import apply_market_order_patch
# apply_market_order_patch()
# patch_applied = apply_market_order_patch()


# =============================================================================
# CONSTANTS
# =============================================================================
QUOTE_STABILITY_REQUIRED = 3      # Need only 3 valid ticks to be stable (faster startup)
QUOTE_MIN_SPREAD = 0.001          # Both bid AND ask must be at least this
MARKET_INTERVAL_SECONDS = 900     # 15-minute markets


@dataclass
class PaperTrade:
    """Track trades for dashboard display"""
    timestamp: datetime
    direction: str
    size_usd: float
    price: float
    signal_score: float
    signal_confidence: float
    outcome: str = "PENDING"
    trade_type: str = "SIM"
    token_id: str = ""      # token comprado — usado p/ ler o resultado real em LIVE
    market_id: str = ""
    pnl_usd: float = 0.0     # PnL realizado (preenchido na resolucao)

    def to_dict(self):
        return {
            'timestamp': self.timestamp.isoformat(),
            'direction': self.direction,
            'size_usd': self.size_usd,
            'price': self.price,
            'signal_score': self.signal_score,
            'signal_confidence': self.signal_confidence,
            'outcome': self.outcome,
            'trade_type': getattr(self, 'trade_type', 'SIM'),
            'token_id': getattr(self, 'token_id', ''),
            'market_id': getattr(self, 'market_id', ''),
            'pnl_usd': getattr(self, 'pnl_usd', 0.0),
        }


def init_redis():
    """Initialize Redis connection for simulation mode control."""
    try:
        redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            db=int(os.getenv('REDIS_DB', 2)),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True
        )
        redis_client.ping()
        logger.info("Redis connection established")
        return redis_client
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")
        logger.warning("Simulation mode will be static (from .env)")
        return None


class IntegratedBTCStrategy(Strategy):
    """
    Integrated BTC Strategy - FIXED VERSION
    - Subscribes immediately at startup
    - Forces stability for first trade
    - Correct timing for market switching
    """

    def __init__(self, redis_client=None, enable_grafana=True, test_mode=False, simulation=False):
        super().__init__()
        
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

        self.bot_start_time = datetime.now(timezone.utc)
        self.restart_after_minutes = 90

        # Nautilus
        self.instrument_id = None
        self.redis_client = redis_client
        self.current_simulation_mode = simulation

        # Store ALL BTC instruments
        self.all_btc_instruments: List[Dict] = []
        self.current_instrument_index: int = -1
        self.next_switch_time: Optional[datetime] = None

        # Quote-stability tracking
        self._stable_tick_count = 0
        self._market_stable = False
        self._last_instrument_switch = None
        
        # =========================================================================
        # FIX 1: Force first trade by setting last_trade_time to -1
        # =========================================================================
        self.last_trade_time = -1  # Force first trade immediately!
        self._waiting_for_market_open = False  # True when waiting for a future market to open
        self._last_bid_ask = None  # (bid_decimal, ask_decimal) from last tick, for liquidity checks
        self._last_heartbeat_time = 0  # To print periodic logs and avoid dead silence
        self._last_logged_price = 0.0  # Track significant price changes for live feed

        # Tick buffer: rolling 90s of ticks for TickVelocityProcessor
        from collections import deque
        self._tick_buffer: deque = deque(maxlen=500)  # ~500 ticks = well over 90s

        # YES token id for the current market (set in _load_all_btc_instruments)
        self._yes_token_id: Optional[str] = None

        # Phase 4: Signal Processors
        self.spike_detector = SpikeDetectionProcessor(
            spike_threshold=0.05,       # FIXED: was 0.15 (too high for probabilities)
            lookback_periods=20,
        )
        self.sentiment_processor = SentimentProcessor(
            extreme_fear_threshold=25,
            extreme_greed_threshold=75,
        )
        self.divergence_processor = PriceDivergenceProcessor(
            divergence_threshold=0.05,
        )
        self.orderbook_processor = OrderBookImbalanceProcessor(
            imbalance_threshold=0.30,   # 30% skew to signal
            min_book_volume=50.0,       # ignore illiquid books
        )
        self.tick_velocity_processor = TickVelocityProcessor(
            velocity_threshold_60s=0.015,  # 1.5% move in 60s
            velocity_threshold_30s=0.010,  # 1.0% move in 30s
        )
        self.deribit_pcr_processor = DeribitPCRProcessor(
            bullish_pcr_threshold=1.20,
            bearish_pcr_threshold=0.70,
            max_days_to_expiry=2,
            cache_seconds=300,          # refresh every 5 min
        )

        # Phase 4: Signal Fusion — update weights for 6 processors
        self.fusion_engine = get_fusion_engine()
        # Rebalanced weights (must sum ≤ 1.0; higher = more influence)
        self.fusion_engine.set_weight("OrderBookImbalance", 0.30)  # best real-time signal
        self.fusion_engine.set_weight("TickVelocity",       0.25)  # fast poly momentum
        self.fusion_engine.set_weight("PriceDivergence",    0.18)  # spot momentum
        self.fusion_engine.set_weight("SpikeDetection",     0.12)  # mean reversion
        self.fusion_engine.set_weight("DeribitPCR",         0.10)  # institutional sentiment
        self.fusion_engine.set_weight("SentimentAnalysis",  0.05)  # daily F&G (weak)

        # Phase 5: Risk Management
        # Initialize Risk Engine with much higher max limits
        # so it doesn't block the dynamic Trade Size from the Dashboard
        self.risk_engine = get_risk_engine()
        self.risk_engine.limits.max_position_size = Decimal("100000.0")
        self.risk_engine.limits.max_total_exposure = Decimal("1000000.0")

        # Phase 6: Performance Tracking
        self.performance_tracker = get_performance_tracker()

        # Phase 7: Learning Engine
        self.learning_engine = get_learning_engine()

        # Phase 6: Grafana (optional)
        if enable_grafana:
            self.grafana_exporter = get_grafana_exporter()
        else:
            self.grafana_exporter = None

        # Price history
        self.price_history = []
        self.max_history = 100

        # Paper trading tracker
        self.paper_trades: List[PaperTrade] = []
        self._load_paper_trades()

        self.test_mode = test_mode

        if test_mode:
            logger.info("=" * 80)
            logger.info("  TEST MODE ACTIVE - Trading every minute!")
            logger.info("=" * 80)

        logger.info("=" * 80)
        logger.info("INTEGRATED BTC STRATEGY INITIALIZED - FIXED VERSION")
        logger.info("  Phase 4: Signal processors ready")
        logger.info("  Phase 5: Risk engine ready")
        logger.info("  Phase 6: Performance tracking ready")
        logger.info("  Phase 7: Learning engine ready")
        logger.info("  $1 per trade maximum")
        logger.info("=" * 80)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _seconds_to_next_15min_boundary(self) -> float:
        """Return seconds until the next 15-minute UTC boundary."""
        now_ts = datetime.now(timezone.utc).timestamp()
        next_boundary = (math.floor(now_ts / MARKET_INTERVAL_SECONDS) + 1) * MARKET_INTERVAL_SECONDS
        return next_boundary - now_ts

    def _is_quote_valid(self, bid, ask) -> bool:
        """Return True only when BOTH bid and ask are present and make sense."""
        if bid is None or ask is None:
            return False
        try:
            b = float(bid)
            a = float(ask)
        except (TypeError, ValueError):
            return False
        if b < QUOTE_MIN_SPREAD or a < QUOTE_MIN_SPREAD:
            return False
        if b > 0.999 or a > 0.999:
            return False
        return True

    def _reset_stability(self, reason: str = ""):
        """Mark the market as unstable and reset the counter."""
        if self._market_stable:
            logger.warning(f"Market stability RESET{' – ' + reason if reason else ''}")
        self._market_stable = False
        self._stable_tick_count = 0

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------

    async def check_simulation_mode(self) -> bool:
        """Check Redis or fallback file for current simulation mode."""
        sim_mode = None
        
        if self.redis_client:
            try:
                sim_mode = self.redis_client.get('btc_trading:simulation_mode')
            except Exception as e:
                logger.warning(f"Failed to check Redis simulation mode: {e}")
        else:
            # Fallback to mode.txt for localhost testing
            try:
                import os
                mode_file = os.path.join(os.path.dirname(__file__), "mode.txt")
                if os.path.exists(mode_file):
                    with open(mode_file, "r", encoding="utf-8") as f:
                        sim_mode = f.read().strip()
            except Exception as e:
                pass
                
        if sim_mode is not None:
            if sim_mode == '-1':
                if not getattr(self, 'redis_paused', False):
                    logger.warning("Bot is STOPPED via Dashboard")
                self.redis_paused = True
                return getattr(self, 'current_simulation_mode', True)
            
            self.redis_paused = False
            redis_simulation = (sim_mode == '1')
            if getattr(self, 'current_simulation_mode', None) != redis_simulation:
                self.current_simulation_mode = redis_simulation
                mode_text = "SIMULATION" if redis_simulation else "LIVE TRADING"
                logger.warning(f"Trading mode changed to: {mode_text}")
                if not redis_simulation:
                    logger.warning("LIVE TRADING ACTIVE - Real money at risk!")
            return redis_simulation
            
        return getattr(self, 'current_simulation_mode', True)

    # ------------------------------------------------------------------
    # Strategy lifecycle
    # ------------------------------------------------------------------

    def on_start(self):
        """Called when strategy starts - LOAD ALL MARKETS AND SUBSCRIBE IMMEDIATELY"""
        logger.info("=" * 80)
        logger.info("INTEGRATED BTC STRATEGY STARTED - FIXED VERSION")
        logger.info("=" * 80)

        # =========================================================================
        # FIX 2: Load ALL BTC instruments at startup
        # =========================================================================
        self._load_all_btc_instruments()

        # =========================================================================
        # FIX 3: Force subscribe to current market IMMEDIATELY
        # =========================================================================
        if self.instrument_id:
            self.subscribe_quote_ticks(self.instrument_id)
            logger.info(f"✓ SUBSCRIBED to market: {self.instrument_id}")
            
            # Try to get current price from cache
            try:
                quote = self.cache.quote_tick(self.instrument_id)
                if quote and quote.bid_price and quote.ask_price:
                    current_price = (quote.bid_price + quote.ask_price) / 2
                    self.price_history.append(current_price)
                    logger.info(f"✓ Initial price: ${float(current_price):.4f}")
            except Exception as e:
                logger.debug(f"No initial price yet: {e}")

        # Generate synthetic history if needed
        if len(self.price_history) < 20:
            self._generate_synthetic_history(target_count=20, existing_count=len(self.price_history))

        # =========================================================================
        # FIX 4: Start the timer loop (but don't rely on it for trading)
        # =========================================================================
        self.run_in_executor(self._start_timer_loop)

        if self.grafana_exporter:
            import threading
            threading.Thread(target=self._start_grafana_sync, daemon=True).start()

        logger.info("=" * 80)
        logger.info("Strategy active - will trade every 15 minutes")
        logger.info(f"Price history: {len(self.price_history)} points")
        if len(self.price_history) >= 20:
            logger.info("✓ READY TO TRADE NOW!")
        else:
            logger.warning(f"⚠ Need more history ({len(self.price_history)}/20)")
        logger.info("=" * 80)

    def _generate_synthetic_history(self, target_count: int = 20, existing_count: int = 0):
        """Generate synthetic price history for testing"""
        if self.price_history:
            base_price = self.price_history[-1]
        else:
            base_price = Decimal("0.5")
        needed = target_count - existing_count
        if needed <= 0:
            return
        for _ in range(needed):
            change = Decimal(str(random.uniform(-0.03, 0.03)))
            new_price = base_price * (Decimal("1.0") + change)
            new_price = max(Decimal("0.01"), min(Decimal("0.99"), new_price))
            self.price_history.append(new_price)
            base_price = new_price

    # ------------------------------------------------------------------
    # Load all BTC instruments at once
    # ------------------------------------------------------------------

    def _load_all_btc_instruments(self):
        """Load ALL BTC instruments from cache and sort by start time"""
        instruments = self.cache.instruments()
        logger.info(f"Loading ALL BTC instruments from {len(instruments)} total...")
        
        now = datetime.now(timezone.utc)
        current_timestamp = int(now.timestamp())
        
        btc_instruments = []
        
        for instrument in instruments:
            try:
                if hasattr(instrument, 'info') and instrument.info:
                    question = instrument.info.get('question', '').lower()
                    slug = instrument.info.get('market_slug', '').lower()
                    
                    if ('btc' in question or 'btc' in slug) and '15m' in slug:
                        try:
                            timestamp_part = slug.split('-')[-1]
                            market_timestamp = int(timestamp_part)
                            
                            # The slug timestamp IS the market start time (Unix, no offset).
                            # end_date_iso is a DATE-only string (e.g. "2026-02-20"), NOT a datetime,
                            # so parsing it gives midnight UTC which is wrong for intraday markets.
                            # Always derive end_timestamp from the slug: start + 900s.
                            real_start_ts = market_timestamp
                            end_timestamp = market_timestamp + 900  # 15-min markets always
                            time_diff = real_start_ts - current_timestamp
                            
                            # Only include markets that haven't ended yet
                            if end_timestamp > current_timestamp:
                                # Extract YES token ID for CLOB order book API.
                                # Nautilus instrument ID format:
                                #   {condition_id}-{token_id}.POLYMARKET
                                # The CLOB /book endpoint only accepts the token_id
                                # (the part after the dash, before .POLYMARKET).
                                raw_id = str(instrument.id)
                                # Strip .POLYMARKET suffix first
                                without_suffix = raw_id.split('.')[0] if '.' in raw_id else raw_id
                                # Then take the token_id after the condition_id dash
                                yes_token_id = without_suffix.split('-')[-1] if '-' in without_suffix else without_suffix

                                btc_instruments.append({
                                    'instrument': instrument,
                                    'slug': slug,
                                    'start_time': datetime.fromtimestamp(real_start_ts, tz=timezone.utc),
                                    'end_time': datetime.fromtimestamp(end_timestamp, tz=timezone.utc),
                                    'market_timestamp': market_timestamp,
                                    'end_timestamp': end_timestamp,
                                    'time_diff_minutes': time_diff / 60,
                                    'yes_token_id': yes_token_id,
                                })
                        except (ValueError, IndexError):
                            continue
            except Exception:
                continue
        
        # Pair YES and NO tokens by slug.
        # Each Polymarket market has two tokens loaded as separate Nautilus instruments.
        # The first instrument found for a slug is stored as the primary (YES/UP).
        # The second instrument found for the same slug is the NO/DOWN token.
        seen_slugs = {}
        deduped = []
        for inst in btc_instruments:
            slug = inst['slug']
            if slug not in seen_slugs:
                # First token seen = YES (UP)
                inst['yes_instrument_id'] = inst['instrument'].id
                inst['no_instrument_id'] = None  # will be filled when second token found
                seen_slugs[slug] = inst
                deduped.append(inst)
            else:
                # Second token seen = NO (DOWN) — store it on the existing entry
                seen_slugs[slug]['no_instrument_id'] = inst['instrument'].id
        btc_instruments = deduped
        
        # Sort by start time (absolute timestamp, not time-of-day)
        btc_instruments.sort(key=lambda x: x['market_timestamp'])
        
        logger.info("=" * 80)
        logger.info(f"FOUND {len(btc_instruments)} BTC 15-MIN MARKETS:")
        for i, inst in enumerate(btc_instruments):
            # A market is ACTIVE if it has started AND not yet ended
            is_active = inst['time_diff_minutes'] <= 0 and inst['end_timestamp'] > current_timestamp
            status = "ACTIVE" if is_active else "FUTURE" if inst['time_diff_minutes'] > 0 else "PAST"
            logger.info(f"  [{i}] {inst['slug']}: {status} (starts at {inst['start_time'].strftime('%H:%M:%S')}, ends at {inst['end_time'].strftime('%H:%M:%S')})")
        logger.info("=" * 80)
        
        self.all_btc_instruments = btc_instruments
        
        # Find current market and SUBSCRIBE IMMEDIATELY
        # FIXED: A market is current if it has STARTED and not yet ENDED (use end_time, not a hardcoded 15-min window)
        for i, inst in enumerate(btc_instruments):
            is_active = inst['time_diff_minutes'] <= 0 and inst['end_timestamp'] > current_timestamp
            if is_active:
                self.current_instrument_index = i
                self.instrument_id = inst['instrument'].id
                self.next_switch_time = inst['end_time']
                self._yes_token_id = inst.get('yes_token_id')
                self._yes_instrument_id = inst.get('yes_instrument_id', inst['instrument'].id)
                self._no_instrument_id = inst.get('no_instrument_id')
                logger.info(f"✓ CURRENT MARKET: {inst['slug']} (index {i})")
                logger.info(f"  Next switch at: {self.next_switch_time.strftime('%H:%M:%S')}")
                logger.info(f"  YES token: {self._yes_token_id[:16]}…" if self._yes_token_id else "  YES token: unknown")
                
                # =========================================================================
                # CRITICAL FIX: Subscribe immediately!
                # =========================================================================
                self.subscribe_quote_ticks(self.instrument_id)
                logger.info(f"  ✓ SUBSCRIBED to current market")
                break
        
        if self.current_instrument_index == -1 and btc_instruments:
            # No currently-active market — find the NEAREST upcoming one
            # (smallest positive time_diff_minutes = starts soonest)
            future_markets = [inst for inst in btc_instruments if inst['time_diff_minutes'] > 0]
            if future_markets:
                nearest = min(future_markets, key=lambda x: x['time_diff_minutes'])
                nearest_idx = btc_instruments.index(nearest)
            else:
                # All markets are in the past — use the last one
                nearest = btc_instruments[-1]
                nearest_idx = len(btc_instruments) - 1

            self.current_instrument_index = nearest_idx
            inst = nearest
            self.instrument_id = inst['instrument'].id
            self._yes_token_id = inst.get('yes_token_id')
            self._yes_instrument_id = inst.get('yes_instrument_id', inst['instrument'].id)
            self._no_instrument_id = inst.get('no_instrument_id')
            self.next_switch_time = inst['start_time']  # switch_time = when it OPENS
            logger.info(f"⚠ NO CURRENT MARKET - WAITING FOR NEAREST FUTURE: {inst['slug']}")
            logger.info(f"  Starts in {inst['time_diff_minutes']:.1f} min at {self.next_switch_time.strftime('%H:%M:%S')} UTC")

            # Subscribe so we get ticks when it opens
            self.subscribe_quote_ticks(self.instrument_id)
            logger.info(f"  ✓ SUBSCRIBED to future market")
            # Block trading until the market actually opens (timer loop sets _market_open flag)
            self._waiting_for_market_open = True
            
    def _switch_to_next_market(self):
        """Switch to the next market in the pre-loaded list"""
        if not self.all_btc_instruments:
            logger.error("No instruments loaded!")
            return False
        
        next_index = self.current_instrument_index + 1
        if next_index >= len(self.all_btc_instruments):
            logger.warning("No more markets available - will restart bot")
            return False
        
        next_market = self.all_btc_instruments[next_index]
        now = datetime.now(timezone.utc)
        
        # Check if next market is ready
        if now < next_market['start_time']:
            logger.info(f"Waiting for next market at {next_market['start_time'].strftime('%H:%M:%S')}")
            return False
        
        # Switch to next market
        self.current_instrument_index = next_index
        self.instrument_id = next_market['instrument'].id
        self.next_switch_time = next_market['end_time']
        self._yes_token_id = next_market.get('yes_token_id')
        self._yes_instrument_id = next_market.get('yes_instrument_id', next_market['instrument'].id)
        self._no_instrument_id = next_market.get('no_instrument_id')
        
        logger.info("=" * 80)
        logger.info(f"SWITCHING TO NEXT MARKET: {next_market['slug']}")
        logger.info(f"  Current time: {now.strftime('%H:%M:%S')} (UTC)")
        logger.info(f"  Market ends at: {self.next_switch_time.strftime('%H:%M:%S')} (UTC)")
        logger.info("=" * 80)
        
        # =========================================================================
        # FIX 5: Force stability for new market and reset trade timer correctly
        # =========================================================================
        self._stable_tick_count = QUOTE_STABILITY_REQUIRED  # Force stable immediately
        self._market_stable = True
        self._waiting_for_market_open = False  # Market is now active
        
        # Reset trade timer so we trade at the NEXT quote we receive
        # Use -1 so any interval will trigger (same as startup)
        self.last_trade_time = -1
        logger.info(f"  Trade timer reset — will trade on next tick")
        
        self.subscribe_quote_ticks(self.instrument_id)
        return True

    # ------------------------------------------------------------------
    # Timer loop - SIMPLIFIED
    # ------------------------------------------------------------------

    def _start_timer_loop(self):
        """Start timer loop in executor"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._timer_loop())
        finally:
            loop.close()

    async def _timer_loop(self):
        """
        Timer loop: checks every 10 seconds if it's time to switch markets.
        Also handles the case where we're waiting for a future market to open.
        """
        while True:
            await self.check_simulation_mode()

            # --- auto-restart check ---
            uptime_minutes = (datetime.now(timezone.utc) - self.bot_start_time).total_seconds() / 60
            if uptime_minutes >= self.restart_after_minutes:
                logger.warning("AUTO-RESTART TIME - Loading fresh filters")
                import signal as _signal
                os.kill(os.getpid(), _signal.SIGTERM)
                return

            now = datetime.now(timezone.utc)

            if self.next_switch_time and now >= self.next_switch_time:
                if self._waiting_for_market_open:
                    # The future market we were waiting for has now opened
                    # Treat it like a market switch so trade timer resets
                    logger.info("=" * 80)
                    logger.info(f"⏰ WAITING MARKET NOW OPEN: {now.strftime('%H:%M:%S')} UTC")
                    logger.info("=" * 80)
                    # Update next_switch_time to the market's END time
                    if (self.current_instrument_index >= 0 and
                            self.current_instrument_index < len(self.all_btc_instruments)):
                        current_market = self.all_btc_instruments[self.current_instrument_index]
                        self.next_switch_time = current_market['end_time']
                        logger.info(f"  Market ends at {self.next_switch_time.strftime('%H:%M:%S')} UTC")
                    self._waiting_for_market_open = False
                    self._market_stable = True
                    self._stable_tick_count = QUOTE_STABILITY_REQUIRED
                    self.last_trade_time = -1  # Trade immediately on next tick
                    logger.info("  ✓ MARKET OPEN — ready to trade on next tick")
                else:
                    # Normal market switch
                    self._switch_to_next_market()

            await asyncio.sleep(10)

    # ------------------------------------------------------------------
    # Quote tick handler - SIMPLIFIED
    # ------------------------------------------------------------------

    def on_quote_tick(self, tick: QuoteTick):
        """Handle quote tick - TRADE when market opens and at each 15-min boundary"""
        try:
            # Check for STOPPED mode without blocking
            if getattr(self, 'redis_paused', False):
                return

            # Only process ticks from current instrument
            if self.instrument_id is None or tick.instrument_id != self.instrument_id:
                return

            now = datetime.now(timezone.utc)
            bid = tick.bid_price
            ask = tick.ask_price

            if bid is None or ask is None:
                return
                
            try:
                bid_decimal = bid.as_decimal()
                ask_decimal = ask.as_decimal()
            except:
                return

            # Always store price history
            mid_price = (bid_decimal + ask_decimal) / 2
            self.price_history.append(mid_price)
            if len(self.price_history) > self.max_history:
                self.price_history.pop(0)
            
            # Store latest bid/ask for liquidity check before order placement
            self._last_bid_ask = (bid_decimal, ask_decimal)

            # Tick buffer for TickVelocityProcessor (rolling 90s window)
            self._tick_buffer.append({'ts': now, 'price': mid_price})

            # Stability gate
            if not self._market_stable:
                self._stable_tick_count += 1
                if self._stable_tick_count >= 1:
                    self._market_stable = True
                    logger.info(f"✓ Market STABLE immediately")
                else:
                    return

            # =========================================================================
            # FIXED TRADING LOGIC:
            # 
            # We trade once per 15-min market interval.
            # Instead of checking wall-clock 15-min boundaries (which caused the 2-hour
            # wait), we use a simple counter keyed to the Polymarket market's OWN
            # start time.
            #
            # The market's start_time is stored in all_btc_instruments[current_index].
            # Within each market, we compute a "sub-interval" index:
            #   sub_interval = elapsed_seconds_since_market_open // 900
            # Trade ID = (market_start_timestamp, sub_interval)
            # This fires once at market open AND once after every 15 min within
            # the same market if it's a multi-interval market.
            #
            # If _waiting_for_market_open is True (started before market opens),
            # we block trading until the timer loop calls _switch_to_next_market.
            # =========================================================================

            # Block trading if waiting for a future market to open
            if self._waiting_for_market_open:
                return

            # Get current market info
            if (self.current_instrument_index < 0 or
                    self.current_instrument_index >= len(self.all_btc_instruments)):
                return

            current_market = self.all_btc_instruments[self.current_instrument_index]
            market_start_ts = current_market['market_timestamp']  # Slug timestamp = market start (Unix)

            # How many 15-min intervals have elapsed since this market opened?
            elapsed_secs = now.timestamp() - market_start_ts
            if elapsed_secs < 0:
                # Market hasn't started yet — block
                return

            sub_interval = int(elapsed_secs // MARKET_INTERVAL_SECONDS)

            # Unique trade key: (market_start_timestamp, sub_interval)
            trade_key = (market_start_ts, sub_interval)

            # =========================================================================
            # TRADE WINDOW: minutes 13–14 of each 15-min market (780–840 seconds in)
            #
            # WHY LATE IN THE MARKET:
            #   At 13 minutes in, the UP/DOWN result is nearly decided. The price IS
            #   the trend — if YES is at $0.78, BTC went up during this interval.
            #   We're not predicting anymore, we're reading a nearly-resolved outcome.
            #
            # WHY NOT EARLIER (the old 30–90s window):
            #   At 30 seconds in, nobody knows which way BTC will move. The signals
            #   have no edge. This is why we were losing at prices near $0.50.
            #
            # TREND FILTER (applied in _make_trading_decision):
            #   Price > 0.60 → clear UP trend → buy YES
            #   Price < 0.40 → clear DOWN trend → buy NO
            #   Price 0.40–0.60 → coin flip → SKIP (don't trade)
            #
            # Share count intuition:
            #   1.4 shares = price $0.71 → strong trend, win rate ~71%
            #   1.9 shares = price $0.53 → weak trend, near coin flip
            #   2.0+ shares = price $0.50 → pure coin flip, SKIP
            # =========================================================================
            # =========================================================================
            seconds_into_sub_interval = elapsed_secs % MARKET_INTERVAL_SECONDS
            
            # --- Dynamic Profile Loading ---
            strategy_profile = "snowball"
            if getattr(self, 'redis_client', None):
                try:
                    cred_json = self.redis_client.get('btc_trading:credentials')
                    if cred_json:
                        import json
                        creds = json.loads(cred_json)
                        strategy_profile = creds.get('strategy_profile', 'snowball')
                except Exception as e:
                    pass

            if strategy_profile == "sniper":
                TRADE_WINDOW_START = 480   # 8 minutes in
                TRADE_WINDOW_END   = 600   # 10 minutes in (120s window)
            elif strategy_profile == "reversal":
                TRADE_WINDOW_START = 180   # 3 minutes in
                TRADE_WINDOW_END   = 480   # 8 minutes in (300s window)
            else: # snowball or default
                TRADE_WINDOW_START = 780   # 13 minutes in
                TRADE_WINDOW_END   = 840   # 14 minutes in (60s window)

            # --- HEARTBEAT LOG FOR DASHBOARD (A CADA 60 SEGUNDOS) ---
            if now.timestamp() - self._last_heartbeat_time >= 60:
                self._last_heartbeat_time = now.timestamp()
                time_to_window = TRADE_WINDOW_START - seconds_into_sub_interval
                if time_to_window > 0:
                    mins = int(time_to_window // 60)
                    secs = int(time_to_window % 60)
                    logger.info(f"⏳ Status do Motor: Aguardando momento exato do ataque (Faltam {mins}m {secs}s)")
                elif seconds_into_sub_interval >= TRADE_WINDOW_END:
                    logger.info(f"⏳ Status do Motor: Aguardando abertura do próximo mercado de 15m")

            # --- LIVE TICKER FOR DASHBOARD (QUANDO MOVE > 1%) ---
            if abs(float(mid_price) - self._last_logged_price) >= 0.01:
                self._last_logged_price = float(mid_price)
                trend = "🟢 Tendência de ALTA" if float(mid_price) >= 0.50 else "🔴 Tendência de BAIXA"
                logger.info(f"📊 Mercado Movendo: Probabilidade de vitória no {trend} está em {float(mid_price)*100:.1f}%")

            if TRADE_WINDOW_START <= seconds_into_sub_interval < TRADE_WINDOW_END and trade_key != self.last_trade_time:
                self.last_trade_time = trade_key

                logger.info("=" * 80)
                logger.info(f" LATE-WINDOW TRADE: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                logger.info(f"   Market: {current_market['slug']}")
                logger.info(f"   Sub-interval #{sub_interval} ({seconds_into_sub_interval:.1f}s in = {seconds_into_sub_interval/60:.1f} min)")
                logger.info(f"   Price: ${float(mid_price):,.4f} | Bid: ${float(bid_decimal):,.4f} | Ask: ${float(ask_decimal):,.4f}")
                logger.info(f"   Trend strength: {'STRONG ✓' if float(mid_price) > 0.60 or float(mid_price) < 0.40 else 'WEAK — may skip'}")
                logger.info(f"   Price history: {len(self.price_history)} points")
                logger.info("=" * 80)

                self.run_in_executor(lambda: self._make_trading_decision_sync(float(mid_price), strategy_profile))

        except Exception as e:
            logger.error(f"Error processing quote tick: {e}")

    # ------------------------------------------------------------------
    # Trading decision (unchanged)
    # ------------------------------------------------------------------

    def _make_trading_decision_sync(self, current_price: float, strategy_profile: str = "snowball"):
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._make_trading_decision(Decimal(str(current_price)), strategy_profile),
                self.loop
            )
            # Wait for it to finish and catch any exceptions
            future.result(timeout=15.0)
        except Exception as e:
            logger.error(f"FATAL ERROR in _make_trading_decision: {e}", exc_info=True)
            
    async def _fetch_market_context(self, current_price: Decimal) -> dict:
        """
        Fetch REAL external data to populate signal processor metadata.

        Returns a dict with:
          - sentiment_score (float 0-100): live Fear & Greed index, or None
          - spot_price (float): live BTC-USD from Coinbase, or None
          - deviation (float): polymarket price vs SMA-20 (always computed)
          - momentum (float): 5-period rate of change (always computed)
          - volatility (float): price std-dev over last 20 ticks (always computed)
        """
        current_price_float = float(current_price)

        # --- Always-available stats from local price_history ---
        recent_prices = [float(p) for p in self.price_history[-20:]]
        sma_20 = sum(recent_prices) / len(recent_prices)
        deviation = (current_price_float - sma_20) / sma_20
        momentum = (
            (current_price_float - float(self.price_history[-5])) / float(self.price_history[-5])
            if len(self.price_history) >= 5 else 0.0
        )
        variance = sum((p - sma_20) ** 2 for p in recent_prices) / len(recent_prices)
        volatility = math.sqrt(variance)

        metadata = {
            "deviation": deviation,
            "momentum": momentum,
            "volatility": volatility,
            # Tick buffer for TickVelocityProcessor
            "tick_buffer": list(self._tick_buffer),
            # YES token id for OrderBookImbalanceProcessor
            "yes_token_id": self._yes_token_id,
        }

        # --- Real sentiment: Fear & Greed Index via NewsSocialDataSource ---
        try:
            from data_sources.news_social.adapter import NewsSocialDataSource
            news_source = NewsSocialDataSource()
            await news_source.connect()
            fg = await news_source.get_fear_greed_index()
            await news_source.disconnect()
            if fg and "value" in fg:
                metadata["sentiment_score"] = float(fg["value"])
                metadata["sentiment_classification"] = fg.get("classification", "")
                logger.info(
                    f"Fear & Greed: {metadata['sentiment_score']:.0f} "
                    f"({metadata['sentiment_classification']})"
                )
            else:
                logger.warning("Fear & Greed fetch returned no data — sentiment processor skipped")
        except Exception as e:
            logger.warning(f"Could not fetch Fear & Greed index: {e} — sentiment processor skipped")

        # --- Real spot price: Coinbase BTC-USD REST API ---
        try:
            from data_sources.coinbase.adapter import CoinbaseDataSource
            coinbase = CoinbaseDataSource()
            await coinbase.connect()
            spot = await coinbase.get_current_price()
            await coinbase.disconnect()
            if spot:
                metadata["spot_price"] = float(spot)
                logger.info(f"Coinbase spot price: ${float(spot):,.2f}")
            else:
                logger.warning("Coinbase price fetch returned None — divergence processor skipped")
        except Exception as e:
            logger.warning(f"Could not fetch Coinbase spot price: {e} — divergence processor skipped")

        logger.info(
            f"Market context — deviation={deviation:.2%}, "
            f"momentum={momentum:.2%}, volatility={volatility:.4f}, "
            f"sentiment={'%.0f' % metadata['sentiment_score'] if 'sentiment_score' in metadata else 'N/A'}, "
            f"spot=${'%.2f' % metadata['spot_price'] if 'spot_price' in metadata else 'N/A'}"
        )
        return metadata

    async def _make_trading_decision(self, current_price: Decimal, strategy_profile: str = "snowball"):
        """
        Make trading decision using our 7-phase system.

        Position size is always $1.00 — no variable sizing, no risk-engine
        calculation needed. The risk engine is still used to check that we
        don't already have too many open positions.
        """
        # --- Mode check ---
        is_simulation = await self.check_simulation_mode()
        logger.info(f"Mode: {'SIMULATION' if is_simulation else 'LIVE TRADING'}")

        # Resolve trades LIVE pendentes (le o resultado REAL do mercado ja fechado).
        # Nunca deve derrubar a decisao — protegido por try/except.
        try:
            self._resolve_pending_live_trades()
        except Exception as e:
            logger.warning(f"Falha ao resolver trades LIVE pendentes: {e}")

        # --- Minimum history guard ---
        if len(self.price_history) < 20:
            logger.warning(f"Not enough price history ({len(self.price_history)}/20)")
            return

        logger.info(f"Current price: ${float(current_price):,.4f}")

        # --- Phase 4a: Build real metadata for processors ---
        metadata = await self._fetch_market_context(current_price)

        # --- Phase 4b: Run all three signal processors ---
        signals = self._process_signals(current_price, metadata)

        if not signals:
            logger.info("No signals generated — no trade this interval")
            return

        logger.info(f"Generated {len(signals)} signal(s):")
        for sig in signals:
            logger.info(
                f"  [{sig.source}] {sig.direction.value}: "
                f"score={sig.score:.1f}, confidence={sig.confidence:.2%}"
            )

        # --- Phase 4c: Fuse signals into one consensus ---
        # min_score lowered to 40 because the TREND FILTER (price at min 11-13)
        # is now the primary decision maker. Fusion is informational context,
        # not the trade gate. The trend gate below is the real filter.
        fused = self.fusion_engine.fuse_signals(signals, min_signals=1, min_score=40.0)
        if not fused:
            logger.info("Fusion produced no actionable signal — no trade this interval")
            return

        logger.info(
            f"FUSED SIGNAL: {fused.direction.value} "
            f"(score={fused.score:.1f}, confidence={fused.confidence:.2%})"
        )

        # --- Phase 5: Position size is configurable via Dashboard (Redis) ---
        import os
        import json
        trade_size_val = os.getenv("MARKET_BUY_USD", "5.00")
        if getattr(self, 'redis_client', None):
            try:
                cred_json = self.redis_client.get('btc_trading:credentials')
                if cred_json:
                    creds = json.loads(cred_json)
                    if 'trade_size' in creds:
                        trade_size_val = str(creds['trade_size'])
            except Exception as e:
                logger.error(f"Error reading trade_size from Redis: {e}")
        
        # =========================================================================
        # TREND FILTER & STRATEGY PROFILES
        # =========================================================================
        price_float = float(current_price)
        direction = None
        trend_confidence = 0.0

        if strategy_profile == "sniper":
            # Early Sniper (Minute 8): confia no sinal fundido se DUAS condicoes baterem:
            #   1. confianca > 70% (baixado de 75%: o sinal de "medo extremo" costuma
            #      capar a confianca em ~74%, entao 75% quase nunca era atingido).
            #   2. score >= 60 (PISO DE CONVICCAO): o score mede o quao lopsided esta
            #      a votacao (50 = empate, 100 = consenso). Sem esse piso, um sinal
            #      quase-empatado (ex: score 52, votos 0.276 vs 0.299) com confianca
            #      71% virava ordem real = cara-ou-coroa disfarcado. Corta esses.
            #   Filtro de edge + teto diario seguem protegendo. Experimento monitorado.
            MIN_SNIPER_SCORE = 60.0
            logger.info("  [Profile: Sniper Antecipado ativado]")
            if fused.confidence > 0.70 and fused.score >= MIN_SNIPER_SCORE:
                # BUGFIX: fused.direction.value e "bullish"/"bearish", mas todo o
                # resto do codigo (edge filter, _place_real_order, _record_paper_trade)
                # decide YES/NO checando `direction == "long"`. Como "bullish" nunca
                # e "long", um sinal BULLISH caia no else e comprava NO (DOWN) — o
                # OPOSTO do sinal. Traduzir para "long"/"short" (igual snowball/reversal).
                sig = fused.direction.value.lower()
                _dir = "long" if sig in ("bullish", "long", "up", "yes") else "short"
                # FILTRO DE DIRECAO (anti-fade): nao apostar CONTRA uma tendencia ja
                # forte. Dados de 02/07 (noite -$3): as contrarias DOWN vs mercado
                # forte pra cima perderam (03:08 -$5, 06:38 -$4); os favoritos (a
                # favor da tendencia) ganharam. Fadar um mercado ja ~decidido e ainda
                # se movendo contra = risco/retorno pessimo. So opera a favor do
                # favorito. Corte 0.60 = mesmo limiar de "tendencia clara" do snowball.
                NO_FADE_HI = 0.60   # nao SHORT (DOWN) acima disso (mercado forte UP)
                NO_FADE_LO = 0.40   # nao LONG (UP) abaixo disso (mercado forte DOWN)
                if _dir == "short" and price_float > NO_FADE_HI:
                    logger.info(f" TREND: Sniper PULOU SHORT — mercado forte pra CIMA ({price_float:.2%} > {NO_FADE_HI:.0%}); nao fadar tendencia (anti-fade)")
                elif _dir == "long" and price_float < NO_FADE_LO:
                    logger.info(f" TREND: Sniper PULOU LONG — mercado forte pra BAIXO ({price_float:.2%} < {NO_FADE_LO:.0%}); nao fadar tendencia (anti-fade)")
                else:
                    direction = _dir
                    trend_confidence = fused.confidence
                    logger.info(f" TREND: Sniper confiante no sinal {direction.upper()} ({fused.confidence:.2%}, score {fused.score:.1f})")
            elif fused.confidence > 0.70:
                logger.info(f" TREND: Sniper ignorou sinal — confiança OK ({fused.confidence:.2%}) mas score {fused.score:.1f} < {MIN_SNIPER_SCORE:.0f} (quase empate, sem convicção)")
            else:
                logger.info(f" TREND: Sniper ignorou sinal (confiança {fused.confidence:.2%} < 70%)")

        elif strategy_profile == "reversal":
            # Reversal Hunter (Minute 3): Look for extremes vs signal
            logger.info("  [Profile: Caçador de Reversão ativado]")
            if price_float > 0.70 and fused.direction.value.lower() == "short" and fused.confidence > 0.65:
                direction = "short"
                trend_confidence = fused.confidence
                logger.info(f" TREND: Reversão Detectada! Preço alto ({price_float}) mas fluxo é SHORT. Apostando no Derretimento (NO)!")
            elif price_float < 0.30 and fused.direction.value.lower() == "long" and fused.confidence > 0.65:
                direction = "long"
                trend_confidence = fused.confidence
                logger.info(f" TREND: Reversão Detectada! Preço baixo ({price_float}) mas fluxo é LONG. Apostando no Pulo (YES)!")
            else:
                logger.info(" TREND: Nenhuma assimetria de reversão extrema encontrada no momento.")

        else: # snowball
            # Snowball (Minute 13): Follow the strict price trend
            logger.info("  [Profile: Bola de Neve (Conservador) ativado]")
            TREND_UP_THRESHOLD   = 0.60   # price above this → buy YES (UP)
            TREND_DOWN_THRESHOLD = 0.40   # price below this → buy NO (DOWN)

            if price_float > TREND_UP_THRESHOLD:
                direction = "long"
                trend_confidence = price_float  # e.g. 0.72 = 72% confident UP
                logger.info(f" TREND: UP ({price_float:.2%} YES probability) → buying YES")
            elif price_float < TREND_DOWN_THRESHOLD:
                direction = "short"
                trend_confidence = 1.0 - price_float  # e.g. 0.28 price = 72% confident DOWN
                logger.info(f" TREND: DOWN ({(1.0 - price_float):.2%} NO probability) → buying NO")
            else:
                logger.info(f" TREND: NEUTRAL (price {price_float:.4f} is between {TREND_DOWN_THRESHOLD} and {TREND_UP_THRESHOLD}) → SKIP")

        if not direction:
            return

        # --- Filtro de EDGE (qualidade da entrada / risco-retorno) ---
        # Preco do token que vamos comprar (o que pagamos por cota):
        #   long (YES) = price_float ; short (NO) = 1 - price_float
        # Se estiver caro demais (favorito ja decidido) o lucro possivel e minusculo
        # e uma unica derrota apaga dezenas de vitorias (ex: comprar a $0.975 = arriscar
        # $5 p/ ganhar ~$0.13). Se estiver barato demais, e um azarao de baixa chance.
        # Só operamos na faixa saudavel [MIN_ENTRY_PRICE, MAX_ENTRY_PRICE].
        import os as _os_edge
        entry_side_price = price_float if direction == "long" else (1.0 - price_float)
        min_entry = float(_os_edge.getenv("MIN_ENTRY_PRICE", "0.15"))
        max_entry = float(_os_edge.getenv("MAX_ENTRY_PRICE", "0.85"))
        if not (min_entry <= entry_side_price <= max_entry):
            motivo = "favorito caro demais (lucro minusculo)" if entry_side_price > max_entry \
                else "azarao de chance baixa demais"
            logger.info(
                f"  🚫 Filtro de edge: entrada a ${entry_side_price:.3f} fora da faixa "
                f"[${min_entry:.2f}–${max_entry:.2f}] — {motivo}. PULANDO (risco/retorno ruim)."
            )
            return

        # --- Position Sizing ---
        # ALL profiles now strictly respect the dashboard value to ensure live trading safety
        # TRAVA: a Polymarket rejeita silenciosamente ordens abaixo de $5.00,
        # então nunca deixamos o tamanho da entrada cair abaixo do mínimo.
        POSITION_SIZE_USD = max(Decimal(trade_size_val), Decimal("5.00"))
        if strategy_profile == "snowball":
            # JUROS COMPOSTOS: entrada = % do Caixa REAL (pUSD on-chain), nao de
            # uma "banca simulada" fixa. Assim a entrada cresce de verdade conforme
            # a banca aumenta (ganhos resgatados) e encolhe se o caixa cai.
            import os as _os
            snowball_pct = Decimal(_os.getenv("SNOWBALL_PCT", "0.20"))  # 20% padrao
            cash = self._get_available_cash_usd()
            if cash and cash >= 5.0:
                entry = (Decimal(str(cash)) * snowball_pct).quantize(Decimal("0.01"))
                entry = max(Decimal("5.00"), entry)      # nunca abaixo do minimo Polymarket
                entry = min(entry, Decimal(str(cash)))    # nunca mais que o caixa disponivel
                POSITION_SIZE_USD = entry
                logger.info(
                    f"  [Snowball/Juros Compostos] Caixa real ${cash:.2f} x "
                    f"{float(snowball_pct):.0%} → entrada ${float(POSITION_SIZE_USD):.2f}"
                )
            else:
                logger.info(
                    f"  [Snowball] Caixa real indisponivel/baixo — "
                    f"usando entrada fixa ${float(POSITION_SIZE_USD):.2f}"
                )
        
        # Risk engine: only check position-count / exposure limits (no sizing math)
        is_valid, error = self.risk_engine.validate_new_position(
            size=POSITION_SIZE_USD,
            direction=direction,
            current_price=current_price,
        )
        if not is_valid:
            logger.warning(f"Risk engine blocked trade: {error}")
            return

        logger.info(f"Position size: ${float(POSITION_SIZE_USD):.2f} | Direction: {direction.upper()}")

        # --- Liquidity guard: don't place if market has no real depth ---
        # The current bid/ask come from the last processed quote tick.
        # If ask <= 0.02 or bid <= 0.02, the orderbook is essentially empty
        # and a FAK (IOC market) order will be rejected immediately.
        last_tick = getattr(self, '_last_bid_ask', None)
        if last_tick:
            last_bid, last_ask = last_tick
            MIN_LIQUIDITY = Decimal("0.02")
            if direction == "long" and last_ask <= MIN_LIQUIDITY:
                logger.warning(
                    f"⚠ No liquidity for BUY: ask=${float(last_ask):.4f} ≤ {float(MIN_LIQUIDITY):.2f} — skipping trade, will retry next tick"
                )
                self.last_trade_time = -1  # Allow retry next tick
                return
            if direction == "short" and last_bid <= MIN_LIQUIDITY:
                logger.warning(
                    f"⚠ No liquidity for SELL: bid=${float(last_bid):.4f} ≤ {float(MIN_LIQUIDITY):.2f} — skipping trade, will retry next tick"
                )
                self.last_trade_time = -1  # Allow retry next tick
                return

        # --- Phase 5 / 6: Execute ---
        if is_simulation:
            await self._record_paper_trade(fused, POSITION_SIZE_USD, current_price, direction, is_live=False)
        else:
            # Travas de risco LIVE (dinheiro real): teto diario de entradas + piso de caixa.
            guard_ok, guard_reason = self._check_live_trade_guards()
            if not guard_ok:
                logger.warning(f"🛡 Trava de risco LIVE: {guard_reason} — entrada BLOQUEADA")
                return
            await self._place_real_order(fused, POSITION_SIZE_USD, current_price, direction)
            
    async def _record_paper_trade(self, signal, position_size, current_price, direction, is_live=False, token_id="", market_id=""):
        exit_delta = timedelta(minutes=1) if self.test_mode else timedelta(minutes=15)
        exit_time = datetime.now(timezone.utc) + exit_delta

        price_float = float(current_price)
        entry_price_side = price_float if direction == "long" else (1.0 - price_float)
        shares = float(position_size) / entry_price_side

        if is_live:
            # LIVE (dinheiro real): NAO adivinha o resultado. Grava PENDENTE; o
            # resultado real e lido depois que o mercado de 15 min fecha, via
            # _resolve_pending_live_trades (le o last-trade-price do token).
            outcome = "PENDING"
            pnl = 0.0
            exit_price_log = price_float
        else:
            # SIMULACAO: sorteia o resultado pela probabilidade do mercado (paper trading).
            win_probability = price_float if direction == "long" else (1.0 - price_float)
            is_win = random.random() < win_probability
            if is_win:
                pnl = (shares * 1.0) - float(position_size)
                outcome = "WIN"
                exit_price_log = 1.00 if direction == "long" else 0.00
            else:
                pnl = -float(position_size)
                outcome = "LOSS"
                exit_price_log = 0.00 if direction == "long" else 1.00

        exit_price = Decimal(str(exit_price_log))
        paper_trade = PaperTrade(
            timestamp=datetime.now(timezone.utc),
            direction=direction.upper(),
            size_usd=float(position_size),
            price=float(current_price),
            signal_score=signal.score,
            signal_confidence=signal.confidence,
            outcome=outcome,
            trade_type="LIVE" if is_live else "SIM",
            token_id=token_id,
            market_id=market_id,
            pnl_usd=pnl,
        )
        self.paper_trades.append(paper_trade)

        self.performance_tracker.record_trade(
            trade_id=f"paper_{int(datetime.now().timestamp())}",
            direction=direction,
            entry_price=current_price,
            exit_price=exit_price,
            size=position_size,
            entry_time=datetime.now(timezone.utc),
            exit_time=exit_time,
            signal_score=signal.score,
            signal_confidence=signal.confidence,
            metadata={
                "simulated": True,
                "num_signals": signal.num_signals if hasattr(signal, 'num_signals') else 1,
                "fusion_score": signal.score,
            }
        )

        # Em LIVE o resultado ainda e desconhecido (PENDENTE) — o contador do Grafana
        # so e atualizado na resolucao real (ver _resolve_pending_live_trades).
        if not is_live and hasattr(self, 'grafana_exporter') and self.grafana_exporter:
            self.grafana_exporter.increment_trade_counter(won=(pnl > 0))
            self.grafana_exporter.record_trade_duration(exit_delta.total_seconds())

        logger.info("=" * 80)
        prefix = "[LIVE TRADING]" if is_live else "[SIMULATION]"
        logger.info(f"{prefix} PAPER TRADE RECORDED")
        logger.info(f"  Direction: {direction.upper()}")
        logger.info(f"  Size: ${float(position_size):.2f}")
        logger.info(f"  Entry Price: ${float(current_price):,.4f}")
        logger.info(f"  Simulated Exit: ${float(exit_price):,.4f}")
        logger.info(f"  Simulated P&L: ${float(pnl):+.2f}")
        logger.info(f"  Outcome: {outcome}")
        logger.info(f"  Total Paper Trades: {len(self.paper_trades)}")
        logger.info("=" * 80)

        self._save_paper_trades()

    def _save_paper_trades(self):
        import json
        import os
        try:
            os.makedirs('data', exist_ok=True)
            trades_data = [t.to_dict() for t in self.paper_trades]
            with open('data/paper_trades.json', 'w') as f:
                json.dump(trades_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save paper trades: {e}")

    def _load_paper_trades(self):
        import json
        import os
        from dateutil.parser import parse
        if not os.path.exists('data/paper_trades.json'):
            return
        try:
            with open('data/paper_trades.json', 'r') as f:
                trades_data = json.load(f)
                for t in trades_data:
                    pt = PaperTrade(
                        timestamp=parse(t['timestamp']),
                        direction=t['direction'],
                        size_usd=float(t['size_usd']),
                        price=float(t['price']),
                        signal_score=float(t.get('signal_score', 0.0)),
                        signal_confidence=float(t.get('signal_confidence', 0.0)),
                        outcome=t.get('outcome', 'PENDING'),
                        trade_type=t.get('trade_type', 'SIM'),
                        token_id=t.get('token_id', ''),
                        market_id=t.get('market_id', ''),
                        pnl_usd=float(t.get('pnl_usd', 0.0)),
                    )
                    self.paper_trades.append(pt)
        except Exception as e:
            logger.warning(f"Failed to load previous paper trades: {e}")

    # ------------------------------------------------------------------
    # Travas de risco LIVE (dinheiro real)
    # ------------------------------------------------------------------

    def _daily_live_key(self) -> str:
        """Chave Redis do contador de entradas LIVE do dia (UTC)."""
        return f"btc_trading:live_count:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

    def _get_daily_live_count(self) -> int:
        """Quantas entradas LIVE ja foram feitas hoje (persistido no Redis, sobrevive a restart)."""
        if not getattr(self, 'redis_client', None):
            return 0
        try:
            v = self.redis_client.get(self._daily_live_key())
            return int(v) if v else 0
        except Exception as e:
            logger.warning(f"Falha ao ler contador diario de entradas LIVE: {e}")
            return 0

    def _increment_daily_live_count(self) -> None:
        """Incrementa o contador diario apos uma ordem LIVE enviada com sucesso."""
        if not getattr(self, 'redis_client', None):
            return
        try:
            key = self._daily_live_key()
            n = self.redis_client.incr(key)
            self.redis_client.expire(key, 172800)  # expira em 2 dias
            logger.info(f"📊 Entradas LIVE hoje: {n}")
        except Exception as e:
            logger.warning(f"Falha ao incrementar contador diario de entradas LIVE: {e}")

    def _get_available_cash_usd(self, max_age_s: float = 60.0):
        """Le o Caixa real (saldo pUSD do funder) via JSON-RPC puro (urllib, sem
        dependencia nova de web3), com cache curto. Retorna float ou None (fail-open).
        O cache evita spam de RPC (test-mode opera a cada minuto e a leitura e usada
        por 2 travas + o sizing do snowball)."""
        import os, time
        cached = getattr(self, '_cash_cache', None)
        if cached and (time.time() - cached[1]) < max_age_s:
            return cached[0]
        funder = os.getenv("POLYMARKET_FUNDER", "")
        if not funder:
            return None
        try:
            import json as _json, urllib.request
            rpc = os.getenv("POLYGON_RPC", "https://polygon-bor-rpc.publicnode.com")
            pusd = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # colateral pUSD
            addr = funder.lower().replace("0x", "").rjust(64, "0")
            data = "0x70a08231" + addr  # selector de balanceOf(address)
            payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": pusd, "data": data}, "latest"]}
            req = urllib.request.Request(
                rpc, data=_json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "btc-bot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                out = _json.loads(r.read().decode())
            raw = out.get("result")
            if not raw or raw == "0x":
                return None
            bal = int(raw, 16) / 1e6  # pUSD tem 6 casas
            self._cash_cache = (bal, time.time())
            return bal
        except Exception as e:
            # fail-open: se nao conseguir ler, nao bloqueia (o teto diario ja limita a exposicao)
            logger.warning(f"Falha ao ler Caixa on-chain (fail-open, nao bloqueia): {e}")
            return None

    def _check_live_trade_guards(self):
        """Trava de risco para operacoes LIVE. Retorna (ok: bool, motivo: str|None)."""
        import os
        # 1. Teto diario de entradas -> limita a perda maxima do dia (N x $5)
        max_per_day = int(os.getenv("MAX_LIVE_TRADES_PER_DAY", "7"))
        count = self._get_daily_live_count()
        if count >= max_per_day:
            return False, (
                f"teto diario de {max_per_day} entradas atingido "
                f"(hoje: {count}) — perda maxima do dia protegida"
            )
        # 2. Piso de caixa -> nunca zerar a conta
        floor = float(os.getenv("CASH_FLOOR_USD", "10"))
        cash = self._get_available_cash_usd()
        if cash is not None and cash < floor:
            return False, f"Caixa ${cash:.2f} abaixo do piso de ${floor:.2f}"
        return True, None

    # ------------------------------------------------------------------
    # Resolucao REAL de trades LIVE (le o resultado do mercado, nao adivinha)
    # ------------------------------------------------------------------

    def _get_clob_reader(self):
        """ClobClient publico (somente leitura de precos), cacheado."""
        client = getattr(self, '_clob_reader', None)
        if client is None:
            try:
                from py_clob_client.client import ClobClient
                client = ClobClient("https://clob.polymarket.com", chain_id=137)
                self._clob_reader = client
            except Exception as e:
                logger.warning(f"Nao consegui criar ClobClient de leitura: {e}")
                self._clob_reader = None
        return self._clob_reader

    def _resolve_pending_live_trades(self):
        """Le o resultado REAL de trades LIVE pendentes cujo mercado de 15 min ja
        fechou e atualiza WIN/LOSS + PnL. Fonte: last-trade-price do token comprado
        (>= 0.9 = ganhou, <= 0.1 = perdeu). Substitui o antigo 'sorteio' aleatorio."""
        pendentes = [
            t for t in self.paper_trades
            if getattr(t, 'trade_type', 'SIM') == 'LIVE'
            and getattr(t, 'outcome', '') == 'PENDING'
            and getattr(t, 'token_id', '')
        ]
        if not pendentes:
            return
        reader = self._get_clob_reader()
        if reader is None:
            return
        now = datetime.now(timezone.utc)
        mudou = False
        for t in pendentes:
            idade_min = (now - t.timestamp).total_seconds() / 60.0
            if idade_min < 16:  # espera o mercado de 15 min fechar (+ folga)
                continue
            try:
                res = reader.get_last_trade_price(t.token_id)
                price = float(res.get('price')) if isinstance(res, dict) else float(res)
            except Exception as e:
                logger.warning(f"Resolver: falha lendo preco (token {t.token_id[:10]}...): {str(e)[:70]}")
                if idade_min > 360:  # >6h sem preco -> desiste, marca desconhecido
                    t.outcome = "UNKNOWN"; mudou = True
                continue
            # entry_side = quanto pagamos por cota do token comprado
            is_long = t.direction.upper() in ("LONG", "YES", "UP")
            entry_side = t.price if is_long else (1.0 - t.price)
            entry_side = entry_side if entry_side > 0 else 0.01
            if price >= 0.9:
                shares = t.size_usd / entry_side
                t.outcome = "WIN"; t.pnl_usd = round(shares - t.size_usd, 2); mudou = True
            elif price <= 0.1:
                t.outcome = "LOSS"; t.pnl_usd = round(-t.size_usd, 2); mudou = True
            elif idade_min > 360:
                t.outcome = "UNKNOWN"; mudou = True
            if t.outcome != "PENDING":
                logger.info(
                    f"✅ Trade LIVE resolvido: {t.direction} ${t.size_usd:.2f} → "
                    f"{t.outcome} (preco final {price:.3f}, PnL ${t.pnl_usd:+.2f})"
                )
                if getattr(self, 'grafana_exporter', None) and t.outcome in ("WIN", "LOSS"):
                    try:
                        self.grafana_exporter.increment_trade_counter(won=(t.outcome == "WIN"))
                    except Exception:
                        pass
        if mudou:
            self._save_paper_trades()

    # ------------------------------------------------------------------
    # Real order (unchanged)
    # ------------------------------------------------------------------

    def _extract_filled_usd(self, resp, intended_usd):
        """Extrai o USD realmente gasto (fill real) da resposta da ordem.

        Numa COMPRA, makingAmount = USDC gasto. O wrapper polymarket-client pode
        normalizar com nomes/locais diferentes, entao testamos varios. Checagem de
        sanidade: o USD gasto nunca excede o pretendido, enquanto as cotas
        (takingAmount) sempre excedem (cota < $1) — isso filtra e evita confundir
        making/taking mesmo sem conhecer a estrutura exata. Retorna float ou None.
        """
        candidates = (
            "making_amount", "makingAmount", "maker_amount", "makerAmount",
            "filled_amount", "filledAmount", "matched_amount", "matchedAmount",
            "size_matched", "sizeMatched", "amount_filled", "amountFilled",
        )
        tol = float(intended_usd) * 1.02  # tolerancia p/ arredondamento

        def _valid(v):
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return f if 0 < f <= tol else None

        # 1. atributos diretos no objeto resp
        for name in candidates:
            r = _valid(getattr(resp, name, None))
            if r is not None:
                return r
        # 2. dicts aninhados comuns
        for holder in ("raw", "data", "response", "result", "__dict__"):
            d = getattr(resp, holder, None)
            if isinstance(d, dict):
                for name in candidates:
                    if name in d:
                        r = _valid(d[name])
                        if r is not None:
                            return r
        # 3. o proprio resp e um dict
        if isinstance(resp, dict):
            for name in candidates:
                if name in resp:
                    r = _valid(resp[name])
                    if r is not None:
                        return r
        return None

    async def _place_real_order(self, signal, position_size, current_price, direction):
        if not self.instrument_id:
            logger.error("No instrument available")
            return

        try:
            # instrument is fetched below after determining YES vs NO token

            logger.info("=" * 80)
            logger.info("LIVE MODE - PLACING REAL ORDER!")
            logger.info("=" * 80)

            # On Polymarket, both UP and DOWN are BUY orders.
            # Bullish = buy YES token (self._yes_instrument_id)
            # Bearish = buy NO token  (self._no_instrument_id)
            # There is NO sell — you always buy whichever side you want.
            side = OrderSide.BUY

            if direction == "long":
                trade_instrument_id = getattr(self, '_yes_instrument_id', self.instrument_id)
                trade_label = "YES (UP)"
            else:
                no_id = getattr(self, '_no_instrument_id', None)
                if no_id is None:
                    logger.warning(
                        "NO token instrument not found for this market — "
                        "cannot bet DOWN. Skipping trade."
                    )
                    return
                trade_instrument_id = no_id
                trade_label = "NO (DOWN)"

            instrument = self.cache.instrument(trade_instrument_id)
            if not instrument:
                logger.error(f"Instrument not in cache: {trade_instrument_id}")
                return

            logger.info(f"Buying {trade_label} token: {trade_instrument_id}")

            trade_price = float(current_price)
            max_usd_amount = float(position_size)

            precision = instrument.size_precision

            # --- DYNAMIC PATCH FIX ---
            # The nautilus patch reads MARKET_BUY_USD for market orders.
            # We must pass the user's size dynamically, otherwise it defaults to $1.00
            # which Polymarket silently rejects since the minimum is $5.00.
            import os
            os.environ["MARKET_BUY_USD"] = str(max_usd_amount)

            # Always BUY — the market-order patch converts this to a USD amount.
            # Pass dummy qty=5 (minimum) so Nautilus risk engine doesn't deny it.
            min_qty_val = float(getattr(instrument, 'min_quantity', None) or 5.0)
            token_qty = max(min_qty_val, 5.0)
            token_qty = round(token_qty, precision)
            logger.info(
                f"BUY {trade_label}: qty={max_usd_amount:.2f} USD "
                f"(Native Py-Clob-Client)"
            )

            timestamp_ms = int(time.time() * 1000)
            unique_id = f"BTC-15MIN-${max_usd_amount:.0f}-{timestamp_ms}"

            # BYPASS: novo SDK oficial polymarket-client
            import asyncio
            from polymarket import AsyncSecureClient

            pk = os.getenv("POLYMARKET_PK", "")
            pk = "0x" + pk if not pk.startswith("0x") else pk
            # FIX: passar a carteira de depósito existente (POLYMARKET_FUNDER).
            # Sem isso o SDK tenta DEPLOYAR uma carteira nova (gasless) e exige
            # uma Builder/Relayer API Key, quebrando com UserInputError. Ver Issue #70.
            funder = os.getenv("POLYMARKET_FUNDER", "") or None
            token_hash = str(trade_instrument_id.value).split("-")[1].split(".")[0]

            async def _place_order():
                async with await AsyncSecureClient.create(
                    private_key=pk,
                    wallet=funder,
                ) as client:
                    # NÃO chamar setup_gasless_wallet(): ela reatribui o client para
                    # uma "gasless wallet" cujo maker é a EOA, que a Polymarket rejeita
                    # ("maker address not allowed, please use the deposit wallet flow").
                    # Usamos direto o client do deposit wallet (create wallet=funder).
                    # Ref: thread Issue #70 (ethanhunt1011) — não usava setup_gasless_wallet.
                    # Garantir que o SDK lê o saldo antes de enviar ordem
                    await client.get_balance_allowance(asset_type='COLLATERAL')
                    # FIX: na Polymarket TODA aposta é um BUY do token escolhido
                    # (YES p/ long, NO p/ short — o token já foi selecionado acima).
                    # Antes: side = "BUY" if direction == "YES" else "SELL", mas
                    # direction é "long"/"short" (nunca "YES") → resultava em SELL sempre.
                    side = "BUY"
                    response = await client.place_market_order(
                        token_id=token_hash,
                        side=side,
                        amount=str(max_usd_amount),
                        order_type="FAK"
                    )
                    return response

            resp = await _place_order()
            filled_usd = None  # USD realmente preenchido (fill real); None = desconhecido
            if getattr(resp, "ok", False) or hasattr(resp, "order_id"):
                logger.info(f"ORDEM ENVIADA: {getattr(resp, 'order_id', 'N/A')}")
                unique_id = getattr(resp, "order_id", unique_id)
                # DIAGNOSTICO: dump cru da resposta pra aprender a estrutura do SDK
                # (o polymarket-client so roda no container; assim vemos os campos reais).
                try:
                    _raw = getattr(resp, "__dict__", None) or resp
                    logger.info(f"  [resp cru] {_raw!r}")
                except Exception:
                    pass
                # FILL REAL: numa compra o USD gasto = makingAmount. Extrai com
                # checagem de sanidade (USD sempre <= pretendido; cotas sempre >
                # pretendido pois cota < $1) — assim nunca confunde making/taking.
                filled_usd = self._extract_filled_usd(resp, max_usd_amount)
                if filled_usd is not None:
                    logger.info(f"  💵 Fill REAL: ${filled_usd:.4f} (pretendido ${max_usd_amount:.2f})")
                else:
                    logger.warning(
                        f"  ⚠ Nao consegui ler o fill real do resp — gravando o pretendido "
                        f"${max_usd_amount:.2f} (PnL pode inflar em fill parcial; ver [resp cru] acima)"
                    )
                # Conta a entrada no teto diario SO apos sucesso real
                self._increment_daily_live_count()
            else:
                logger.error(f"Ordem rejeitada: {getattr(resp, 'code', 'N/A')} — {getattr(resp, 'message', str(resp))}")

            logger.info(f"REAL ORDER SUBMITTED!")
            logger.info(f"  Order ID: {unique_id}")
            logger.info(f"  Direction: {trade_label}")
            logger.info(f"  Side: BUY")
            logger.info(f"  Token Quantity: {token_qty:.6f}")
            logger.info(f"  Estimated Cost: ~${max_usd_amount:.2f}")
            logger.info(f"  Price: ${trade_price:.4f}")
            logger.info("=" * 80)

            self._track_order_event("placed")
            # Grava o FILL REAL quando conhecido (senao o pretendido, como antes).
            recorded_size = Decimal(str(filled_usd)) if filled_usd is not None else position_size
            await self._record_paper_trade(
                signal, recorded_size, current_price, direction, is_live=True,
                token_id=token_hash, market_id=str(trade_instrument_id.value),
            )

        except Exception as e:
            logger.error(f"Error placing real order: {e}")
            import traceback
            traceback.print_exc()
            self._track_order_event("rejected")

    # ------------------------------------------------------------------
    # Signal processing
    # ------------------------------------------------------------------

    def _process_signals(self, current_price, metadata=None):
        signals = []
        if metadata is None:
            metadata = {}

        processed_metadata = {}
        for key, value in metadata.items():
            if isinstance(value, float):
                processed_metadata[key] = Decimal(str(value))
            else:
                processed_metadata[key] = value

        spike_signal = self.spike_detector.process(
            current_price=current_price,
            historical_prices=self.price_history,
            metadata=processed_metadata,
        )
        if spike_signal:
            signals.append(spike_signal)

        if 'sentiment_score' in processed_metadata:
            sentiment_signal = self.sentiment_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if sentiment_signal:
                signals.append(sentiment_signal)

        if 'spot_price' in processed_metadata:
            divergence_signal = self.divergence_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if divergence_signal:
                signals.append(divergence_signal)

        # --- Order Book Imbalance (real-time Polymarket CLOB depth) ---
        if processed_metadata.get('yes_token_id'):
            ob_signal = self.orderbook_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if ob_signal:
                signals.append(ob_signal)

        # --- Tick Velocity (last 60s of Polymarket probability movement) ---
        if processed_metadata.get('tick_buffer'):
            tv_signal = self.tick_velocity_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if tv_signal:
                signals.append(tv_signal)

        # --- Deribit Put/Call Ratio (institutional options sentiment) ---
        pcr_signal = self.deribit_pcr_processor.process(
            current_price=current_price,
            historical_prices=self.price_history,
            metadata=processed_metadata,
        )
        if pcr_signal:
            signals.append(pcr_signal)

        return signals

    # ------------------------------------------------------------------
    # Order events
    # ------------------------------------------------------------------

    def _track_order_event(self, event_type: str) -> None:
        """
        Safely track an order event on the performance tracker.

        PerformanceTracker does not expose `increment_order_counter`, so we
        use whichever method is actually available, or fall back to a no-op.
        Supported event_type values: "placed", "filled", "rejected".
        """
        try:
            pt = self.performance_tracker
            # Try the method that actually exists first
            if hasattr(pt, 'record_order_event'):
                pt.record_order_event(event_type)
            elif hasattr(pt, 'increment_counter'):
                pt.increment_counter(event_type)
            elif hasattr(pt, 'increment_order_counter'):
                pt.increment_order_counter(event_type)
            else:
                # No suitable method found – log and carry on
                logger.debug(
                    f"PerformanceTracker has no order-counter method; "
                    f"ignoring event '{event_type}'"
                )
        except Exception as e:
            logger.warning(f"Failed to track order event '{event_type}': {e}")

    def on_order_filled(self, event):
        logger.info("=" * 80)
        logger.info(f"ORDER FILLED!")
        logger.info(f"  Order: {event.client_order_id}")
        logger.info(f"  Fill Price: ${float(event.last_px):.4f}")
        logger.info(f"  Quantity: {float(event.last_qty):.6f}")
        logger.info("=" * 80)
        self._track_order_event("filled")

    def on_order_denied(self, event):
        logger.error("=" * 80)
        logger.error(f"ORDER DENIED!")
        logger.error(f"  Order: {event.client_order_id}")
        logger.error(f"  Reason: {event.reason}")
        logger.error("=" * 80)
        self._track_order_event("rejected")

    def on_order_rejected(self, event):
        """Handle order rejection — reset trade timer so we can retry next tick."""
        reason = str(getattr(event, 'reason', ''))
        reason_lower = reason.lower()
        if 'no orders found' in reason_lower or 'fak' in reason_lower or 'no match' in reason_lower:
            logger.warning(
                f"⚠ FAK rejected (no liquidity) — resetting timer to retry next tick\n"
                f"  Reason: {reason}"
            )
            self.last_trade_time = -1  # Allow retry on next quote tick
        else:
            logger.warning(f"Order rejected: {reason}")

    # ------------------------------------------------------------------
    # Grafana / stop
    # ------------------------------------------------------------------

    def _start_grafana_sync(self):
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.grafana_exporter.start())
            logger.info("Grafana metrics started on port 8000")
        except Exception as e:
            logger.error(f"Failed to start Grafana: {e}")

    def on_stop(self):
        logger.info("Integrated BTC strategy stopped")
        logger.info(f"Total paper trades recorded: {len(self.paper_trades)}")
        if self.grafana_exporter:
            import asyncio
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.grafana_exporter.stop())
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def setup_redis_logging(redis_client):
    """Broadcast loguru logs to Redis PubSub and a local file for the dashboard terminal."""
    log_file = Path(__file__).parent / "live_logs.txt"
    def file_sink(message):
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(str(message).strip() + "\n")
        except:
            pass
    logger.add(file_sink, format="{message}")
    
    if not redis_client: return
    def redis_sink(message):
        try:
            redis_client.publish('btc_trading:live_logs', str(message).strip())
        except:
            pass
    logger.add(redis_sink, format="{message}")

def run_integrated_bot(simulation: bool = False, enable_grafana: bool = True, test_mode: bool = False):
    """Run the integrated BTC 15-min trading bot - LOADS ALL BTC MARKETS FOR THE DAY"""
    
    print("=" * 80)
    print("INTEGRATED POLYMARKET BTC 15-MIN TRADING BOT")
    print("Nautilus + 7-Phase System + Redis Control")
    print("=" * 80)

    redis_client = init_redis()
    setup_redis_logging(redis_client)

    if redis_client:
        try:
            # Maintain current mode if it exists
            mode = redis_client.get('btc_trading:simulation_mode')
            if mode is None:
                mode_value = '1' if simulation else '0'
                redis_client.set('btc_trading:simulation_mode', mode_value)
                logger.info(f"Redis simulation_mode initialized to: {mode_value}")
        except Exception as e:
            logger.warning(f"Could not interact with Redis simulation mode: {e}")

    print(f"\nConfiguration:")
    print(f"  Initial Mode: {'SIMULATION' if simulation else 'LIVE TRADING'}")
    print(f"  Redis Control: {'Enabled' if redis_client else 'Disabled'}")
    print(f"  Grafana: {'Enabled' if enable_grafana else 'Disabled'}")
    print(f"  Max Trade Size: ${os.getenv('MARKET_BUY_USD', '1.00')}")
    print(f"  Quote stability gate: {QUOTE_STABILITY_REQUIRED} valid ticks")
    print()

    now = datetime.now(timezone.utc)
    
    # =========================================================================
    # Slug timestamps ARE standard Unix timestamps (no offset) aligned to
    # 15-min boundaries. Generate slugs for current + next 24 hours.
    # =========================================================================
    now = datetime.now(timezone.utc)
    unix_interval_start = (int(now.timestamp()) // 900) * 900  # current 15-min boundary

    btc_slugs = []
    for i in range(-1, 97):  # include 1 prior interval (in case we're just after boundary)
        timestamp = unix_interval_start + (i * 900)
        btc_slugs.append(f"btc-updown-15m-{timestamp}")

    filters = {
        "active": True,
        "closed": False,
        "archived": False,
        "slug": tuple(btc_slugs),
        "limit": 100,
    }

    logger.info("=" * 80)
    logger.info("LOADING BTC 15-MIN MARKETS BY SLUG")
    logger.info(f"  Interval start: {unix_interval_start} | Count: {len(btc_slugs)}")
    logger.info(f"  First: {btc_slugs[0]}  Last: {btc_slugs[-1]}")
    logger.info("=" * 80)

    instrument_cfg = InstrumentProviderConfig(
        load_all=True,
        filters=filters,
        use_gamma_markets=True,
    )

    # Polymarket L2 requer chave, secret e passphrase juntos.
    # Load credentials prioritizing Redis over .env
    api_key = os.getenv("POLYMARKET_API_KEY")
    api_secret = os.getenv("POLYMARKET_API_SECRET")
    passphrase = os.getenv("POLYMARKET_PASSPHRASE")
    private_key = os.getenv("POLYMARKET_PK")

    if redis_client:
        try:
            import json
            cred_json = redis_client.get('btc_trading:credentials')
            if cred_json:
                creds = json.loads(cred_json)
                if creds.get("api_key"): api_key = creds["api_key"]
                if creds.get("api_secret"): api_secret = creds["api_secret"]
                if creds.get("api_passphrase"): passphrase = creds["api_passphrase"]
                if creds.get("private_key"): private_key = creds["private_key"]
        except Exception as e:
            logger.warning(f"Failed to load credentials from Redis: {e}")



    if not (api_key and api_secret and passphrase):
        api_key = None
        api_secret = None
        passphrase = None

    poly_data_cfg = PolymarketDataClientConfig(
        private_key=private_key,
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        signature_type=2,
        instrument_provider=instrument_cfg,
    )

    poly_exec_cfg = PolymarketExecClientConfig(
        private_key=private_key,
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        signature_type=2,
        instrument_provider=instrument_cfg,
        funder=os.environ.get("POLYMARKET_FUNDER")
    )

    config_kwargs = {
        "environment": "live",
        "trader_id": "BTC-15MIN-INTEGRATED-001",
        "logging": LoggingConfig(
            log_level="INFO",
            log_directory="./logs/nautilus",
        ),
        "data_engine": LiveDataEngineConfig(qsize=6000),
        "exec_engine": LiveExecEngineConfig(qsize=6000),
        "risk_engine": LiveRiskEngineConfig(bypass=True),
        "data_clients": {POLYMARKET: poly_data_cfg},
    }

    has_creds = bool(api_key and api_secret and passphrase)
    if has_creds:
        config_kwargs["exec_clients"] = {POLYMARKET: poly_exec_cfg}
    else:
        logger.warning("Nenhuma credencial da API encontrada - Exec Client desativado (Apenas Simulacao)")

    config = TradingNodeConfig(**config_kwargs)

    strategy = IntegratedBTCStrategy(
        redis_client=redis_client,
        enable_grafana=enable_grafana,
        test_mode=test_mode,
        simulation=simulation,
    )

    print("\nBuilding Nautilus node...")
    node = TradingNode(config=config)
    node.add_data_client_factory(POLYMARKET, PolymarketLiveDataClientFactory)
    if has_creds:
        node.add_exec_client_factory(POLYMARKET, PolymarketLiveExecClientFactory)
    node.trader.add_strategy(strategy)
    node.build()
    logger.info("Nautilus node built successfully")

    print()
    print("=" * 80)
    print("BOT STARTING")
    print("=" * 80)

    try:
        node.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        node.dispose()
        logger.info("Bot stopped")

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Integrated BTC 15-Min Trading Bot")
    parser.add_argument("--live", action="store_true",
                        help="Run in LIVE mode (real money at risk!). Default is simulation.")
    parser.add_argument("--no-grafana", action="store_true", help="Disable Grafana metrics")
    parser.add_argument("--test-mode", action="store_true",
                        help="Run in TEST MODE (trade every minute for faster testing)")

    args = parser.parse_args()
    enable_grafana = not args.no_grafana
    test_mode = args.test_mode

    # --test-mode ALWAYS forces simulation even if --live is also passed
    if args.test_mode:
        simulation = True
    else:
        simulation = not args.live

    if not simulation:
        logger.warning("=" * 80)
        logger.warning("LIVE TRADING MODE — REAL MONEY AT RISK!")
        logger.warning("=" * 80)
    else:
        logger.info("=" * 80)
        logger.info(f"SIMULATION MODE — {'TEST MODE (fast clock)' if test_mode else 'paper trading only'}")
        logger.info("No real orders will be placed.")
        logger.info("=" * 80)

    try:
        run_integrated_bot(simulation=simulation, enable_grafana=enable_grafana, test_mode=test_mode)
    except Exception as e:
        import traceback
        logger.error(f"FATAL ERROR IN RUN: {e}\n{traceback.format_exc()}")
        time.sleep(600)


if __name__ == "__main__":
    main()