# skills.py — OpenFOAM-specific skills for the OF-Agent.
#
# All public functions accept `context` as their first kwarg.
# `context` is a LocalContext or SSHContext (see context.py).
# A bare pathlib.Path is also accepted and coerced automatically via _coerce().
#
# All functions return plain strings — the LLM reads text, not dicts.
#
# Safety contract for modify_controlDict:
#   - Only keys in _ALLOWED_KEYS are writable.
#   - Values are validated before any write.
#   - A .bak backup is always created before modifying.
#   - No file outside <case_root>/system/controlDict is ever written.

import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from context import _coerce


# ─── internal helpers ────────────────────────────────────────────────────────

def _find_log(ctx) -> Optional[str]:
    """
    Return the filename (relative to case root) of the most recently
    modified log.* file, or None if no log exists.
    """
    logs = ctx.glob_logs()   # [(filename, mtime), ...]
    if not logs:
        return None
    logs.sort(key=lambda x: x[1], reverse=True)
    return logs[0][0]


def _read_log_text(ctx) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Return (log_filename, text, error_string).
    On success: error_string is None.
    On failure: log_filename and/or text may be None.
    """
    log_name = _find_log(ctx)
    if log_name is None:
        return None, None, "No log file found in case directory."
    try:
        text = ctx.read_text(log_name)
        return log_name, text, None
    except OSError as e:
        return log_name, None, f"Cannot read {log_name}: {e}"


def _detect_solver(text: str) -> Optional[str]:
    """Extract solver name from the log header line 'Exec   : solverName ...'"""
    m = re.search(r"Exec\s*:\s*(\S+)", text)
    if m:
        return Path(m.group(1)).name   # strip path prefix if present
    return None


# ─── public skills ───────────────────────────────────────────────────────────

def list_case_files(context) -> str:
    """List the top-level structure of the OpenFOAM case directory."""
    ctx = _coerce(context)

    lines = [f"Case root: {ctx.resolve_str()}"]
    for name, is_dir in ctx.listdir("."):
        if is_dir:
            children = [n for n, _ in ctx.listdir(name)]
            preview  = ", ".join(children[:8])
            if len(children) > 8:
                preview += ", ..."
            lines.append(f"  {name}/  [{preview}]")
        else:
            lines.append(f"  {name}")
    return "\n".join(lines)


def get_log_summary(context) -> str:
    """
    Read the log header (first 60 lines) to identify solver, version,
    run date, and host.  MUST be called first to confirm solver identity.
    """
    ctx = _coerce(context)
    log_name, text, err = _read_log_text(ctx)
    if err:
        return f"ERROR: {err}"

    header = text.splitlines()[:60]
    solver = _detect_solver("\n".join(header))

    solver_line = (
        f"Solver detected: {solver}"
        if solver
        else "WARNING: solver could not be detected from log header."
    )
    return solver_line + f"\nLog file: {log_name}\n\nLog header:\n" + "\n".join(header)


def tail_log(context, n: int = 60) -> str:
    """
    Return the last N lines of the main log file.
    Uses an efficient byte-seek for large remote logs.
    """
    ctx = _coerce(context)
    log_name = _find_log(ctx)
    if log_name is None:
        return "ERROR: no log file found in case directory."

    try:
        # Read ~300 bytes per expected line — enough for any OpenFOAM output.
        raw   = ctx.tail_bytes(log_name, int(n) * 300)
        lines = raw.decode("utf-8", errors="replace").splitlines()
        # The first line may be a fragment (byte-seek landed mid-line) — drop it.
        if len(lines) > 1:
            lines = lines[1:]
        tail = lines[-int(n):]
        return (
            f"Log: {log_name}  (showing last {len(tail)} lines)\n"
            + "\n".join(tail)
        )
    except OSError as e:
        return f"ERROR: cannot read {log_name}: {e}"


def grep_log(context, pattern: str, max_matches: int = 40) -> str:
    """
    Search the log for lines matching a regex pattern (case-insensitive).
    For large logs (> 5 MB) only the last 5 MB is searched.
    Useful patterns: 'Divergen', 'FATAL', 'bounding', 'nan|inf', 'exceeded'.
    """
    ctx = _coerce(context)
    log_name = _find_log(ctx)
    if log_name is None:
        return "ERROR: no log file found."

    try:
        # Cap at 5 MB to avoid exhausting memory / SSH bandwidth on huge logs.
        raw  = ctx.tail_bytes(log_name, 5 * 1024 * 1024)
        text = raw.decode("utf-8", errors="replace")
    except OSError as e:
        return f"ERROR: cannot read log: {e}"

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"ERROR: invalid regex '{pattern}': {e}"

    matches = [line for line in text.splitlines() if regex.search(line)]
    if not matches:
        return f"No lines matching '{pattern}' in {log_name}."

    total = len(matches)
    shown = matches[-int(max_matches):]
    header = (
        f"Found {total} match(es) for '{pattern}'"
        + (f" — showing last {len(shown)}:" if total > max_matches else ":")
    )
    return header + "\n" + "\n".join(shown)


def read_residuals(context, last_n: int = 25) -> str:
    """
    Parse and summarise initial-residual trends from the log.
    Covers all OpenFOAM solvers that emit the standard residual line:
      <solver>:  Solving for <field>, Initial residual = <val>, ...
    Returns per-field statistics and a trend label.
    """
    ctx = _coerce(context)
    log_name, text, err = _read_log_text(ctx)
    if err:
        return f"ERROR: {err}"

    residual_re = re.compile(
        r"Solving for (\w+),\s*"
        r"Initial residual\s*=\s*([0-9eE+\-\.]+),\s*"
        r"Final residual\s*=\s*([0-9eE+\-\.]+),\s*"
        r"No Iterations\s*(\d+)",
        re.IGNORECASE,
    )

    by_field: dict[str, list[float]] = defaultdict(list)
    for m in residual_re.finditer(text):
        try:
            by_field[m.group(1)].append(float(m.group(2)))
        except ValueError:
            pass

    if not by_field:
        return (
            f"No residual data found in {log_name}.\n"
            "The solver may not have started yet, or the log format is unrecognised."
        )

    lines = [f"Residual summary — last {last_n} time steps per field:"]
    for field, vals in sorted(by_field.items()):
        recent = vals[-int(last_n):]
        n      = len(recent)
        last   = recent[-1]
        peak   = max(recent)

        # Trend: compare mean of first half vs second half.
        if n >= 4:
            mid   = n // 2
            early = sum(recent[:mid]) / mid
            late  = sum(recent[mid:]) / max(1, n - mid)
            if late > early * 2.0 or last > 1.0:
                trend = "INCREASING — likely diverging"
            elif late < early * 0.5:
                trend = "decreasing — converging"
            else:
                trend = "stable"
        else:
            trend = "insufficient data"

        lines.append(
            f"\n  {field}:"
            f"\n    entries in log : {len(vals)}"
            f"\n    last value     : {last:.4e}"
            f"\n    peak (recent)  : {peak:.4e}"
            f"\n    trend          : {trend}"
            f"\n    last 8 values  : {[f'{v:.3e}' for v in recent[-8:]]}"
        )
    return "\n".join(lines)


def read_courant_numbers(context, last_n: int = 25) -> str:
    """
    Extract Courant number history from the log (transient solvers).
    Pattern: 'Courant Number mean: X  max: Y'
    """
    ctx = _coerce(context)
    log_name, text, err = _read_log_text(ctx)
    if err:
        return f"ERROR: {err}"

    co_re = re.compile(
        r"Courant Number mean:\s*([0-9eE+\-\.]+)\s+max:\s*([0-9eE+\-\.]+)",
        re.IGNORECASE,
    )
    entries = [(float(m.group(1)), float(m.group(2))) for m in co_re.finditer(text)]

    if not entries:
        return (
            "No Courant number data found.\n"
            "This may be a steady-state solver (SIMPLE) or the run has not started."
        )

    recent    = entries[-int(last_n):]
    max_co    = max(v[1] for v in recent)
    last_mean = recent[-1][0]
    last_max  = recent[-1][1]

    if max_co > 10:
        warning = "CRITICAL: max Co > 10 — severe instability, reduce deltaT immediately."
    elif max_co > 1:
        warning = "WARNING: max Co > 1 — time step too large, instability likely."
    elif max_co > 0.8:
        warning = "CAUTION: max Co approaching 1 — monitor closely."
    else:
        warning = "OK: max Co within acceptable range."

    return "\n".join([
        f"Courant number history ({log_name}, last {len(recent)} steps):",
        f"  last mean Co : {last_mean:.4f}",
        f"  last max Co  : {last_max:.4f}",
        f"  peak max Co  : {max_co:.4f}",
        f"  {warning}",
        f"  recent max values: {[f'{v[1]:.3f}' for v in recent[-10:]]}",
    ])


def read_controlDict(context) -> str:
    """Read and return the full content of system/controlDict."""
    ctx = _coerce(context)
    if not ctx.exists("system/controlDict"):
        return f"ERROR: system/controlDict not found in {ctx.resolve_str()}"
    try:
        return ctx.read_text("system/controlDict")
    except OSError as e:
        return f"ERROR: cannot read controlDict: {e}"


# Keys that the agent is allowed to modify — whitelist of stability / control params.
_ALLOWED_KEYS = {
    "deltaT",
    "maxCo",
    "maxDeltaT",
    "maxAlphaCo",
    "endTime",
    "writeInterval",
    "adjustTimeStep",
    "stopAt",           # endTime | nextWrite | writeNow | noWriteNow
    "purgeWrite",
}

# Keys whose values must be positive finite numbers (not keywords like yes/no).
_NUMERIC_KEYS = {
    "deltaT", "maxCo", "maxDeltaT", "maxAlphaCo", "endTime", "writeInterval",
    "purgeWrite",
}

# Per-key value whitelists for non-numeric keys.
_ENUM_KEYS = {
    "adjustTimeStep": {"yes", "no", "true", "false", "on", "off"},
    "stopAt":         {"endTime", "nextWrite", "writeNow", "noWriteNow"},
}


def modify_controlDict(context, key: str, value: str) -> str:
    """
    Safely modify ONE key in system/controlDict.

    key   : must be one of the allowed OpenFOAM controlDict keys
    value : the new value as a string (e.g. '0.0025' or 'yes')

    Always creates system/controlDict.bak before writing.
    Returns a success or error message — never raises.
    """
    ctx = _coerce(context)

    # ── key validation ─────────────────────────────────────────────────────
    if key not in _ALLOWED_KEYS:
        return (
            f"ERROR: '{key}' is not in the allowed modification list.\n"
            f"Allowed: {sorted(_ALLOWED_KEYS)}\n"
            f"For fvSolution / fvSchemes changes, use manual intervention."
        )

    # ── value sanitisation ─────────────────────────────────────────────────
    clean_value = str(value).strip().rstrip(";")

    if re.search(r"[{};#\n\r]", clean_value):
        return f"ERROR: value '{value}' contains forbidden characters ({{, }}, ;, #)."

    if key in _NUMERIC_KEYS:
        try:
            numeric = float(clean_value)
        except ValueError:
            return f"ERROR: '{clean_value}' is not a valid number for key '{key}'."
        if numeric <= 0:
            return f"ERROR: '{key}' must be positive (got {numeric})."
        if not (1e-15 < numeric < 1e10):
            return (
                f"ERROR: value {numeric} for '{key}' is outside the "
                f"plausible range [1e-15, 1e10]."
            )
    elif key in _ENUM_KEYS:
        if clean_value not in _ENUM_KEYS[key]:
            return (
                f"ERROR: '{clean_value}' is not a valid value for '{key}'.\n"
                f"Allowed: {sorted(_ENUM_KEYS[key])}"
            )

    # ── read file ──────────────────────────────────────────────────────────
    if not ctx.exists("system/controlDict"):
        return f"ERROR: system/controlDict not found in {ctx.resolve_str()}"
    try:
        original = ctx.read_text("system/controlDict")
    except OSError as e:
        return f"ERROR: cannot read controlDict: {e}"

    # ── locate key (OpenFOAM foam-dict format: "key  value;") ──────────────
    pattern = re.compile(
        r"^(\s*" + re.escape(key) + r"\s+)([^;]+?)(;)",
        re.MULTILINE,
    )
    match = pattern.search(original)
    if not match:
        # Key absent — insert it before the last closing "}" of the file.
        # OpenFOAM foam-dict files end with a bare "}" on its own line.
        insert_line = f"\n{key}  {clean_value};\n"
        last_brace = original.rfind("\n}")
        if last_brace == -1:
            # No closing brace found — append at end of file.
            new_text = original.rstrip() + insert_line
        else:
            new_text = original[:last_brace] + insert_line + original[last_brace:]
        old_value = "(absent — key added)"
    else:
        old_value = match.group(2).strip()
        if old_value == clean_value:
            return f"No change: '{key}' is already '{clean_value}'."
        new_text = pattern.sub(
            lambda m: m.group(1) + clean_value + m.group(3),
            original,
            count=1,
        )

    # ── backup + atomic write ──────────────────────────────────────────────
    try:
        ctx.copy2("system/controlDict", "system/controlDict.bak")
        ctx.write_text("system/controlDict", new_text)
    except OSError as e:
        return f"ERROR: failed to write controlDict: {e}"

    return (
        f"SUCCESS: '{key}' changed {old_value!r} -> '{clean_value}' "
        f"in system/controlDict on {ctx.resolve_str()}. "
        f"Backup saved to system/controlDict.bak."
    )


# ─── confirmation gate ───────────────────────────────────────────────────────

def confirm_with_user(context, message: str) -> str:
    """
    Show a proposed action to the user and return their free-text response.

    The agent MUST call this before any call to modify_controlDict.
    The return value is whatever the user typed — the agent (as LLM) interprets
    it to decide whether to proceed, abort, or use a different value.

    `context` is accepted but not used (required by the skill injection protocol).
    """
    sep = "=" * 58
    print(f"\n{sep}")
    print("  OF-AGENT — PROPOSED MODIFICATION")
    print(sep)
    for line in message.strip().splitlines():
        print(f"  {line}")
    print(sep)

    try:
        raw = input("  You > ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        return "cancelled"

    return raw if raw else "cancelled"


# ─── simulation timeline ─────────────────────────────────────────────────────

def list_timesteps(context) -> str:
    """
    List the numeric time directories present in the case root.
    Returns start time, available checkpoints, and the latest written time.
    Also checks for processor* directories (parallel run).
    """
    ctx = _coerce(context)
    entries = ctx.listdir(".")
    parallel = any(name.startswith("processor") and is_dir
                   for name, is_dir in entries)

    # Collect numeric directory names
    time_dirs = []
    for name, is_dir in entries:
        if not is_dir:
            continue
        try:
            time_dirs.append(float(name))
        except ValueError:
            pass
    time_dirs.sort()

    if not time_dirs:
        return "No numeric time directories found in case root."

    lines = [
        f"Time directories found: {len(time_dirs)}",
        f"  First checkpoint : {time_dirs[0]:.6g}",
        f"  Latest checkpoint: {time_dirs[-1]:.6g}",
        f"  All checkpoints  : {[f'{t:.6g}' for t in time_dirs]}",
        f"  Parallel run     : {'yes' if parallel else 'no'}",
    ]
    if parallel:
        # Count processor dirs
        n_proc = sum(1 for name, is_dir in entries
                     if is_dir and re.match(r"processor\d+$", name))
        lines.append(f"  Processors       : {n_proc}")
    return "\n".join(lines)


def estimate_runtime(context) -> str:
    """
    Estimate the remaining wall-clock time for the simulation.

    Algorithm:
      1. Parse (simulation_time, ExecutionTime) pairs from the log.
      2. Compute simulation speed (sim-s / wall-s) from the last 10 steps.
      3. Read endTime from system/controlDict.
      4. remaining_wall = (endTime - current_sim_time) / speed
      5. Report variance of the speed to indicate confidence level.
    """
    ctx = _coerce(context)
    log_name, text, err = _read_log_text(ctx)
    if err:
        return f"ERROR: {err}"

    # Parse all "Time = X" positions and all "ExecutionTime = Y s" positions.
    # Strategy: for each Time marker, the next ExecutionTime line in the text
    # is its wall-clock stamp (OpenFOAM always writes ExecutionTime at step end).
    time_re = re.compile(r"^Time\s*=\s*([0-9eE+\-\.]+)\s*$", re.MULTILINE)
    exec_re = re.compile(r"ExecutionTime\s*=\s*([0-9eE+\-\.]+)\s*s", re.IGNORECASE)

    # Build position-indexed lists
    exec_matches = [(float(m.group(1)), m.start()) for m in exec_re.finditer(text)]

    pairs: list[tuple[float, float]] = []   # (sim_time, wall_time_s)
    for tm in time_re.finditer(text):
        sim_t = float(tm.group(1))
        pos   = tm.start()
        # Find next ExecutionTime after this position
        nxt = next((wall for wall, ep in exec_matches if ep > pos), None)
        if nxt is not None:
            pairs.append((sim_t, nxt))

    if len(pairs) < 2:
        return (
            "Insufficient data to estimate runtime.\n"
            "Need at least 2 completed time steps in the log."
        )

    current_sim_time  = pairs[-1][0]
    current_wall_time = pairs[-1][1]

    # Read endTime from controlDict
    end_time: Optional[float] = None
    if ctx.exists("system/controlDict"):
        try:
            ctrl = ctx.read_text("system/controlDict")
            m = re.search(r"^\s*endTime\s+([0-9eE+\-\.]+)\s*;", ctrl, re.MULTILINE)
            if m:
                end_time = float(m.group(1))
        except OSError:
            pass

    # Compute per-step speed from last ≤10 consecutive pairs
    recent = pairs[-10:]
    step_speeds: list[float] = []
    for i in range(1, len(recent)):
        d_sim  = recent[i][0] - recent[i - 1][0]
        d_wall = recent[i][1] - recent[i - 1][1]
        if d_wall > 0 and d_sim > 0:
            step_speeds.append(d_sim / d_wall)

    lines = [f"Runtime estimate  ({log_name}):"]
    lines.append(f"  Simulation time now   : {current_sim_time:.6g} s")
    lines.append(
        f"  Wall time elapsed     : {current_wall_time:.0f} s"
        f"  ({current_wall_time / 3600:.2f} h)"
    )

    if end_time is not None:
        remaining_sim = end_time - current_sim_time
        lines.append(f"  End time (controlDict): {end_time:.6g} s")
        lines.append(f"  Remaining sim time    : {remaining_sim:.6g} s")

        if remaining_sim <= 0:
            lines.append("  NOTE: current time >= endTime — simulation may already be finished.")
        elif step_speeds:
            mean_speed = sum(step_speeds) / len(step_speeds)
            remaining_wall = remaining_sim / mean_speed

            # Coefficient of variation → confidence label
            if len(step_speeds) >= 3:
                variance = sum((s - mean_speed) ** 2 for s in step_speeds) / len(step_speeds)
                cv = (variance ** 0.5) / mean_speed if mean_speed > 0 else 1.0
                if cv < 0.10:
                    confidence = "high (stable speed)"
                elif cv < 0.30:
                    confidence = "medium (moderately variable speed)"
                else:
                    confidence = "low (highly variable speed — rough estimate)"
            else:
                confidence = "low (too few steps to estimate variance)"

            def _fmt(s: float) -> str:
                if s < 120:
                    return f"{s:.0f} s"
                if s < 7200:
                    return f"{s/60:.1f} min"
                return f"{s/3600:.2f} h"

            lines.append(
                f"  Simulation speed      : {mean_speed:.4e} sim-s/wall-s"
                f"  ({1/mean_speed:.1f} wall-s per sim-s)"
            )
            lines.append(f"  Estimated remaining   : {_fmt(remaining_wall)}")
            lines.append(f"  Confidence            : {confidence}")
        else:
            lines.append("  Cannot estimate speed (wall-time not advancing between steps).")
    else:
        lines.append("  endTime not found in controlDict — cannot estimate remaining time.")

    return "\n".join(lines)


# ─── case configuration reader ────────────────────────────────────────────────

# Known turbulence property files, in priority order
_TURB_FILES = [
    "constant/turbulenceProperties",
    "constant/RASProperties",
    "constant/LESProperties",
    "constant/turbulenceModel",
]

# Transient solvers → LES/PISO/PIMPLE cases
_TRANSIENT_SOLVERS = {
    "pimpleFoam", "pisoFoam", "interFoam", "interIsoFoam",
    "buoyantPimpleFoam", "reactingFoam", "sonicFoam",
}
_STEADY_SOLVERS = {
    "simpleFoam", "rhoSimpleFoam", "buoyantSimpleFoam",
    "potentialFoam", "laplacianFoam",
}


def read_case_setup(context) -> str:
    """
    Read and summarise the physical setup of the case:
      - turbulence model and simulation type (LES / RAS / laminar)
      - solver type (transient vs steady-state)
      - number of processors (parallel run detection)
      - available log files (detects multi-stage runs: RANS → LES restarts)
      - latest written time directory
    """
    ctx = _coerce(context)
    lines = [f"Case setup summary  ({ctx.resolve_str()}):"]

    # ── solver from most recent log ───────────────────────────────────────
    log_files = sorted(ctx.glob_logs(), key=lambda x: x[1], reverse=True)
    if log_files:
        try:
            header = ctx.read_text(log_files[0][0])[:2000]
            solver = _detect_solver(header)
        except OSError:
            solver = None
        lines.append(f"\n  Active solver     : {solver or 'unknown'}")
        if solver in _TRANSIENT_SOLVERS:
            lines.append("  Solver type       : transient (time-accurate)")
        elif solver in _STEADY_SOLVERS:
            lines.append("  Solver type       : steady-state")
        else:
            lines.append("  Solver type       : unknown")

        # All log files → detect multi-stage runs
        if len(log_files) > 1:
            log_names = [n for n, _ in log_files]
            lines.append(f"  Log files found   : {log_names}")
            lines.append(
                "  NOTE: multiple logs suggest a multi-stage run "
                "(e.g. RANS initialisation followed by LES)."
            )
        else:
            lines.append(f"  Log file          : {log_files[0][0]}")
    else:
        lines.append("\n  No log files found.")

    # ── turbulence properties ─────────────────────────────────────────────
    turb_found = False
    for tf in _TURB_FILES:
        if ctx.exists(tf):
            try:
                turb_text = ctx.read_text(tf)
                # simulationType keyword
                m = re.search(
                    r"simulationType\s+(\w+)\s*;", turb_text, re.IGNORECASE
                )
                sim_type = m.group(1) if m else "not found"
                lines.append(f"\n  Turbulence file   : {tf}")
                lines.append(f"  simulationType    : {sim_type}")

                # Model name (RASModel / LESModel / SGSModel)
                for kw in ("RASModel", "LESModel", "SGSModel", "model"):
                    mm = re.search(
                        rf"{kw}\s+(\w+)\s*;", turb_text, re.IGNORECASE
                    )
                    if mm:
                        lines.append(f"  Turbulence model  : {mm.group(1)}")
                        break
                turb_found = True
                break
            except OSError:
                pass
    if not turb_found:
        lines.append("\n  Turbulence properties file: not found.")

    # ── transport / material properties ──────────────────────────────────
    for tf in ("constant/transportProperties", "constant/physicalProperties"):
        if ctx.exists(tf):
            lines.append(f"  Transport props   : {tf} (present)")
            break

    # ── parallel run detection ────────────────────────────────────────────
    entries = ctx.listdir(".")
    n_proc  = sum(1 for n, d in entries if d and re.match(r"processor\d+$", n))
    if n_proc:
        lines.append(f"\n  Parallel run      : yes ({n_proc} processors)")
        # Read decomposeParDict if present
        if ctx.exists("system/decomposeParDict"):
            try:
                dpd = ctx.read_text("system/decomposeParDict")
                m = re.search(r"numberOfSubdomains\s+(\d+)\s*;", dpd)
                if m:
                    lines.append(f"  Decomposition     : {m.group(1)} subdomains")
            except OSError:
                pass
    else:
        lines.append("\n  Parallel run      : no (serial)")

    # ── time directories ─────────────────────────────────────────────────
    time_dirs = sorted(
        float(n) for n, d in entries
        if d and _is_float(n)
    )
    if time_dirs:
        lines.append(
            f"\n  Time checkpoints  : {len(time_dirs)} "
            f"({time_dirs[0]:.6g} → {time_dirs[-1]:.6g})"
        )
    else:
        lines.append("\n  Time checkpoints  : none found")

    return "\n".join(lines)


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


# ─── generic file tools ───────────────────────────────────────────────────────

def read_file(context, path: str) -> str:
    """
    Read any file inside the case directory.
    Always call this before patch_file or write_file so you see exact content.
    """
    ctx = _coerce(context)
    if not ctx.exists(path):
        return f"ERROR: file not found: {path}"
    try:
        content = ctx.read_text(path)
        n = len(content.splitlines())
        return f"=== {path} ({n} lines) ===\n{content}"
    except Exception as e:
        return f"ERROR reading {path}: {e}"


def write_file(context, path: str, content: str) -> str:
    """
    Overwrite a file completely with new content.
    Creates a .bak backup of the original if it already exists.
    Use for full rewrites; prefer patch_file for targeted edits.
    """
    ctx = _coerce(context)
    backed_up = False
    if ctx.exists(path):
        try:
            ctx.copy2(path, path + ".bak")
            backed_up = True
        except Exception:
            pass
    try:
        ctx.write_text(path, content)
        note = f" (backup: {path}.bak)" if backed_up else " (new file)"
        return f"SUCCESS: wrote {path}{note}"
    except Exception as e:
        return f"ERROR writing {path}: {e}"


def patch_file(context, path: str, old_text: str, new_text: str) -> str:
    """
    Replace an exact string in a file (find-and-replace).

    old_text must appear EXACTLY ONCE in the file — include enough surrounding
    lines to make it unique.  If it appears zero or multiple times the patch
    is refused and an error is returned.

    A .bak backup is created before writing.

    Use this for targeted edits: inserting a key before/after a block,
    changing a value in place, adding lines inside a specific section.
    Always read_file first to copy the exact text including whitespace.
    """
    ctx = _coerce(context)
    if not ctx.exists(path):
        return f"ERROR: file not found: {path}"
    try:
        original = ctx.read_text(path)
    except Exception as e:
        return f"ERROR reading {path}: {e}"

    count = original.count(old_text)
    if count == 0:
        return (
            f"ERROR: old_text not found in {path}.\n"
            f"Make sure to copy the exact text (whitespace, newlines) from read_file."
        )
    if count > 1:
        return (
            f"ERROR: old_text found {count} times in {path} — too ambiguous.\n"
            f"Include more surrounding lines to make it unique."
        )

    try:
        ctx.copy2(path, path + ".bak")
    except Exception:
        pass

    new_content = original.replace(old_text, new_text, 1)
    try:
        ctx.write_text(path, new_content)
        return f"SUCCESS: patched {path} (backup: {path}.bak)"
    except Exception as e:
        return f"ERROR writing {path}: {e}"


def list_files(context, path: str = ".") -> str:
    """
    List files and subdirectories at the given relative path (default: case root).
    More targeted than list_case_files — use when you need to inspect
    a specific subdirectory (e.g. 'constant', 'system', '0').
    """
    ctx = _coerce(context)
    entries = ctx.listdir(path)
    if not entries:
        return f"(empty or not found: {path})"
    lines = []
    for name, is_dir in entries:
        prefix = "[dir]" if is_dir else "     "
        lines.append(f"  {prefix} {name}")
    header = f"{path}/" if not path.endswith("/") else path
    return header + "\n" + "\n".join(lines)


# Files the agent must never delete without explicit user instruction.
_PROTECTED_FILES = {
    "system/controlDict",
    "system/fvSolution",
    "system/fvSchemes",
    "system/blockMeshDict",
    "system/decomposeParDict",
    "constant/turbulenceProperties",
    "constant/transportProperties",
    "constant/polyMesh/boundary",
}


# ─── one-shot status snapshot ────────────────────────────────────────────────

def simulation_status(context) -> str:
    """
    Comprehensive one-shot status snapshot — combines solver, progress,
    residuals, Courant number, and ETA into a single panel.

    Use this as the FIRST diagnostic call when the user asks open-ended
    questions like "how is the simulation going?" or "status?".
    Avoids 4-5 separate skill calls for the same information.
    """
    ctx = _coerce(context)
    log_name, text, err = _read_log_text(ctx)
    if err:
        return f"ERROR: {err}"

    out: list[str] = [f"Simulation status  ({ctx.resolve_str()})"]
    out.append(f"  Log file              : {log_name}")

    # ── solver ───────────────────────────────────────────────────────────
    solver = _detect_solver(text[:4000])
    if solver:
        if solver in _TRANSIENT_SOLVERS:
            stype = "transient"
        elif solver in _STEADY_SOLVERS:
            stype = "steady-state"
        else:
            stype = "unknown type"
        out.append(f"  Solver                : {solver}  ({stype})")

    # ── liveness from log tail ───────────────────────────────────────────
    tail = text[-4000:]
    if "FOAM FATAL" in tail or "FOAM Fatal" in tail:
        live = "CRASHED — FOAM FATAL in log tail"
    elif re.search(r"\bEnd\s*\n", tail):
        live = "FINISHED — 'End' marker in log tail"
    else:
        live = "RUNNING (or stalled — no End / FATAL marker)"
    out.append(f"  Liveness              : {live}")

    # ── current sim time, endTime, % progress ────────────────────────────
    times = [float(m.group(1))
             for m in re.finditer(r"^Time\s*=\s*([0-9eE+\-\.]+)\s*$",
                                  text, re.MULTILINE)]
    end_time = None
    delta_t  = None
    adj_ts   = None
    stop_at  = None
    if ctx.exists("system/controlDict"):
        try:
            ctrl = ctx.read_text("system/controlDict")
            m = re.search(r"^\s*endTime\s+([0-9eE+\-\.]+)\s*;", ctrl, re.MULTILINE)
            if m: end_time = float(m.group(1))
            m = re.search(r"^\s*deltaT\s+([0-9eE+\-\.]+)\s*;", ctrl, re.MULTILINE)
            if m: delta_t = float(m.group(1))
            m = re.search(r"^\s*adjustTimeStep\s+(\w+)\s*;", ctrl, re.MULTILINE)
            if m: adj_ts = m.group(1)
            m = re.search(r"^\s*stopAt\s+(\w+)\s*;", ctrl, re.MULTILINE)
            if m: stop_at = m.group(1)
        except OSError:
            pass

    if times:
        cur = times[-1]
        out.append(f"  Current sim time      : {cur:.6g}")
        if end_time is not None:
            pct = (cur / end_time * 100.0) if end_time > 0 else 0.0
            out.append(f"  endTime               : {end_time:.6g}  ({pct:.1f}% done)")
        if delta_t is not None:
            out.append(f"  deltaT                : {delta_t:.4e}")
        if adj_ts is not None:
            out.append(f"  adjustTimeStep        : {adj_ts}")
        if stop_at is not None and stop_at != "endTime":
            out.append(f"  stopAt                : {stop_at}  (override active)")

    # ── residuals (last value per field, plus trend label) ───────────────
    residual_re = re.compile(
        r"Solving for (\w+),\s*Initial residual\s*=\s*([0-9eE+\-\.]+)",
        re.IGNORECASE,
    )
    by_field: dict[str, list[float]] = defaultdict(list)
    for m in residual_re.finditer(text):
        try:
            by_field[m.group(1)].append(float(m.group(2)))
        except ValueError:
            pass
    if by_field:
        out.append("  Residuals (latest)    :")
        for field, vals in sorted(by_field.items()):
            recent = vals[-20:]
            last = recent[-1]
            if len(recent) >= 4:
                mid   = len(recent) // 2
                early = sum(recent[:mid]) / mid
                late  = sum(recent[mid:]) / max(1, len(recent) - mid)
                if late > early * 2.0 or last > 1.0:
                    trend = "INCREASING"
                elif late < early * 0.5:
                    trend = "decreasing"
                else:
                    trend = "stable"
            else:
                trend = "n/a"
            out.append(f"    {field:<8s} : {last:.3e}  ({trend})")

    # ── Courant number (transient only) ──────────────────────────────────
    co_re = re.compile(
        r"Courant Number mean:\s*([0-9eE+\-\.]+)\s+max:\s*([0-9eE+\-\.]+)",
        re.IGNORECASE,
    )
    co_entries = [(float(m.group(1)), float(m.group(2)))
                  for m in co_re.finditer(text)]
    if co_entries:
        recent = co_entries[-20:]
        last_max = recent[-1][1]
        peak     = max(v[1] for v in recent)
        if peak > 10:    co_label = "CRITICAL"
        elif peak > 1:   co_label = "WARNING"
        elif peak > 0.8: co_label = "caution"
        else:            co_label = "ok"
        out.append(
            f"  Courant number        : last_max={last_max:.3f}  "
            f"recent_peak={peak:.3f}  ({co_label})"
        )

    # ── ETA (compact, reuses estimate_runtime logic inline) ──────────────
    exec_re = re.compile(r"ExecutionTime\s*=\s*([0-9eE+\-\.]+)\s*s",
                         re.IGNORECASE)
    exec_matches = [(float(m.group(1)), m.start()) for m in exec_re.finditer(text)]
    pairs: list[tuple[float, float]] = []
    for tm in re.finditer(r"^Time\s*=\s*([0-9eE+\-\.]+)\s*$",
                          text, re.MULTILINE):
        nxt = next((wall for wall, ep in exec_matches if ep > tm.start()), None)
        if nxt is not None:
            pairs.append((float(tm.group(1)), nxt))
    if len(pairs) >= 2 and end_time is not None and end_time > pairs[-1][0]:
        recent = pairs[-10:]
        speeds = [(recent[i][0] - recent[i-1][0]) / (recent[i][1] - recent[i-1][1])
                  for i in range(1, len(recent))
                  if recent[i][1] > recent[i-1][1] and recent[i][0] > recent[i-1][0]]
        if speeds:
            mean_speed = sum(speeds) / len(speeds)
            remaining_wall = (end_time - pairs[-1][0]) / mean_speed
            wall_now = pairs[-1][1]
            def _fmt(s: float) -> str:
                if s < 120:  return f"{s:.0f} s"
                if s < 7200: return f"{s/60:.1f} min"
                return f"{s/3600:.2f} h"
            out.append(
                f"  Wall elapsed / ETA    : {_fmt(wall_now)} elapsed, "
                f"~{_fmt(remaining_wall)} remaining"
            )

    # ── checkpoints on disk ──────────────────────────────────────────────
    entries = ctx.listdir(".")
    time_dirs = sorted(float(n) for n, d in entries if d and _is_float(n))
    parallel  = any(d and re.match(r"processor\d+$", n) for n, d in entries)
    if time_dirs:
        out.append(
            f"  Saved checkpoints     : {len(time_dirs)} "
            f"(latest: {time_dirs[-1]:.6g})"
            + ("  [parallel run]" if parallel else "")
        )

    return "\n".join(out)


def delete_file(context, path: str) -> str:
    """
    Delete a single file inside the case directory.
    Protected case files (controlDict, fvSolution, etc.) cannot be deleted
    through this skill — use manual intervention for those.
    Safe for: log files, backup files (.bak), old time directories, etc.
    """
    ctx = _coerce(context)
    # Normalise separators for the protection check
    norm = path.replace("\\", "/").lstrip("./")
    if norm in _PROTECTED_FILES:
        return (
            f"ERROR: '{path}' is protected — delete manually if really needed."
        )
    if not ctx.exists(path):
        return f"ERROR: file not found: {path}"
    try:
        ctx.remove(path)
        return f"SUCCESS: deleted {path}"
    except Exception as e:
        return f"ERROR deleting {path}: {e}"
