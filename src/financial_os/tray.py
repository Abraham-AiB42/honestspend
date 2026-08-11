"""Windows system tray — always-on Safe to spend + digest alerts."""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from decimal import Decimal

import httpx

from financial_os.config import settings


def _base() -> str:
    return f"http://{settings.host}:{settings.port}"


def _get(path: str) -> dict | list | None:
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{_base()}{path}")
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


def _money(v: str | float | Decimal) -> str:
    try:
        return f"${Decimal(str(v)):,.0f}"
    except Exception:
        return str(v)


def run_tray(*, poll_seconds: int = 60) -> None:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError as e:
        raise SystemExit(
            "Tray requires: pip install pystray pillow\n"
            f"Import error: {e}"
        ) from e

    from financial_os.services.paths_util import (
        clear_pid,
        pid_alive,
        read_pid,
        tray_pid_path,
        write_pid,
    )

    pid_file = tray_pid_path()
    existing = read_pid(pid_file)
    if existing and pid_alive(existing) and existing != __import__("os").getpid():
        raise SystemExit(f"Tray already running (pid {existing}). Quit the existing tray first.")
    write_pid(pid_file)

    state: dict = {
        "title": "HonestSpend — starting…",
        "last_critical_key": None,
        "offline_notified": False,
    }

    def make_icon_image(alert: bool = False) -> Image.Image:
        bg = (180, 60, 50) if alert else (45, 180, 160)
        img = Image.new("RGB", (64, 64), color=(18, 26, 36))
        draw = ImageDraw.Draw(img)
        draw.ellipse((4, 4, 60, 60), fill=bg)
        draw.text((22, 18), "L", fill=(11, 15, 20))
        return img

    def _winui_candidates() -> list:
        from pathlib import Path

        candidates: list[Path] = []
        # Pointer file written by WinUI on launch
        try:
            data = Path(settings.data_dir)
            pointer = data / "winui.path"
            if pointer.is_file():
                line = pointer.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
                if line:
                    candidates.append(Path(line[0].strip()))
        except Exception:
            pass
        for env_key in ("HONESTSPEND_WINUI", "FLOATPILE_WINUI", "FOS_WINUI"):
            env_exe = os.environ.get(env_key)
            if env_exe:
                candidates.insert(0, Path(env_exe))
                break
        cwd = Path.cwd()
        candidates += [
            cwd / "winui.path",
            cwd.parent / "winui.path",
            cwd / "HonestSpend.WinUI.exe",
            cwd.parent / "HonestSpend.WinUI.exe",
            # prior brand EXEs still open until user reinstalls
            cwd / "Floatpile.WinUI.exe",
            cwd.parent / "Floatpile.WinUI.exe",
        ]
        if cwd.name.lower() == "engine":
            candidates.insert(0, cwd.parent / "HonestSpend.WinUI.exe")
        resolved: list[Path] = []
        for c in candidates:
            try:
                if c.name.lower() == "winui.path" and c.is_file():
                    p = Path(
                        c.read_text(encoding="utf-8", errors="ignore")
                        .strip()
                        .splitlines()[0]
                        .strip()
                    )
                    resolved.append(p)
                else:
                    resolved.append(c)
            except Exception:
                continue
        return resolved

    def open_desktop(icon=None, item=None, page: str | None = None):
        """Client-first: launch WinUI if present; never prefer PWA.

        Optional page deep-link (e.g. review, reports, settings) via --page.
        """
        import subprocess
        from pathlib import Path

        # Write navigate request so a running single-instance WinUI can switch page
        if page:
            try:
                data = Path(settings.data_dir)
                data.mkdir(parents=True, exist_ok=True)
                (data / "winui.navigate").write_text(page.strip().lower(), encoding="utf-8")
            except Exception:
                pass

        for exe in _winui_candidates():
            try:
                if exe.is_file() and exe.suffix.lower() in (".exe", ""):
                    args = [str(exe)]
                    if page:
                        args += ["--page", page]
                    subprocess.Popen(args, cwd=str(exe.parent), close_fds=True)
                    return
            except Exception:
                continue
        # Last resort: docs — not Glance as primary
        webbrowser.open(f"{_base()}/docs")

    def open_sort_charges(icon=None, item=None):
        open_desktop(page="review")

    def open_reports(icon=None, item=None):
        open_desktop(page="reports")

    def open_settings(icon=None, item=None):
        open_desktop(page="settings")

    def open_import(icon=None, item=None):
        open_desktop(page="import")

    def open_inbox_folder(icon=None, item=None):
        """Open the bank CSV drop folder in Explorer."""
        import subprocess
        from pathlib import Path

        try:
            from financial_os.services.import_inbox import ensure_inbox_layout

            layout = ensure_inbox_layout()
            path = layout["inbox"]
            Path(path).mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                subprocess.Popen(["explorer", path], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", path], close_fds=True)
        except Exception:
            open_import()

    def run_import_inbox(icon=None, item=None):
        """Process inbox CSVs via local API (or offline CLI fallback)."""
        try:
            with httpx.Client(timeout=120.0) as client:
                r = client.post(f"{_base()}/api/import/inbox/process", json={})
                if r.status_code < 400:
                    data = r.json()
                    n = data.get("transactions_created", 0)
                    seen = data.get("files_seen", 0)
                    msg = f"Inbox: {seen} file(s) · {n} new transactions"
                    if icon:
                        notify(icon, "HonestSpend · import", msg)
                    refresh_now(icon)
                    return
        except Exception:
            pass
        # Offline: only if books are not encrypted/sealed
        try:
            from financial_os.db import init_db, make_engine, make_session_factory
            from financial_os.services.db_crypto import refuse_offline_open_if_encrypted
            from financial_os.services.import_inbox import process_inbox

            refuse_offline_open_if_encrypted()
            eng = make_engine()
            init_db(eng)
            Session = make_session_factory(eng)
            with Session() as s:
                data = process_inbox(s)
                s.commit()
            n = data.get("transactions_created", 0)
            seen = data.get("files_seen", 0)
            if icon:
                notify(icon, "HonestSpend · import", f"Inbox: {seen} file(s) · {n} new")
        except RuntimeError as e:
            if icon:
                notify(icon, "HonestSpend · import", str(e)[:120])
        except Exception as e:
            if icon:
                notify(icon, "HonestSpend · import", f"Failed: {e}")

    def open_glance(icon=None, item=None):
        # Thin shell only — not the product
        webbrowser.open(f"{_base()}/glance")

    def open_plaid(icon=None, item=None):
        webbrowser.open(f"{_base()}/static/plaid-link.html")

    def open_docs(icon=None, item=None):
        webbrowser.open(f"{_base()}/docs")

    def notify(icon, title: str, message: str) -> None:
        try:
            icon.notify(message, title)
        except Exception:
            pass

    def refresh_now(icon=None, item=None):
        data = _get("/api/ifpp")
        if not data:
            state["title"] = "HonestSpend — server offline\nStart: financial-os serve"
            if icon:
                icon.title = state["title"]
                try:
                    icon.icon = make_icon_image(alert=True)
                except Exception:
                    pass
                if not state["offline_notified"]:
                    notify(icon, "HonestSpend", "Engine offline on :7420")
                    state["offline_notified"] = True
            return

        state["offline_notified"] = False
        combined = _money(data.get("combined_purchasing_power", 0))
        cash = _money(data.get("cash_spendable", 0))
        card = _money(data.get("card_float_interest_free", 0))
        red = data.get("next_red_day") or "none"
        mode = data.get("mode", "")

        dig = _get("/api/digest") or {}
        alerts = dig.get("alerts") if isinstance(dig, dict) else None
        alerts = alerts or []
        critical = [a for a in alerts if a.get("level") == "critical"]
        warn = [a for a in alerts if a.get("level") == "warn"]
        alert_line = dig.get("message") if isinstance(dig, dict) else ""
        if not alert_line:
            alert_line = "All clear" if not alerts else f"{len(alerts)} alert(s)"

        state["title"] = (
            f"Safe to spend {combined}\n"
            f"Cash {cash} · Can charge {card}\n"
            f"Next risk {red}\n"
            f"{alert_line}"
        )
        if icon:
            icon.title = state["title"]
            try:
                icon.icon = make_icon_image(alert=bool(critical))
            except Exception:
                pass

            # Notify once per distinct critical set
            if critical:
                key = "|".join(sorted(a.get("code", "") + a.get("message", "") for a in critical))
                if key != state["last_critical_key"]:
                    state["last_critical_key"] = key
                    msg = critical[0].get("message", "Critical alert")
                    if len(critical) > 1:
                        msg += f" (+{len(critical) - 1} more)"
                    notify(icon, "HonestSpend · action needed", msg)
            else:
                state["last_critical_key"] = None
                if warn and item is not None:
                    # only on manual refresh for warnings
                    notify(icon, "HonestSpend", warn[0].get("message", "Warning"))

    def on_exit(icon, item):
        clear_pid(pid_file)
        icon.stop()

    def poll_loop(icon):
        while icon.visible:
            refresh_now(icon)
            time.sleep(poll_seconds)

    menu = pystray.Menu(
        pystray.MenuItem("Open HonestSpend (desktop)", open_desktop, default=True),
        pystray.MenuItem("Sort charges", open_sort_charges),
        pystray.MenuItem("Import (bank CSV)", open_import),
        pystray.MenuItem("Open inbox folder", open_inbox_folder),
        pystray.MenuItem("Import inbox now", run_import_inbox),
        pystray.MenuItem("Reports", open_reports),
        pystray.MenuItem("Settings", open_settings),
        pystray.MenuItem("Refresh Safe to spend", refresh_now),
        pystray.MenuItem("Link bank (browser)", open_plaid),
        pystray.MenuItem("API docs", open_docs),
        pystray.MenuItem("Glance (browser fallback)", open_glance),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit tray", on_exit),
    )
    icon = pystray.Icon(
        "honestspend",
        make_icon_image(),
        "HonestSpend",
        menu,
    )

    def setup(icon):
        icon.visible = True
        refresh_now(icon)
        t = threading.Thread(target=poll_loop, args=(icon,), daemon=True)
        t.start()

    print(f"HonestSpend tray — polling {_base()}/api/ifpp + digest")
    print("Hover for Safe to spend. Critical alerts toast once.")
    icon.run(setup=setup)
