import asyncio
import logging
import os
import signal
import sys
import inspect
from collections import defaultdict
from datetime import datetime

from chart.bitquery_insight import run_bitquery_insight
from chart.bitquery_leaderboard_loop import run_bitquery_leaderboard_loop
from chart.chart_pattern_detector import run_chart_pattern_detector
from chart.volume_divergence_detector import VolumeDivergenceDetector
from chart.volume_spike_detector import VolumeSpikeDetector

# === Core AI + Telegram ===
from core.ai_brain import ai_brain as shared_ai_brain

# === Engine + Core Ops ===
from core.bot_engine import BotEngine
from core.live_config import config
from core.live_config import config as live_config
from core.llm.llm_brain import init_llm_brain, llm_brain
from core.llm.style_evolution import style_evolution
from core.telegram_interface import TelegramInterface
from defense.ai_alpha_overlap_detector import run_alpha_overlap_detector
from defense.auto_rug_blacklist import run as run_auto_rug_blacklist
from defense.honeypot_scanner import HoneypotMonitor
from defense.liquidity_monitor import LiquidityMonitor
from defense.race_protection import race_protector
from defense.rug_wave_defender import RugWaveDefender
from exec.auto_sell_logic import AutoSellLogic, start_autosell
from exec.feeding_frenzy import FeedingFrenzy
from exec.real_time_wallet_trigger import run_real_time_wallet_trigger
from exec.trade_executor import TradeExecutor
from exec.raydium_orca_tracker import RaydiumOrcaTracker
# === Agents ===
from inputs.agents import causal_agent, graph_agent, meta_strategy_agent, style_agent
from inputs.agents.alpha_agent import start_alpha_agent
from inputs.meta_data.smart_token_group_analyzer import run as run_group_analyzer
from inputs.nft.magic_eden_scanner import run_magic_eden_scanner
from inputs.nft.nft_signal_scanner import run_nft_signal_scanner
from inputs.onchain.solana_stream_listener import run_solana_stream_listener
from inputs.onchain.stream_safety_adapter import run_stream_safety_adapter
from inputs.social.influencer_scanner import InfluencerScanner
from inputs.social.sentiment_scanner import start_sentiment_scanner
from inputs.social.telegram_group_auto_joiner_user import start_telegram_user_joiner
from inputs.social.telegram_group_listener import run_telegram_signal_scanner
from inputs.social.telegram_group_scanner import TelegramGroupScanner
from inputs.social.telegram_message_router import start_user_listeners
from inputs.social.x_alpha.x_feed_scanner import run_scan_x_feed
from inputs.social.x_alpha.x_orchestrator import run_x_orchestrator
from inputs.trending.trending_fanin import run_trending_fanin
from inputs.wallet.cabal_watcher import run_cabal_watcher

# === Wallet + Events ===
from inputs.wallet.multi_wallet_manager import multi_wallet
from inputs.wallet.wallet_alpha_sniper_overlap import run_alpha_sniper_overlap
from inputs.wallet.wallet_cluster_tracker import WalletClusterTracker
from inputs.wallet.wallet_core import WalletManager
from librarian.data_librarian import librarian, run_librarian

# === Critical Boot: Bandit + FeatureStore First ===
from librarian.feature_store import init_feature_store
from maintenance.auto_rebalance import run_auto_rebalance
from maintenance.junk_token_cleaner import run_junk_token_cleaner
from maintenance.rpc_monitor import rpc_monitor_loop
from maintenance.wallet_sweeper import run_wallet_sweeper
from memory.brain_pulse_loop import run_brain_pulse_loop
from memory.chat_memory_loader import load_chat_logs_at_startup
from memory.memory_sync_service import run_memory_sync
from solana.rpc.async_api import AsyncClient
from special.continuous_learning_loop import run_continuous_learning

# === Strategy + Execution ===
from special.experiment_runner import ExperimentRunner
from strategy.contextual_bandit import init_bandit_manager
from strategy.strategy_rotation_scheduler import run_strategy_scheduler
from utils.log_cleaner import clean_logs, run_hourly_maintenance
from utils.logger import log_event
from utils.price_fetcher import start_price_websocket
from utils.process_killer import kill_existing_main_process

# === Utils / Infra ===
from utils.rpc_loader import get_active_rpc, rpc_rotation_loop
from utils.service_status import update_status
from utils.websocket_loader import websocket_rotation_loop

MAIN_PID_FILE = "/home/ubuntu/nyx/main.pid"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("/home/ubuntu/nyx/runtime/logs/main_output.log"),
        logging.StreamHandler()
    ]
)

bot_engine_instance: BotEngine = None
logger = logging.getLogger("main")

# ---------- helpers: just start tasks, no supervision ----------

def _to_coro(fn):
    """Call fn and return a coroutine regardless of sync/async signature."""
    if inspect.iscoroutinefunction(fn):
        return fn()
    res = fn()
    if inspect.iscoroutine(res):
        return res
    async def _noop():
        return res
    return _noop()

def start_task(name: str, fn):
    """Fire-and-forget. No restarts. Log if task crashes so you see failures."""
    async def _runner():
        try:
            await _to_coro(fn)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.exception(f"[{name}] crashed: {e}")
    logging.info(f"[Boot] Starting module: {name}")
    return asyncio.create_task(_runner(), name=name)

# ----------------------------------------------------------------

async def start_robot_from_telegram():
    global bot_engine_instance
    if bot_engine_instance:
        log_event("⚠️ Bot engine already running.")
        return
    await main()

def register_all_agents():
    causal_agent.register()
    style_agent.register()
    meta_strategy_agent.register()
    graph_agent.register()

async def main():
    global bot_engine_instance

    # Init core stores first
    feature_store = await init_feature_store()
    bandit = await init_bandit_manager()

    update_status("main")
    log_event("🛠 Starting Nyx boot sequence...")
    _ensure_runtime_dirs()
    librarian.load_all()

    # Wire AI brain
    shared_ai_brain.attach_bandit(bandit)
    shared_ai_brain.attach_feature_store(feature_store)
    shared_ai_brain.attach_librarian(librarian)

    register_all_agents()

    log_event(f"🚀 Bot launching at {datetime.utcnow().isoformat()} UTC")
    kill_existing_main_process()
    with open(MAIN_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # Wallet bootstrap
    multi_wallet.load_all_wallets()
    wallet: WalletManager = await multi_wallet.get_best_wallet(min_threshold=0.5)
    if not wallet:
        log_event("❌ No wallet with enough SOL to start the bot.")
        return

    tokens = await wallet.get_tokens()
    log_event(f"🔁 Wallet sync complete. Found {len(tokens)} active tokens.")
    if tokens:
        from strategy.strategy_memory import update_meta_keywords
        for token in tokens:
            update_meta_keywords(token_address=token["mint"], keywords=["wallet_sync"])
        log_event("🧠 Meta keyword tracker initialized.")
    else:
        log_event("⚠️ No tokens to prime meta keywords from.")

    await load_chat_logs_at_startup("/home/ubuntu/nyx/runtime/data/chat_chunks")

    # Engine
    rpc = AsyncClient(get_active_rpc())
    bot_engine_instance = BotEngine(wallet, rpc)
    engine = bot_engine_instance
    TelegramInterface(config, wallet)  # side-effect: starts bot interface
    trade_executor = TradeExecutor()
    AutoSellLogic(wallet)
    frenzy = FeedingFrenzy(wallet, engine, shared_ai_brain)

    shared_ai_brain.attach_engine(engine)
    shared_ai_brain.attach_wallet(wallet)
    librarian.register("ai_brain", shared_ai_brain)

    shared_ai_brain.set_action_hooks(
        buy_hook=trade_executor.buy_token,
        simulate_hook=_simulate_buy,
        risk_check_hook=_risk_gate
    )

    await start_autosell(wallet)
    experiment_runner = ExperimentRunner(
        ai_brain=shared_ai_brain,
        engine=engine,
        wallet=wallet,
    )

    # LLM brain
    init_llm_brain()
    llm_brain.inject_identity()
    llm_brain.attach_engine(engine)
    llm_brain.attach_wallet(wallet)

    # Telegram alpha agent etc.
    await start_alpha_agent(enable_telegram=True)

    # === Phase 0: Foundations ===
    log_event("[Boot] Phase 0: Foundations")
    start_task("Librarian", run_librarian)
    start_task("MemorySync", run_memory_sync)
    start_task("HourlyMaintenance", run_hourly_maintenance)

    await asyncio.sleep(2)

    # === Phase 1: Core Modules ===
    log_event("[Boot] Phase 1: Core Modules starting")
    start_task("PriceWebSocket", start_price_websocket)
    start_task("StreamSafetyAdapter", lambda: run_stream_safety_adapter(poll_source="auto"))
    start_task("TrendingFanin", run_trending_fanin)
    start_task("AutoRebalance", lambda: run_auto_rebalance(wallet, shared_ai_brain))
    start_task("WalletSweeper", run_wallet_sweeper)
    start_task("SolanaStream", run_solana_stream_listener)
    start_task("DailyLogCleaner", clean_logs)
    start_task("Engine", engine.run)
    start_task("RaydiumOrcaTracker", lambda: RaydiumOrcaTracker().run)

    await asyncio.sleep(5)

    # === Phase 2: Tracker Modules ===
    log_event("[Boot] Phase 2: Tracker Modules starting]")
    start_task("HoneypotMonitor", HoneypotMonitor(wallet).run)
    start_task("InfluencerScanner", lambda: InfluencerScanner(wallet, engine.executor).start())
    start_task("RugWaveDefender", RugWaveDefender().run)
    start_task("VolumeSpikeDetector", VolumeSpikeDetector().run)
    start_task("LiquidityMonitor", LiquidityMonitor().run)
    start_task("SentimentScanner", start_sentiment_scanner)
    start_task("WalletClusterTracker", WalletClusterTracker().run)
    start_task("VolumeDivergenceDetector", VolumeDivergenceDetector().run)
    start_task("TelegramGroupScanner", TelegramGroupScanner().run)
    start_task("TelegramSignalScanner", run_telegram_signal_scanner)
    start_task("TGUserSignalListener", start_user_listeners)
    start_task("XOrchestrator", run_x_orchestrator)
    start_task("XFeedScanner", run_scan_x_feed)
    start_task("AIAlphaOverlap", lambda: run_alpha_overlap_detector())
    start_task("JunkTokenCleaner", run_junk_token_cleaner)
    start_task("ChartPatternDetector", lambda: run_chart_pattern_detector(shared_ai_brain))
    start_task("BitqueryInsight", run_bitquery_insight)
    start_task("BitqueryLeaderboard", run_bitquery_leaderboard_loop)
    start_task("RealTimeWalletTrigger", run_real_time_wallet_trigger)
    start_task("NFTSignalScanner", run_nft_signal_scanner)
    start_task("MagicEdenScanner", lambda: run_magic_eden_scanner(interval=int(config.get("me_scan_interval_sec", 30))))

    await asyncio.sleep(5)

    # === Phase 3: Brain Modules ===
    log_event("[Boot] Phase 3: Brain Modules starting")
    start_task("NyxBrain", shared_ai_brain.run)
    start_task("🧪 Experiment Runner", experiment_runner.run)
    start_task("LLMBrain", llm_brain.run)
    start_task("RPCMonitor", rpc_monitor_loop)
    start_task("RPCRotation", rpc_rotation_loop)
    start_task("WebSocketRotation", websocket_rotation_loop)
    start_task("RaceProtector", race_protector.run)
    start_task("TokenGroupAnalyzer", run_group_analyzer)
    start_task("AutoRugBlacklist", run_auto_rug_blacklist)
    start_task("AlphaSniperOverlap", run_alpha_sniper_overlap)
    start_task("TelegramUserJoiner", start_telegram_user_joiner)
    start_task("CabalWatcher", run_cabal_watcher)
    start_task("StrategyScheduler", run_strategy_scheduler)
    start_task("BrainPulseMonitor", run_brain_pulse_loop)

    log_event("✅ All boot phases dispatched.")

    # Keep the main task alive forever; modules run in background tasks.
    # If you want a couple of background loops (learning, etc.), start them here too.
    await asyncio.gather(
        DailyLearningLoop(),
    )

def shutdown_handler(signal_received=None, frame=None):
    logging.info("🧠 Saving memory before shutdown...")
    librarian.persist_all()
    logging.info("🛑 Graceful shutdown complete.")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

# Priority Scheduler (left as-is; unused by boot)
CURRENT_THROTTLES = defaultdict(lambda: 1.0)

def _ensure_runtime_dirs():
    dirs = [
        "/home/ubuntu/nyx/runtime/library/bandit",
        "/home/ubuntu/nyx/runtime/memory",
        "/home/ubuntu/nyx/runtime/logs",
        "/home/ubuntu/nyx/runtime/monitor",
        "/home/ubuntu/nyx/runtime/data/chat_chunks",
        "/home/ubuntu/nyx/runtime/firehose",
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)

async def priority_control_loop(poll_every: float = 1.0):
    # left defined but not started
    from runtime.priority_scheduler import scheduler_step
    while True:
        CURRENT_THROTTLES.update(scheduler_step())
        await asyncio.sleep(poll_every)

trade_executor = TradeExecutor()

async def _simulate_buy(token: str, amount_sol: float, horizon_s: int):
    from utils.logger import log_event
    log_event(f"[SIM] {token} probe {amount_sol} SOL for {horizon_s}s")

def _risk_gate(token: str):
    flags = []
    try:
        from defense.honeypot_scanner import is_honeypot
        from defense.liquidity_monitor import check_lp_status
        from strategy.strategy_memory import is_blacklisted_token
        if is_blacklisted_token(token):
            flags.append("blacklisted")
        if is_honeypot(token):
            flags.append("honeypot")
        if check_lp_status(token):
            flags.append("lp_unlocked")
    except Exception:
        pass
    ok = not flags
    return ok, flags

async def DailyLearningLoop():
    while True:
        try:
            await run_continuous_learning()
            style_evolution().decay()
        except Exception as e:
            logging.warning(f"[DailyLearningLoop] Error: {e}")
        await asyncio.sleep(24 * 3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log_event("🛑 Bot manually stopped.")
        librarian.persist_all()
    except Exception as e:
        log_event(f"❌ Bot crashed at runtime: {e}")
        librarian.persist_all()
