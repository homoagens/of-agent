#!/usr/bin/env python3
# main.py — OF-Agent entry point.
#
# Accepts both local paths and SSH targets — auto-detected from the argument.
#
# Usage (from of_agent/):
#   python main.py /path/to/openfoam/case
#   python main.py user@host:/remote/path
#   python main.py cfd@hpc.example.com:/scratch/pipe --key ~/.ssh/id_rsa
#   python main.py cfd@10.0.0.5:/runs/cavity --port 2222 --password secret

import argparse
import sys
from pathlib import Path

import config
from agent import AgentConfig, run_agent
from context import LocalContext, SSHContext
from session import SessionMemory
from prompts import SYSTEM_PROMPT, ACTIVITY_LABELS
from skills import (
    simulation_status,
    list_case_files,
    get_log_summary,
    tail_log,
    grep_log,
    read_residuals,
    read_courant_numbers,
    read_controlDict,
    list_timesteps,
    estimate_runtime,
    read_case_setup,
    confirm_with_user,
    modify_controlDict,
    read_file,
    write_file,
    patch_file,
    list_files,
    delete_file,
)

SKILLS = {
    "simulation_status":    simulation_status,
    "list_case_files":      list_case_files,
    "get_log_summary":      get_log_summary,
    "tail_log":             tail_log,
    "grep_log":             grep_log,
    "read_residuals":       read_residuals,
    "read_courant_numbers": read_courant_numbers,
    "read_controlDict":     read_controlDict,
    "list_timesteps":       list_timesteps,
    "estimate_runtime":     estimate_runtime,
    "read_case_setup":      read_case_setup,
    "confirm_with_user":    confirm_with_user,
    "modify_controlDict":   modify_controlDict,
    "read_file":            read_file,
    "write_file":           write_file,
    "patch_file":           patch_file,
    "list_files":           list_files,
    "delete_file":          delete_file,
}

_EXIT_COMMANDS = {"exit", "quit", "q", "bye"}

_SLASH_HELP = """\
Slash commands (executed instantly, no LLM call):
  /help            show this help
  /status          comprehensive simulation snapshot
  /log [N]         tail last N lines of the log (default 40)
  /residuals       residual trends per field
  /courant         Courant number history
  /eta             estimated remaining wall time
  /controlDict     show current system/controlDict
  /ls [path]       list files at a relative path (default: case root)
  /clear           wipe session memory (this case only)
  /save            force-save session memory now
  /debug           toggle DEBUG mode (verbose ReAct output)
  /exit, /quit     leave OF-Agent
Anything else is sent to the agent (free-form natural language)."""


def _handle_slash(cmd: str, ctx, mem: "SessionMemory") -> bool:
    """
    Handle a slash command. Returns True if the loop should continue,
    False if the user asked to quit.
    Prints output directly — does NOT touch session memory (commands are
    operational, not part of the diagnostic conversation).
    """
    from skills import (
        simulation_status, tail_log, read_residuals, read_courant_numbers,
        estimate_runtime, read_controlDict, list_files,
    )

    parts = cmd[1:].split(maxsplit=1)
    name  = parts[0].lower() if parts else ""
    arg   = parts[1] if len(parts) > 1 else ""

    if name in {"exit", "quit", "q", "bye"}:
        return False

    if name in {"help", "h", "?"}:
        print(_SLASH_HELP)
        return True

    if name == "status":
        print(simulation_status(context=ctx))
        return True

    if name == "log":
        try:
            n = int(arg) if arg else 40
        except ValueError:
            n = 40
        print(tail_log(context=ctx, n=n))
        return True

    if name == "residuals":
        print(read_residuals(context=ctx))
        return True

    if name in {"courant", "co"}:
        print(read_courant_numbers(context=ctx))
        return True

    if name == "eta":
        print(estimate_runtime(context=ctx))
        return True

    if name in {"controldict", "cd"}:
        print(read_controlDict(context=ctx))
        return True

    if name == "ls":
        print(list_files(context=ctx, path=arg or "."))
        return True

    if name == "clear":
        mem.clear()
        print("Session memory cleared for this case.")
        return True

    if name == "save":
        mem.save()
        print("Session memory saved.")
        return True

    if name == "debug":
        config.DEBUG = not config.DEBUG
        print(f"DEBUG mode: {'ON' if config.DEBUG else 'OFF'}")
        return True

    print(f"Unknown command: /{name}.  Type /help for the list.")
    return True


def _make_cfg(ctx) -> AgentConfig:
    return AgentConfig(
        name                = "of-agent",
        system_prompt       = SYSTEM_PROMPT,
        skills              = SKILLS,
        final_keys          = ("reply",),
        temperature         = 0.2,
        max_steps           = 12,
        skill_context       = ctx,
        skill_context_kwarg = "context",
        activity_labels     = ACTIVITY_LABELS,
    )


def _parse_ssh_target(target: str) -> tuple[str | None, str, str]:
    """Parse 'user@host:/path' or 'host:/path' into (user, hostname, remote_path)."""
    host_part, remote_path = target.split(":", 1)
    user, hostname = (host_part.rsplit("@", 1) if "@" in host_part
                      else (None, host_part))
    if not remote_path.startswith("/"):
        raise ValueError(f"Remote path must be absolute, got: {remote_path!r}")
    return user, hostname, remote_path


def _is_ssh_target(target: str) -> bool:
    """True if target looks like user@host:/path (contains ':' but is not a Windows drive)."""
    if ":" not in target:
        return False
    # On Windows a plain path like C:/foo has a single-char prefix before ':'
    colon_idx = target.index(":")
    return colon_idx > 1


def _repl(ctx, case_label: str) -> None:
    """Run the interactive REPL loop with the given context."""
    mem = SessionMemory(context=ctx, case_label=case_label)

    print()
    print("╔══════════════════════════════════════════════════════╗")
    if isinstance(ctx, SSHContext):
        print("║           OF-Agent  —  live SSH mode                ║")
    else:
        print("║           OF-Agent  —  live mode                    ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"  Monitoring : {ctx.resolve_str()}")
    if mem.loaded_from_file:
        print("  Previous session loaded - ask 'where did we leave off?' for a recap.")
    print("  Type a question, /help for slash commands, or 'exit' to quit.")
    print()

    try:
        while True:
            try:
                user_input = input("You > ").strip()
            except EOFError:
                print("\nExiting.")
                break

            if not user_input:
                continue

            if user_input.lower() in _EXIT_COMMANDS:
                print("OF-Agent: goodbye.")
                break

            # Slash commands: handled directly, no LLM call.
            if user_input.startswith("/"):
                print()
                if not _handle_slash(user_input, ctx, mem):
                    print("OF-Agent: goodbye.")
                    break
                print()
                continue

            cfg    = _make_cfg(ctx)
            result = run_agent(cfg, user_task=user_input,
                               initial_messages=mem.as_messages())

            if result is None:
                reply = "(agent returned no response)"
            else:
                reply = result.get("reply") or "(empty response)"

            mem.add(user_input, reply)
            print(f"\nOF-Agent: {reply}\n")
    finally:
        mem.save()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OF-Agent: monitor a live OpenFOAM simulation (local or SSH).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python main.py /home/user/runs/motorBike
  python main.py cfd@hpc.example.com:/scratch/runs/cavity
  python main.py cfd@10.0.0.5:/runs/cavity --port 2222
  python main.py user@10.0.0.5:/home/user/case --password mypass
        """,
    )
    parser.add_argument(
        "target",
        help="Local path to case directory, or user@host:/remote/path for SSH",
    )
    parser.add_argument("--port",     type=int,   default=22,
                        help="SSH port (default: 22)")
    parser.add_argument("--key",      default=None, metavar="PATH",
                        help="SSH private key file")
    parser.add_argument("--password", default=None,
                        help="SSH password")
    parser.add_argument("--timeout",  type=float, default=30.0,
                        help="SSH connection timeout in seconds (default: 30)")
    args = parser.parse_args()

    if _is_ssh_target(args.target):
        # ── SSH mode ──────────────────────────────────────────────────
        try:
            user, hostname, remote_path = _parse_ssh_target(args.target)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

        user_display = f"{user}@" if user else ""
        print(f"\nConnecting to {user_display}{hostname}:{args.port} ...")

        try:
            ctx = SSHContext(
                hostname     = hostname,
                remote_root  = remote_path,
                user         = user,
                port         = args.port,
                key_filename = args.key,
                password     = args.password,
                timeout      = args.timeout,
            )
        except ImportError as e:
            print(f"\nERROR: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"\nERROR: SSH connection failed: {e}", file=sys.stderr)
            sys.exit(1)

        case_label = f"{user_display}{hostname}:{remote_path}"

    else:
        # ── Local mode ────────────────────────────────────────────────
        case_path = Path(args.target).resolve()
        if not case_path.is_dir():
            print(f"ERROR: '{case_path}' is not a valid directory.", file=sys.stderr)
            sys.exit(1)
        ctx        = LocalContext(case_path)
        case_label = str(case_path)

    with ctx:
        _repl(ctx, case_label)


if __name__ == "__main__":
    main()
