"""Windows system tray — always-on Spendable Now + digest alerts."""

from __future__ import annotations

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
        "title": "LedgerRing — starting…",
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

    def open_app(icon=None, item=None):
        # Primary client is WinUI — browser is fallback / Plaid only
        webbrowser.open(f"{_base()}/")

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
            state["title"] = "LedgerRing — server offline\nStart: financial-os serve"
            if icon:
                icon.title = state["title"]
                try:
                    icon.icon = make_icon_image(alert=True)
                except Exception:
                    pass
                if not state["offline_notified"]:
                    notify(icon, "LedgerRing", "Engine offline on :7420")
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
            f"Spendable {combined}\n"
            f"Cash {cash} · Card float {card}\n"
            f"Mode {mode} · Red day {red}\n"
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
                    notify(icon, "LedgerRing · action needed", msg)
            else:
                state["last_critical_key"] = None
                if warn and item is not None:
                    # only on manual refresh for warnings
                    notify(icon, "LedgerRing", warn[0].get("message", "Warning"))

    def on_exit(icon, item):
        clear_pid(pid_file)
        icon.stop()

    def poll_loop(icon):
        while icon.visible:
            refresh_now(icon)
            time.sleep(poll_seconds)

    menu = pystray.Menu(
        pystray.MenuItem("Refresh Spendable + digest", refresh_now, default=True),
        pystray.MenuItem("Plaid Link (browser)", open_plaid),
        pystray.MenuItem("API docs", open_docs),
        pystray.MenuItem("Web fallback UI", open_app),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit tray", on_exit),
    )
    icon = pystray.Icon(
        "lederring",
        make_icon_image(),
        "LedgerRing",
        menu,
    )

    def setup(icon):
        icon.visible = True
        refresh_now(icon)
        t = threading.Thread(target=poll_loop, args=(icon,), daemon=True)
        t.start()

    print(f"LedgerRing tray — polling {_base()}/api/ifpp + digest")
    print("Hover for Spendable. Critical alerts toast once.")
    icon.run(setup=setup)
