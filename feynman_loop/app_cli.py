"""The `feynman-loop` console command: one entry point for every surface.

  feynman-loop init            set up MCP (Claude Desktop + generic snippet) and Claude Code hooks
  feynman-loop due [...]       what's due + learner profile (same flags as feynman_loop.due)
  feynman-loop check SRC NAME  terminal explain-back on a source file
  feynman-loop web [--port]    the web UI (binds localhost only; it is single-user by design)
  feynman-loop mcp             run the MCP server on stdio (what host configs invoke)
  feynman-loop mode [MODE]     show, or set, how proactive the loop is (off|nudge|commit)
  feynman-loop scope [...]     which projects the proactive hooks fire in (default: all)
"""

from __future__ import annotations

import argparse
import sys


def _needs_key() -> bool:
    """The terminal and web judges call the API directly, so they need a key. Zero-key mode
    lives in the MCP surface, where the host model does the judging under verification."""
    from feynman_loop import providers

    if providers.has_api_key():
        return False
    print("This surface needs ANTHROPIC_API_KEY (it judges via the API directly).\n"
          "Zero-key mode works in MCP hosts (Claude Desktop, Claude Code, Cursor, ChatGPT):\n"
          "run `feynman-loop init` and use the tools from your chat instead.")
    return True


def _needs_embeddings() -> bool:
    """Web/CLI retrieval uses the vector stack; MCP grounds directly and doesn't need it."""
    try:
        import chromadb  # noqa: F401

        return False
    except ImportError:
        print('This surface needs the embeddings extra: pip install "feynman-loop[embeddings]"')
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="feynman-loop", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="configure MCP + hooks")
    p_init.add_argument("--key", default=None, help="Anthropic API key (else $ANTHROPIC_API_KEY)")
    p_init.add_argument("--notifications", action="store_true",
                        help="install a daily macOS notification with the top due question (opt-in)")

    p_due = sub.add_parser("due", help="what's due + learner profile")
    p_due.add_argument("--context", action="store_true")
    p_due.add_argument("--quiet", action="store_true")
    p_due.add_argument("--notify", action="store_true")

    p_check = sub.add_parser("check", help="terminal explain-back")
    p_check.add_argument("source")
    p_check.add_argument("concept")

    p_web = sub.add_parser("web", help="run the web UI (localhost)")
    p_web.add_argument("--port", type=int, default=8000)

    sub.add_parser("mcp", help="run the MCP server (stdio)")

    p_exp = sub.add_parser("export", help="dump the full ledger as JSON (backup/portability)")
    p_exp.add_argument("--out", default=None, help="write to a file instead of stdout")

    p_mode = sub.add_parser("mode", help="show or set the engagement mode")
    p_mode.add_argument("mode", nargs="?", default=None,
                        help="off (silent), nudge (offer; default), commit (self-armed gate)")

    p_scope = sub.add_parser("scope", help="which projects the proactive hooks fire in")
    p_scope.add_argument("action", nargs="?", default=None,
                         help="add | remove | all  (omit to show the current scope)")
    p_scope.add_argument("path", nargs="?", default=None,
                         help="project directory for add/remove (default: current directory)")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        from feynman_loop.installer import run_init
        return run_init(api_key=args.key, notifications=args.notifications)
    if args.cmd == "due":
        from feynman_loop.due import main as due_main
        flags = ((["--context"] if args.context else []) + (["--quiet"] if args.quiet else [])
                 + (["--notify"] if args.notify else []))
        return due_main(flags)
    if args.cmd == "check":
        if _needs_key() or _needs_embeddings():
            return 1
        from feynman_loop.cli import main as check_main
        return check_main(["feynman-loop", args.source, args.concept])
    if args.cmd == "web":
        if _needs_key() or _needs_embeddings():
            return 1
        import uvicorn
        # WHY localhost only: the web app is single-user (one local ledger) and unauthenticated;
        # exposing it on a public interface would share one identity and one API key with everyone.
        uvicorn.run("feynman_loop.web.app:app", host="127.0.0.1", port=args.port)
        return 0
    if args.cmd == "mcp":
        from feynman_loop.mcp_server import mcp
        mcp.run()
        return 0
    if args.cmd == "export":
        import json
        from pathlib import Path

        from feynman_loop import paths
        from feynman_loop.db import export_ledger

        dump = json.dumps(export_ledger(paths.home()), indent=2)
        if args.out:
            Path(args.out).write_text(dump)
            print(f"wrote {args.out}")
        else:
            print(dump)
        return 0
    if args.cmd == "mode":
        from feynman_loop import paths, settings

        root = paths.home()
        if args.mode is None:
            print(settings.get_mode(root))  # show current
            return 0
        try:
            settings.set_mode(root, args.mode)
        except ValueError as e:
            print(str(e))
            return 2
        blurb = {
            "off": "Proactive nudges and the daily notification are now silent. "
                   "Explicit `feynman-loop due` still works.",
            "nudge": "At a natural moment, sessions will OFFER an explain-back. Never forced.",
            "commit": "Self-armed gate ON. At session end, if you shipped unexplained AI-written "
                      "code, you will be asked to explain it before wrapping up. You can still "
                      "decline, and it never traps you. Relax it with `feynman-loop mode nudge`.",
        }[args.mode]
        print(f"mode set to {args.mode}. {blurb}")
        return 0
    if args.cmd == "scope":
        import os

        from feynman_loop import paths, settings

        root = paths.home()
        act = args.action
        if act is None:
            scope = settings.get_scope(root)
            if not scope:
                print("scope: all projects (proactive hooks fire everywhere).")
            else:
                print("scope: proactive hooks fire ONLY in these projects:")
                for p in scope:
                    print(f"  {p}")
            return 0
        if act == "all":
            settings.set_scope_all(root)
            print("scope: all projects (proactive hooks fire everywhere).")
            return 0
        if act in ("add", "remove"):
            target = args.path or os.getcwd()  # default to the current directory for convenience
            if act == "add":
                stored = settings.add_scope_path(root, target)
                print(f"scope: added {stored}.\nProactive hooks now fire ONLY in your allowlisted "
                      "projects. See them with `feynman-loop scope`; reset with `feynman-loop scope all`.")
            else:
                settings.remove_scope_path(root, target)
                print(f"scope: removed {settings._norm(target)}.")
            return 0
        print("usage: feynman-loop scope [add|remove|all] [PATH]")
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
