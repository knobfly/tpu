# --- LEARNING SECTION ---
import logging

class LibrarianLearning:
	def evolve_strategy(self, context=None, outcome=None, reinforce=True):
		ctx = context or {}
		try:
			try:
				from strategy.strategy_memory import evolve_strategy as sm_evolve
				res = sm_evolve(context=ctx, outcome=outcome, reinforce=reinforce)
				if isinstance(res, dict):
					return res
			except Exception as e:
				logging.debug(f"[LibrarianLearning] strategy_memory.evolve_strategy unavailable: {e}")
			try:
				from strategy.strategy_memory import tune_strategy_context as sm_tune_ctx
				res = sm_tune_ctx(ctx)
				if isinstance(res, dict):
					res.setdefault("notes", []).append("via strategy_memory.tune_strategy_context")
					return res
			except Exception as e:
				logging.debug(f"[LibrarianLearning] strategy_memory.tune_strategy_context unavailable: {e}")
			try:
				from strategy.ai_self_tuner import tune_strategy as legacy_tune
				res = legacy_tune(ctx)
				if isinstance(res, dict):
					res.setdefault("notes", []).append("via ai_self_tuner.tune_strategy")
					return res
			except Exception as e:
				logging.debug(f"[LibrarianLearning] ai_self_tuner.tune_strategy unavailable: {e}")
			logging.info("[LibrarianLearning] evolve_strategy: no strategy backend available; returning no-op.")
			return {
				"final_score": float(ctx.get("meta_score", 0) or 0),
				"aggression": "balanced",
				"exit_mode": "default",
				"notes": ["no-op"],
			}
		except Exception as e:
			logging.warning(f"[LibrarianLearning] evolve_strategy failed: {e}")
			return {
				"final_score": float(ctx.get("meta_score", 0) or 0),
				"aggression": "balanced",
				"exit_mode": "default",
				"notes": [f"error: {e}"],
			}
# librarian_learning.py
# Learning, strategy, and reinforcement logic for DataLibrarian.

