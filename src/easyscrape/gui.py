"""Small Tk front end: paste links or pick link files, then run the CLI in a subprocess."""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__

_URL_RE = re.compile(r"https?://\S+")
_FILE_TYPES = [
    ("Link dosyaları", "*.txt *.md *.json *.csv *.list"),
    ("Tüm dosyalar", "*.*"),
]
_HELP = (
    "Linkleri kutuya yapıştırın (her satıra bir tane) ve/veya bir .txt, .md, .json dosyası "
    "seçin; dosyadaki tüm linkler okunur.\n\n"
    "Kayıt klasörü varsayılan olarak Masaüstü; sadece indirilen dosya kaydedilir.\n"
    "Yalnızca ses: videoyu değil sadece sesi indirir.\n\n"
    "Çalışır: YouTube, YouTube Music, Spotify, X, Instagram, TikTok, Pinterest, SoundCloud ve "
    "yt-dlp / gallery-dl'nin desteklediği siteler.\n"
    "Kısayol: Ctrl+Enter ile indirir."
)
_W, _H = 640, 250
_BOOKKEEPING = {"report.txt", "failed.json", "metadata.json", ".easyscrape-state.json"}


def _desktop() -> Path:
    for candidate in (Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"):
        if candidate.is_dir():
            return candidate
    return Path.home()


def _unique(target: Path) -> Path:
    candidate, n = target, 1
    while candidate.exists():
        candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
        n += 1
    return candidate


def _deliver(staging: Path, dest: Path) -> list[Path]:
    """Move only the downloaded media out of the staging tree, flat, into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for item in sorted(staging.rglob("*")):
        if item.is_file() and item.name not in _BOOKKEEPING:
            target = _unique(dest / item.name)
            shutil.move(str(item), target)
            moved.append(target)
    return moved


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"EasyScrape {__version__}")
        self.resizable(False, False)
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._files: list[str] = []
        self._staging: Path | None = None
        self._dest = _desktop()
        self._build()
        x = self.winfo_screenwidth() - _W - 12
        y = self.winfo_screenheight() - _H - 64  # clear of the taskbar
        self.geometry(f"{_W}x{_H}+{x}+{y}")
        self.bind("<Control-Return>", lambda _e: self._start())
        self.after(100, self._drain)

    def _build(self) -> None:
        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Linkler (satır başına bir tane)").pack(anchor="w")
        self.links = tk.Text(left, width=34, height=7, wrap="word", undo=True)
        self.links.pack(fill="both", expand=True, pady=(2, 0))
        self.links.focus_set()

        right = ttk.Frame(body)
        right.pack(side="left", fill="y", padx=(8, 0))
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Button(top, text="Dosya seç...", command=self._pick_files).pack(side="left")
        ttk.Button(
            top, text="i", width=2, command=lambda: messagebox.showinfo("EasyScrape", _HELP)
        ).pack(side="right")
        self.files_label = ttk.Label(right, text="Dosya yok", foreground="gray", width=28)
        self.files_label.pack(anchor="w", pady=2)

        ttk.Label(right, text="Kayıt klasörü").pack(anchor="w", pady=(6, 0))
        orow = ttk.Frame(right)
        orow.pack(fill="x")
        self.out = tk.StringVar(value=str(self._dest))
        ttk.Entry(orow, textvariable=self.out, width=22).pack(side="left")
        ttk.Button(orow, text="...", width=3, command=self._pick_out).pack(side="left", padx=2)

        self.audio = tk.BooleanVar()
        ttk.Checkbutton(right, text="Yalnızca ses", variable=self.audio).pack(anchor="w", pady=6)
        self.start_btn = ttk.Button(right, text="İndir", command=self._start)
        self.start_btn.pack(fill="x")

        self.log = tk.Text(self, height=5, state="disabled", wrap="none", font=("Consolas", 8))
        self.log.pack(fill="x", padx=8, pady=(0, 8))

    def _pick_files(self) -> None:
        chosen = filedialog.askopenfilenames(parent=self, filetypes=_FILE_TYPES)
        self._files = list(chosen)
        names = ", ".join(Path(p).name for p in self._files)
        self.files_label.configure(text=names or "Dosya yok", foreground="" if names else "gray")

    def _pick_out(self) -> None:
        chosen = filedialog.askdirectory(parent=self)
        if chosen:
            self.out.set(chosen)

    def _start(self) -> None:
        if self._proc is not None:
            return
        sources = _URL_RE.findall(self.links.get("1.0", "end")) + self._files
        if not sources:
            messagebox.showwarning("EasyScrape", "Link girin ya da dosya seçin.", parent=self)
            return
        self._dest = Path(self.out.get().strip() or str(_desktop())).expanduser()
        self._staging = Path(tempfile.mkdtemp(prefix="easyscrape-"))
        cmd = [sys.executable, "-m", "easyscrape", *sources, "--out", str(self._staging)]
        cmd.append("--no-metadata")
        if self.audio.get():
            cmd.append("--audio-only")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "COLUMNS": "100"}
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", env=env, creationflags=flags,
            )
        except OSError as exc:
            messagebox.showerror("EasyScrape", str(exc), parent=self)
            return
        self._clear_log()
        self._write("İndiriliyor...\n")
        self.start_btn.configure(state="disabled")
        threading.Thread(target=self._reader, args=(self._proc,), daemon=True).start()

    def _reader(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self._lines.put(line)
        proc.wait()
        self._lines.put(None)

    def _drain(self) -> None:
        try:
            while True:
                line = self._lines.get_nowait()
                if line is None:
                    code = self._proc.returncode if self._proc else 0
                    self._proc = None
                    self.start_btn.configure(state="normal")
                    self._finish(code)
                else:
                    self._write(line)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _finish(self, code: int) -> None:
        moved: list[Path] = []
        if self._staging is not None:
            try:
                moved = _deliver(self._staging, self._dest)
            except OSError as exc:
                self._write(f"Dosyalar taşınamadı: {exc}\n")
            shutil.rmtree(self._staging, ignore_errors=True)
            self._staging = None
        for path in moved:
            self._write(f"-> {path}\n")
        if moved:
            self._write(f"Bitti: {len(moved)} dosya {self._dest} klasörüne kaydedildi.\n")
        else:
            self._write("Hiç dosya indirilemedi.\n" if code else "Bitti.\n")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
