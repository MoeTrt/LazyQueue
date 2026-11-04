import asyncio
import os
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont

import qrcode

import agent

BG = "#07100B"
CARD = "#101C15"
LINE = "#2A3B2F"
GOLD = "#D4B26A"
INK = "#F3EEE2"
MUTED = "#9AA79E"
OK = "#7FD69C"
OFF = "#66746A"
DANGER = "#E0574A"

startup_error: list[BaseException] = []
FAMILY = "TkDefaultFont"


def run_agent():
    try:
        asyncio.run(agent.main(gui=True))
    except BaseException as e:
        startup_error.append(e)


def prevent_sleep():
    """Empêche la mise en veille du PC tant que l'app tourne (le téléphone perdrait l'agent)."""
    if sys.platform == "darwin":
        try:
            import subprocess

            subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except OSError:
            pass
    elif os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except (AttributeError, OSError):
            pass


def enable_windows_dpi():
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


class Button(tk.Label):
    def __init__(self, master, text, command, primary=False):
        self.colors = (GOLD, "#1B1405", "#E6C987") if primary else (CARD, INK, "#1A2A20")
        super().__init__(master, text=text, bg=self.colors[0], fg=self.colors[1], padx=18, pady=10, cursor="hand2",
                         font=(FAMILY, 13, "bold"), highlightthickness=1, highlightbackground=LINE)
        self.command = command
        self.bind("<Button-1>", lambda _e: self.command())
        self.bind("<Enter>", lambda _e: self.configure(bg=self.colors[2]))
        self.bind("<Leave>", lambda _e: self.configure(bg=self.colors[0]))


class Window(tk.Tk):
    def __init__(self):
        global FAMILY
        super().__init__()
        FAMILY = self.base_family = tkfont.nametofont("TkDefaultFont").actual()["family"]
        self.title("LazyQueue")
        self.configure(bg=BG)
        self.resizable(False, False)
        try:
            self.icon = tk.PhotoImage(file=str(agent.RES_DIR / "static" / "icon-192.png"))
            self.iconphoto(True, self.icon)
        except tk.TclError:
            pass
        self.url = ""
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

        wrap = tk.Frame(self, bg=BG, padx=28, pady=24)
        wrap.pack()
        tk.Label(wrap, text="LazyQueue", bg=BG, fg=INK, font=(self.base_family, 24, "bold")).pack()
        tk.Label(wrap, text="Scanne ce QR code avec l'appareil photo de ton téléphone", bg=BG, fg=MUTED,
                 font=(self.base_family, 12)).pack(pady=(4, 16))

        self.qr = tk.Canvas(wrap, width=264, height=264, bg="#FFFFFF", highlightthickness=0)
        self.qr.pack()

        code_box = tk.Frame(wrap, bg=CARD, highlightthickness=1, highlightbackground=LINE, padx=16, pady=10)
        code_box.pack(fill="x", pady=(18, 0))
        tk.Label(code_box, text="Code d'appairage (app sur l'écran d'accueil)", bg=CARD, fg=MUTED,
                 font=(self.base_family, 11)).pack()
        self.code = tk.Label(code_box, text="······", bg=CARD, fg=GOLD, font=(self.base_family, 30, "bold"))
        self.code.pack()

        status = tk.Frame(wrap, bg=BG)
        status.pack(fill="x", pady=(16, 0))
        self.client_dot, self.client_text = self.status_row(status, "Client League of Legends")
        self.phone_dot, self.phone_text = self.status_row(status, "Téléphones connectés")

        actions = tk.Frame(wrap, bg=BG)
        actions.pack(fill="x", pady=(20, 0))
        self.copy_btn = Button(actions, "Copier le lien", self.copy_link)
        self.copy_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))
        Button(actions, "Quitter", self.quit_app).pack(side="left", expand=True, fill="x", padx=(6, 0))

        tk.Label(wrap, text="Garde cette fenêtre ouverte pendant que tu joues.", bg=BG, fg=OFF,
                 font=(self.base_family, 11)).pack(pady=(16, 0))
        self.after(200, self.refresh)

    def status_row(self, master, label):
        row = tk.Frame(master, bg=BG)
        row.pack(fill="x", pady=3)
        dot = tk.Label(row, text="●", bg=BG, fg=OFF, font=(self.base_family, 14))
        dot.pack(side="left")
        tk.Label(row, text=label, bg=BG, fg=INK, font=(self.base_family, 13)).pack(side="left", padx=(6, 0))
        value = tk.Label(row, text="…", bg=BG, fg=MUTED, font=(self.base_family, 13))
        value.pack(side="right")
        return dot, value

    def draw_qr(self, url):
        qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        size, margin = 264, 16
        cell = (size - 2 * margin) / len(matrix)
        self.qr.delete("all")
        for y, row in enumerate(matrix):
            for x, on in enumerate(row):
                if on:
                    x0, y0 = margin + x * cell, margin + y * cell
                    self.qr.create_rectangle(x0, y0, x0 + cell + 0.5, y0 + cell + 0.5, fill=BG, width=0)

    def refresh(self):
        if startup_error:
            self.show_error(startup_error[0])
            return
        s = agent.status()
        if s["url"] and s["url"] != self.url:
            self.url = s["url"]
            self.draw_qr(self.url)
        self.code.configure(text=f"{s['code'][:3]} {s['code'][3:]}")
        self.client_dot.configure(fg=OK if s["client"] else OFF)
        self.client_text.configure(text="connecté" if s["client"] else "non détecté")
        self.phone_dot.configure(fg=OK if s["phones"] else OFF)
        self.phone_text.configure(text=str(s["phones"]))
        self.after(500, self.refresh)

    def show_error(self, error):
        in_use = isinstance(error, OSError) and getattr(error, "errno", None) in (48, 98, 10048)
        message = ("LazyQueue est déjà lancé sur ce PC (port 8765 occupé)." if in_use
                   else f"LazyQueue n'a pas pu démarrer :\n{error!r}")
        for child in self.winfo_children():
            child.destroy()
        box = tk.Frame(self, bg=BG, padx=28, pady=28)
        box.pack()
        tk.Label(box, text="LazyQueue", bg=BG, fg=INK, font=(self.base_family, 22, "bold")).pack()
        tk.Label(box, text=message, bg=BG, fg=DANGER, wraplength=320, justify="center",
                 font=(self.base_family, 13)).pack(pady=16)
        quit_btn = Button(box, "Fermer", self.quit_app)
        quit_btn.pack(fill="x")

    def copy_link(self):
        if not self.url:
            return
        self.clipboard_clear()
        self.clipboard_append(self.url)
        self.copy_btn.configure(text="Lien copié")
        self.after(1500, lambda: self.copy_btn.configure(text="Copier le lien"))

    def quit_app(self):
        self.destroy()
        os._exit(0)


def main():
    enable_windows_dpi()
    prevent_sleep()
    threading.Thread(target=run_agent, daemon=True).start()
    Window().mainloop()


if __name__ == "__main__":
    main()
