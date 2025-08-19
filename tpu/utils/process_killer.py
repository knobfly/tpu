# modules/utils/process_killer.py
import os
import signal


def kill_existing_main_process():
    pid_file = "/home/nyx/main.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, signal.SIGTERM)
        except Exception:
            pass
        os.remove(pid_file)


def kill_existing_telegram_bot():
    pid_file = "/home/nyx/telegram_controller.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, signal.SIGTERM)
        except Exception:
            pass
        os.remove(pid_file)
