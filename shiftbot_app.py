import datetime
import json
import os
import queue
import subprocess
import sys
import threading
from tkinter import simpledialog

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw
from plyer import notification

bot_process = None
output_queue = queue.Queue()
tray_icon = None
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

app = ctk.CTk()
app.title("ShiftBot")
app.geometry("500x420")


def load_settings():
    if not os.path.exists(SETTINGS_PATH):
        return {"check_interval_seconds": 30, "auto_reply": True}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"check_interval_seconds": 30, "auto_reply": True}


def save_settings():
    with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2)


settings = load_settings()


def log(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    if "log_box" in globals():
        log_box.insert("end", f"[{timestamp}] {message}\n")
        log_box.see("end")


def read_output(process):
    """Read subprocess stdout in a background thread and push lines to the queue."""
    for line in process.stdout:
        output_queue.put(line.rstrip())
    output_queue.put(None)  # sentinel: process finished

def notify(title, message):
    notification.notify(
        title=title,
        message=message,
        app_name="ShiftBot",
        timeout=5
    )

def poll_output():
    """Drain the queue and update the log; also detect when the process ends."""
    while not output_queue.empty():
        line = output_queue.get_nowait()
        if line is None:
            status_label.configure(text="Status: Stopped")
            _set_buttons(running=False)
            log("ShiftBot exited.")
        else:
            log(line)
            if "Reply sent" in line:
                notify("ShiftBot", "Reply sent successfully.")
            if "Found shift email" in line:
                notify("ShiftBot", "New shift email found.")
    app.after(100, poll_output)


def _set_buttons(running: bool):
    start_button.configure(state="disabled" if running else "normal")
    stop_button.configure(state="normal" if running else "disabled")


def start_bot():
    global bot_process
    if bot_process is not None and bot_process.poll() is None:
        log("ShiftBot is already running.")
        return

    bot_process = subprocess.Popen(
        [sys.executable, "shiftbot.py"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    threading.Thread(target=read_output, args=(bot_process,), daemon=True).start()

    status_label.configure(text="Status: Running")
    _set_buttons(running=True)
    log("ShiftBot started.")


def stop_bot():
    global bot_process
    if bot_process is None or bot_process.poll() is not None:
        log("ShiftBot is not running.")
        return

    bot_process.terminate()
    status_label.configure(text="Status: Stopped")
    _set_buttons(running=False)
    log("ShiftBot stopped.")


def on_close():
    if settings.get("minimize_to_tray", True):
        app.withdraw()
        log("ShiftBot minimized to system tray.")
    else:
        quit_app()


def open_settings():
    current = settings.get("check_interval_seconds", 30)

    value = simpledialog.askinteger(
        "ShiftBot Settings",
        "Check interval in seconds:",
        initialvalue=current,
        minvalue=5,
        maxvalue=3600,
    )

    if value is not None:
        settings["check_interval_seconds"] = value
        save_settings()
        log(f"Settings saved. Check interval: {value} seconds.")

def toggle_auto_reply():
    current = settings.get("auto_reply", True)
    settings["auto_reply"] = not current
    save_settings()
    status = "enabled" if settings["auto_reply"] else "disabled"
    log(f"Auto-reply {status}.")
    update_auto_reply_button()

def update_auto_reply_button():
    if settings.get("auto_reply", True):
        auto_reply_button.configure(fg_color="#2ecc71", text="Auto-Reply: ON")
    else:
        auto_reply_button.configure(fg_color="#e74c3c", text="Auto-Reply: OFF")

def create_tray_image():
    image = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill="blue")
    draw.text((22, 20), "S", fill="white")
    return image

def show_window(icon=None, item=None):
    app.after(0, app.deiconify)

def quit_app(icon=None, item=None):
    global tray_icon

    if bot_process is not None and bot_process.poll() is None:
        bot_process.terminate()

    if tray_icon:
        tray_icon.stop()

    app.after(0, app.destroy)

def setup_tray():
    global tray_icon

    menu = pystray.Menu(
        pystray.MenuItem("Show ShiftBot", show_window),
        pystray.MenuItem("Quit", quit_app),
    )

    tray_icon = pystray.Icon(
        "ShiftBot",
        create_tray_image(),
        "ShiftBot",
        menu,
    )

    threading.Thread(target=tray_icon.run, daemon=True).start()

# ── UI layout ────────────────────────────────────────────────────────────────

title = ctk.CTkLabel(app, text="ShiftBot", font=("Arial", 28, "bold"))
title.pack(pady=20)

status_label = ctk.CTkLabel(app, text="Status: Stopped", font=("Arial", 16))
status_label.pack(pady=6)

btn_frame = ctk.CTkFrame(app, fg_color="transparent")
btn_frame.pack(pady=6)

start_button = ctk.CTkButton(btn_frame, text="Start Bot", width=140, command=start_bot)
start_button.pack(side="left", padx=8)
auto_reply_button = ctk.CTkButton(app, text="Auto-Reply: ON", width=140, fg_color="#2ecc71", command=toggle_auto_reply)
auto_reply_button.pack(pady=6)
settings_button = ctk.CTkButton(app, text="Settings", width=140, command=open_settings)
settings_button.pack(pady=6)

stop_button = ctk.CTkButton(
    btn_frame, text="Stop Bot", width=140, fg_color="#c0392b",
    hover_color="#922b21", command=stop_bot, state="disabled"
)
stop_button.pack(side="left", padx=8)

log_box = ctk.CTkTextbox(app, width=455, height=200)
log_box.pack(pady=12)

clear_button = ctk.CTkButton(
    app, text="Clear Log", width=120, fg_color="gray30",
    hover_color="gray20", command=lambda: log_box.delete("1.0", "end")
)
clear_button.pack()

# ── Bootstrap ─────────────────────────────────────────────────────────────────
app.protocol("WM_DELETE_WINDOW", on_close)
setup_tray()
update_auto_reply_button()
log("Ready.")
app.after(100, poll_output)
app.mainloop()