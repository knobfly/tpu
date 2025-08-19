# /api_controller.py

import logging
import threading

from core.bot_engine import get_current_mode, toggle_mode
from core.config import live_config, set_goal_profit, set_volume_threshold, toggle_rebuy_on_dip
from exec.feeding_frenzy import is_frenzy_ready, start_feeding_frenzy
from flask import Flask, jsonify, request
from strategy.strategy_memory import is_ai_strategy_enabled, toggle_ai_strategy
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc
from utils.websocket_loader import get_current_ws_rpc  # ✅ WS fallback

app = Flask(__name__)
bot_running = False  # Optional: can be made dynamic with real bot status tracking


# === API Routes ===

@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "mode": get_current_mode(),
        "ai_enabled": is_ai_strategy_enabled(),
        "bot_running": bot_running,
        "goal_profit": live_config.get("goal_profit"),
        "volume_threshold": live_config.get("volume_threshold"),
        "rebuy_on_dip": live_config.get("rebuy_on_dip", False)
    })


@app.route("/toggle_mode", methods=["POST"])
def toggle_mode_api():
    new_mode = toggle_mode()
    log_event(f"Mode changed via API to {new_mode}")
    return jsonify({"new_mode": new_mode})


@app.route("/toggle_ai", methods=["POST"])
def toggle_ai():
    new_state = toggle_ai_strategy()
    log_event(f"AI strategy toggled via API to {'ON' if new_state else 'OFF'}")
    return jsonify({"ai_strategy": new_state})


@app.route("/set_goal", methods=["POST"])
def update_goal():
    try:
        data = request.get_json()
        new_goal = float(data.get("value"))
        set_goal_profit(new_goal)
        log_event(f"Goal profit updated via API to {new_goal}")
        return jsonify({"status": "ok", "goal_profit": new_goal})
    except Exception as e:
        logging.error(f"API error setting goal: {e}")
        return jsonify({"error": "Invalid request"}), 400


@app.route("/set_volume", methods=["POST"])
def update_volume():
    try:
        data = request.get_json()
        new_threshold = float(data.get("value"))
        set_volume_threshold(new_threshold)
        log_event(f"Volume threshold updated via API to {new_threshold}")
        return jsonify({"status": "ok", "volume_threshold": new_threshold})
    except Exception as e:
        logging.error(f"API error setting volume: {e}")
        return jsonify({"error": "Invalid request"}), 400


@app.route("/toggle_rebuy", methods=["POST"])
def toggle_rebuy():
    new_state = toggle_rebuy_on_dip()
    log_event(f"Rebuy-on-dip toggled via API to {'ON' if new_state else 'OFF'}")
    return jsonify({"rebuy_on_dip": new_state})


@app.route("/start_frenzy", methods=["POST"])
def api_frenzy():
    if is_frenzy_ready():
        try:
            threading.Thread(target=start_feeding_frenzy, args=(
                get_active_rpc(),
                get_current_ws_rpc(),
                live_config
            ), daemon=True).start()
            log_event("Feeding Frenzy started via API")
            return jsonify({"status": "frenzy_started"})
        except Exception as e:
            logging.error(f"Error starting Feeding Frenzy via API: {e}")
            return jsonify({"error": "Failed to start frenzy"}), 500
    else:
        return jsonify({"error": "Frenzy not ready"}), 429


@app.route("/trades", methods=["GET"])
def trades():
    try:
        with open("logs/trades.log", "r") as f:
            lines = f.readlines()[-50:]
        return jsonify({"recent_trades": lines})
    except Exception as e:
        logging.warning(f"Failed to read trades log: {e}")
        return jsonify({"recent_trades": []})


@app.route("/heartbeat", methods=["GET"])
def heartbeat():
    return jsonify({"alive": True})


# === Run Server ===

def run_api():
    app.run(host="0.0.0.0", port=8989)

