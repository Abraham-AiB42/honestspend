"""CLI entrypoints."""

from __future__ import annotations

import argparse
import sys

import uvicorn

from financial_os.config import settings
from financial_os.db import init_db, make_engine, make_session_factory
from financial_os.seed import seed_all


def cmd_init_db(_args: argparse.Namespace) -> int:
    engine = make_engine()
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        seed_all(session)
        print(f"Database ready at {settings.db_path}")
        print("Profiles: Personal (default). Add Business / Add Child via API or desktop app.")
        print("Tax COA seeded (1120-S / Sch C / Sch A maps).")
    finally:
        session.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    # Ensure DB + tax COA (no Excel dependency)
    cmd_init_db(args)
    if settings.seed_demo:
        from financial_os.services.demo_seed import seed_demo_if_empty

        engine = make_engine()
        session = make_session_factory(engine)()
        try:
            if seed_demo_if_empty(session):
                session.commit()
                print("Seeded demo accounts (FOS_SEED_DEMO=1).")
        finally:
            session.close()
    else:
        print("HonestSpend ready — complete setup in the app (no spreadsheet required).")

    print(f"Open http://{args.host or settings.host}:{args.port or settings.port}")
    uvicorn.run(
        "financial_os.api.app:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
    )
    return 0


def cmd_import_xlsx(args: argparse.Namespace) -> int:
    from financial_os.services.excel_import import import_budget_xlsx

    engine = make_engine()
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        seed_all(session)
        result = import_budget_xlsx(
            session,
            args.path,
            profile_slug=args.profile,
            sheet_name=args.sheet,
            since=args.since,
            dry_run=args.dry_run,
        )
        session.commit()
        print(
            f"scanned={result.rows_scanned} created={result.transactions_created} "
            f"skip_empty={result.skipped_empty} skip_existing={result.skipped_existing}"
        )
        if result.date_from:
            print(f"range={result.date_from} .. {result.date_to}")
        for e in result.errors:
            print(f"ERROR: {e}")
        return 1 if result.errors else 0
    finally:
        session.close()


def cmd_tax_packet(args: argparse.Namespace) -> int:
    from financial_os.db import Profile
    from financial_os.services.tax_packet import write_tax_packet_dir

    engine = make_engine()
    session = make_session_factory(engine)()
    try:
        profile = session.query(Profile).filter(Profile.slug == args.profile).first()
        if not profile:
            print(f"Profile not found: {args.profile}")
            return 1
        out = write_tax_packet_dir(
            session, profile.id, args.year, settings.data_dir / "exports"
        )
        print(f"Wrote {out}")
        return 0
    finally:
        session.close()


def cmd_tray(args: argparse.Namespace) -> int:
    from financial_os.tray import run_tray

    run_tray(poll_seconds=args.poll)
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Create backup / run scheduled auto-backup (Task Scheduler friendly)."""
    import json

    from financial_os.services.backup import create_backup, maybe_auto_backup, prune_backups

    engine = make_engine()
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        if args.force or args.auto:
            if args.auto and not args.force:
                result = maybe_auto_backup(session, force=False)
                if result is None:
                    print(json.dumps({"ok": True, "skipped": True, "reason": "not_due"}))
                    return 0
                session.commit()
                print(json.dumps(result, default=str))
                return 0 if result.get("ok") else 1
            note = args.note or ("auto" if args.auto else "cli")
            result = create_backup(as_zip=True, note=note)
            if args.keep:
                pruned = prune_backups(int(args.keep))
                result["pruned"] = pruned
            # stamp last_at when --auto
            if args.auto:
                from datetime import datetime, timezone

                from financial_os.db import AppSettings

                row = session.get(AppSettings, 1)
                if row:
                    row.auto_backup_last_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
            print(json.dumps(result, default=str))
            return 0
        # default: force create
        result = create_backup(as_zip=True, note=args.note or "cli")
        session.commit()
        print(json.dumps(result, default=str))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    finally:
        session.close()


def cmd_health(_args: argparse.Namespace) -> int:
    """Exit 0 if API healthy on configured host/port."""
    import httpx

    url = f"http://{settings.host}:{settings.port}/api/health"
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get(url)
            if r.is_success:
                print(r.text)
                return 0
            print(f"unhealthy {r.status_code}")
            return 1
    except Exception as e:
        print(f"offline: {e}")
        return 1


def cmd_digest(_args: argparse.Namespace) -> int:
    """Print daily digest (for cron / Task Scheduler)."""
    import json

    from financial_os.services.digest import build_digest

    engine = make_engine()
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        seed_all(session)
        dig = build_digest(session)
        print(json.dumps(dig, indent=2))
        # Non-zero exit if critical alerts (automation hooks)
        critical = [a for a in dig.get("alerts") or [] if a.get("level") == "critical"]
        return 2 if critical else 0
    finally:
        session.close()


def cmd_token(args: argparse.Namespace) -> int:
    """Mint or rotate API token for a user (multi-client)."""
    from financial_os.db import AppUser
    from financial_os.services.permissions import generate_api_token

    engine = make_engine()
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        seed_all(session)
        user = session.query(AppUser).filter(AppUser.username == args.username).first()
        if not user:
            if not args.create:
                print(f"User '{args.username}' not found. Use --create.")
                return 1
            from financial_os.services.permissions import Role

            role = args.role or "owner"
            if role not in {r.value for r in Role}:
                print(f"Invalid role: {role}")
                return 1
            user = AppUser(
                username=args.username,
                display_name=args.display_name or args.username,
                role=role,
                active=True,
            )
            session.add(user)
            session.flush()
        token = generate_api_token()
        user.api_token = token
        session.commit()
        print(f"username={user.username}")
        print(f"role={user.role}")
        print(f"api_token={token}")
        print("Send header: X-API-Key: <token>")
        return 0
    finally:
        session.close()


def cmd_version(_args: argparse.Namespace) -> int:
    from financial_os import __version__

    print(f"HonestSpend {__version__}")
    return 0


def cmd_import_inbox(args: argparse.Namespace) -> int:
    """Process data_dir/inbox CSV drops into accounts."""
    from financial_os.services.import_inbox import ensure_inbox_layout, process_inbox

    engine = make_engine()
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        layout = ensure_inbox_layout()
        if args.show_path:
            print(layout["inbox"])
            return 0
        result = process_inbox(
            session,
            default_account_id=args.account_id,
            auto_categorize=not args.no_categorize,
            amount_sign=args.sign,
            dry_run=args.dry_run,
        )
        session.commit()
        import json

        print(json.dumps(result, indent=2, default=str))
        if result.get("files_seen", 0) == 0:
            return 0
        # exit 1 if any hard failure with no creates
        bad = [r for r in result.get("results") or [] if not r.get("ok")]
        if bad and result.get("transactions_created", 0) == 0 and not args.dry_run:
            return 1
        return 0
    finally:
        session.close()


def cmd_glance(args: argparse.Namespace) -> int:
    """Print glance JSON or open the multi-platform glance UI in a browser."""
    import json
    import webbrowser

    import httpx

    host = args.host or settings.host
    port = args.port or settings.port
    base = f"http://{host}:{port}"
    headers = {}
    if args.api_key:
        headers["X-API-Key"] = args.api_key

    if args.open:
        url = f"{base}/glance"
        print(f"Opening {url}")
        webbrowser.open(url)
        return 0

    params = {}
    if args.scope:
        params["scope"] = args.scope
    if args.profile_id:
        params["profile_id"] = args.profile_id
    try:
        r = httpx.get(f"{base}/api/glance", params=params, headers=headers, timeout=10.0)
        r.raise_for_status()
    except Exception as e:
        print(f"glance failed: {e}", file=sys.stderr)
        print(f"Is the engine up? honestspend serve → {base}", file=sys.stderr)
        return 1
    print(json.dumps(r.json(), indent=2))
    return 0


def cmd_home(args: argparse.Namespace) -> int:
    """Print Simple Home JSON (Safe to spend + Do this next + 3-minute check)."""
    import json

    import httpx

    host = args.host or settings.host
    port = args.port or settings.port
    base = f"http://{host}:{port}"
    headers = {}
    if args.api_key:
        headers["X-API-Key"] = args.api_key
    params = {}
    if args.scope:
        params["scope"] = args.scope
    if args.profile_id:
        params["profile_id"] = args.profile_id
    try:
        r = httpx.get(f"{base}/api/home/simple", params=params, headers=headers, timeout=15.0)
        r.raise_for_status()
    except Exception as e:
        print(f"home failed: {e}", file=sys.stderr)
        print(f"Is the engine up? honestspend serve → {base}", file=sys.stderr)
        return 1
    body = r.json()
    if args.brief:
        next_ = body.get("do_this_next") or {}
        ritual = body.get("three_minute_check") or {}
        print(f"Safe to spend: ${body.get('safe_to_spend')}  [{body.get('status_label')}]")
        print(f"Do this next:  {next_.get('title')}")
        if next_.get("reason"):
            print(f"  {next_['reason']}")
        if ritual:
            print(f"3-min check:   {ritual.get('progress_label')} — {ritual.get('subtitle')}")
        return 0
    print(json.dumps(body, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="honestspend", description="HonestSpend liquidity cockpit")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="Create DB and seed tax COA")
    p_init.set_defaults(func=cmd_init_db)

    p_serve = sub.add_parser("serve", help="Run local API + UI")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_imp = sub.add_parser("import-xlsx", help="Legacy one-time xlsx migration")
    p_imp.add_argument("path", help="Path to old .xlsx")
    p_imp.add_argument("--profile", default="personal")
    p_imp.add_argument("--sheet", default="Budget")
    p_imp.add_argument("--since", type=lambda s: __import__("datetime").date.fromisoformat(s), default=None)
    p_imp.add_argument("--dry-run", action="store_true")
    p_imp.set_defaults(func=cmd_import_xlsx)

    p_inbox = sub.add_parser(
        "import-inbox",
        help="Import bank CSVs from data_dir/inbox (freeware drop folder)",
    )
    p_inbox.add_argument("--account-id", type=int, default=None, help="Fallback account if name match fails")
    p_inbox.add_argument("--sign", default="bank", choices=["bank", "invert"])
    p_inbox.add_argument("--no-categorize", action="store_true")
    p_inbox.add_argument("--dry-run", action="store_true")
    p_inbox.add_argument("--show-path", action="store_true", help="Print inbox folder path and exit")
    p_inbox.set_defaults(func=cmd_import_inbox)

    p_tax = sub.add_parser("tax-packet", help="Write tax packet to disk")
    p_tax.add_argument("--profile", default="personal")
    p_tax.add_argument("--year", type=int, default=None)
    p_tax.set_defaults(func=lambda a: cmd_tax_packet(_with_year(a)))

    p_tray = sub.add_parser("tray", help="System tray Safe to spend")
    p_tray.add_argument("--poll", type=int, default=60, help="Refresh seconds")
    p_tray.set_defaults(func=cmd_tray)

    p_dig = sub.add_parser("digest", help="Print daily digest JSON (exit 2 if critical)")
    p_dig.set_defaults(func=cmd_digest)

    p_bak = sub.add_parser("backup", help="Create local DB backup (JSON stdout)")
    p_bak.add_argument("--auto", action="store_true", help="Respect schedule unless --force")
    p_bak.add_argument("--force", action="store_true", help="Always create backup")
    p_bak.add_argument("--note", default=None)
    p_bak.add_argument("--keep", type=int, default=None, help="Prune to N backups after create")
    p_bak.set_defaults(func=cmd_backup)

    p_hl = sub.add_parser("health", help="Check local API /api/health (exit 1 if down)")
    p_hl.set_defaults(func=cmd_health)

    p_tok = sub.add_parser("token", help="Mint/rotate API token for a user")
    p_tok.add_argument("username", nargs="?", default="owner")
    p_tok.add_argument("--create", action="store_true", help="Create user if missing")
    p_tok.add_argument("--role", default="owner")
    p_tok.add_argument("--display-name", default=None)
    p_tok.set_defaults(func=cmd_token)

    p_ver = sub.add_parser("version", help="Print version")
    p_ver.set_defaults(func=cmd_version)

    p_home = sub.add_parser("home", help="Simple Home JSON (Safe to spend + Do this next)")
    p_home.add_argument("--host", default=None)
    p_home.add_argument("--port", type=int, default=None)
    p_home.add_argument("--api-key", default=None)
    p_home.add_argument("--scope", default=None, choices=["entity", "group"])
    p_home.add_argument("--profile-id", type=int, default=None)
    p_home.add_argument(
        "--brief",
        action="store_true",
        help="One-screen plain summary instead of full JSON",
    )
    p_home.set_defaults(func=cmd_home)

    p_gl = sub.add_parser("glance", help="Mobile/Mac/Linux glance JSON or open UI")
    p_gl.add_argument("--open", action="store_true", help="Open /glance in browser")
    p_gl.add_argument("--host", default=None)
    p_gl.add_argument("--port", type=int, default=None)
    p_gl.add_argument("--api-key", default=None)
    p_gl.add_argument("--scope", default="entity")
    p_gl.add_argument("--profile-id", type=int, default=None)
    p_gl.set_defaults(func=cmd_glance)

    args = parser.parse_args(argv)
    return args.func(args)


def _with_year(args: argparse.Namespace) -> argparse.Namespace:
    if args.year is None:
        from datetime import date as _d

        args.year = _d.today().year
    return args


if __name__ == "__main__":
    sys.exit(main())
