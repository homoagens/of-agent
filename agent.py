# agent.py — generic ReAct loop (Reasoning + Acting).
#
# An Agent is configured with:
#   - system_prompt   : base instructions + JSON response format description
#   - skills          : dict {name: callable} — the agent's toolkit
#   - final_keys      : JSON keys that, if present in a response, end the loop
#                       (e.g. {"conclusion"} or {"final_answer"})
#   - activity_labels : dict {skill_name: "readable string"} — used in
#                       non-DEBUG mode to show what the agent is doing instead
#                       of raw file contents.
#                       Strings may contain {arg_name} placeholders.
#
# The loop:
#   1. The LLM responds with JSON containing "thought" + ("action"+"args" or a final_key)
#   2. If a final_key is present -> loop ends, return the dict
#   3. Otherwise execute skills[action](**args), append the OBSERVATION, repeat
#   4. If steps are exhausted -> forced verdict
#
# Display modes (config.DEBUG):
#   DEBUG=True  -> full output: THOUGHT, ACTION, OBSERVATION, FINAL panels
#   DEBUG=False -> only a short activity label per skill call
#
# Completely domain-agnostic.

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console
from rich.panel import Panel

import config
import llm_client
import memory
from json_parser import extract_json

console = Console()


@dataclass
class AgentConfig:
    """Configuration for a single ReAct agent."""
    name:          str
    system_prompt: str
    skills:        dict                     # {skill_name: callable}
    final_keys:    tuple = ("conclusion",)  # keys that terminate the loop
    model:         Optional[str]   = None
    temperature:   Optional[float] = None
    max_steps:     Optional[int]   = None
    style:         str = ""                 # appended to system_prompt
    # Optional context object injected as first kwarg into every skill call.
    skill_context:       object = None
    skill_context_kwarg: str    = "context"
    # Human-readable labels for non-DEBUG mode.
    # {skill_name: "short description, may use {arg_name}"}
    # Skills not in this map fall back to their name with underscores replaced.
    activity_labels: dict = field(default_factory=dict)


# ── display helpers ───────────────────────────────────────────────────────────

def _dbg_panel(content: str, title: str, style: str) -> None:
    """Print a rich panel only in DEBUG mode."""
    if config.DEBUG:
        console.print(Panel(content, title=title, style=style))


def _dbg_print(*args, **kwargs) -> None:
    """console.print only in DEBUG mode."""
    if config.DEBUG:
        console.print(*args, **kwargs)


def _activity_line(cfg: AgentConfig, action: str, args: dict) -> None:
    """
    In non-DEBUG mode print a short human-readable line.
    In DEBUG mode print the full ACTION line.
    Prints nothing for actions not in the toolkit — prevents noise from
    pseudo-actions like "FINAL" that some models emit by mistake.
    """
    if config.DEBUG:
        console.print(f"[cyan]ACTION:[/cyan] {action}({args})")
        return
    # Non-DEBUG: only print for real skills
    if action not in cfg.skills:
        return
    label_tmpl = cfg.activity_labels.get(action)
    if label_tmpl is None:
        label_tmpl = action.replace("_", " ")
    if label_tmpl == "":
        # Empty label = skill manages its own output (e.g. confirm_with_user)
        return
    try:
        label = label_tmpl.format(**args) if args else label_tmpl
    except (KeyError, IndexError):
        label = label_tmpl
    console.print(f"  [dim]>>[/dim] {label}")


# ── internals ─────────────────────────────────────────────────────────────────

def _log_step(log_path: Path, entry: dict):
    """Incrementally append a step to a JSON log file (list of dicts)."""
    if log_path.exists():
        log = json.loads(log_path.read_text(encoding="utf-8"))
    else:
        log = []
    log.append(entry)
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


def _call_skill(cfg: AgentConfig, action: str, args: dict) -> str:
    """Execute a skill with error handling. Always returns a string."""
    if action not in cfg.skills:
        return f"ERROR: skill '{action}' does not exist. Available: {list(cfg.skills)}"
    try:
        fn = cfg.skills[action]
        kwargs = dict(args)
        if cfg.skill_context is not None:
            kwargs.setdefault(cfg.skill_context_kwarg, cfg.skill_context)
        return str(fn(**kwargs))
    except Exception as e:
        return f"ERROR executing {action}: {e}"


# ── public API ────────────────────────────────────────────────────────────────

def run_agent(
    cfg: AgentConfig,
    user_task: str,
    log_path: Optional[Path] = None,
    initial_messages: Optional[list] = None,
) -> Optional[dict]:
    """
    Run the ReAct loop.

    cfg              : AgentConfig
    user_task        : initial user message
    log_path         : optional path to write a narrative JSON step log
    initial_messages : optional list of {role, content} inserted BETWEEN the
                       system prompt and user_task (cross-turn memory pattern).
                       If None the behaviour is identical to no history.

    Returns the final dict (containing one of the final_keys) or None.
    The dict is enriched with: name (str), forced (bool).
    """
    model       = cfg.model       or config.DEFAULT_MODEL
    temperature = cfg.temperature if cfg.temperature is not None else config.DEFAULT_TEMPERATURE
    max_steps   = cfg.max_steps   or config.MAX_STEPS

    system_prompt = cfg.system_prompt
    if cfg.style:
        system_prompt += f"\n\nOperating style: {cfg.style}"

    if log_path is not None:
        log_path = Path(log_path)
        log_path.write_text("[]", encoding="utf-8")

    messages = [{"role": "system", "content": system_prompt}]
    if initial_messages:
        messages.extend(initial_messages)
    messages.append({"role": "user", "content": user_task})

    if config.DEBUG:
        console.print(Panel(
            f"Agent started: [bold]{cfg.name.upper()}[/bold]",
            style="bold red"
        ))

    for step in range(1, max_steps + 1):
        _dbg_print(f"\n[dim]--- Step {step}/{max_steps} ---[/dim]")

        # ── intra-run memory compression ──────────────────────────────
        messages    = memory.compress(messages, config.MAX_MESSAGES,
                                      f"loop {cfg.name}", model=model)
        total_chars = sum(len(m.get("content", "")) for m in messages)
        if total_chars > config.MAX_CHARS:
            _dbg_print(f"[yellow]Payload {total_chars} chars — compressing...[/yellow]")
            messages = memory.compress(messages, 0, f"loop {cfg.name}", model=model)

        # ── LLM call ──────────────────────────────────────────────────
        try:
            text = llm_client.call_llm(
                messages=messages, model=model,
                temperature=temperature, max_tokens=config.MAX_TOKENS,
            )
        except Exception as e:
            console.print(f"[red]LLM error at step {step}: {e}[/red]")
            continue

        # ── JSON parsing ──────────────────────────────────────────────
        try:
            response = extract_json(text)
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            continue

        thought = response.get("thought", "")
        _dbg_panel(thought, "THOUGHT", "bold yellow")

        # ── FINAL — one of the final_keys is present ──────────────────
        final_key = next((k for k in cfg.final_keys if k in response), None)
        if final_key:
            _dbg_panel(
                json.dumps(
                    {k: response[k] for k in response if k != "thought"},
                    indent=2, ensure_ascii=False,
                ),
                "FINAL", "bold yellow"
            )
            if log_path is not None:
                _log_step(log_path, {"step": step, **response})
            response["name"]   = cfg.name
            response["forced"] = False
            return response

        # ── ACTION ────────────────────────────────────────────────────
        if "action" not in response:
            _dbg_print("[red]Response has neither action nor final key — skipping.[/red]")
            continue

        action = response["action"]
        raw_args = response.get("args", {})
        args   = raw_args if isinstance(raw_args, dict) else {}

        # Guard: action must be a plain string.
        if not isinstance(action, str):
            _dbg_print(f"[red]action is not a string ({type(action).__name__}) — skipping.[/red]")
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content":
                'The "action" field must be a plain string (skill name). '
                'Fix your JSON and try again.'
            })
            continue

        # Some models emit {"action": "FINAL"} instead of the proper final key.
        # Intercept and ask for the correct format — avoids error loops.
        _TERMINAL_PSEUDO = {"FINAL", "final", "END", "end", "CONCLUDE", "conclude",
                            "ANSWER", "answer", "RESPONSE", "response"}
        if action in _TERMINAL_PSEUDO:
            _dbg_print(f"[yellow]Pseudo-action '{action}' — requesting correct format.[/yellow]")
            final_list = " or ".join(f'"{k}"' for k in cfg.final_keys)
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": (
                f'You used action="{action}" instead of a final key. '
                f'Produce IMMEDIATELY a JSON response with key {final_list}, '
                f'without using "action".'
            )})
            continue

        _activity_line(cfg, action, args)

        observation = _call_skill(cfg, action, args)
        _dbg_panel(observation, "OBSERVATION", "cyan")

        if log_path is not None:
            _log_step(log_path, {
                "step": step, "thought": thought,
                "action": action, "args": args, "observation": observation,
            })

        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user",      "content": f"[OBSERVATION]: {observation}"})

    # ── Steps exhausted — forced verdict ──────────────────────────────
    _dbg_print("[yellow]Steps exhausted — requesting forced verdict...[/yellow]")
    if not config.DEBUG:
        console.print("  [dim]>>[/dim] concluding with available information")

    final_list = " or ".join(f'"{k}"' for k in cfg.final_keys)
    messages.append({
        "role": "user",
        "content": (
            f"You have exhausted all available steps. "
            f"With the information gathered so far, produce NOW a final JSON response "
            f"with one of these keys: {final_list}. "
            f"You must conclude — no more actions are allowed."
        )
    })
    messages = memory.compress(messages, config.MAX_MESSAGES,
                               f"forced {cfg.name}", model=model)

    try:
        text     = llm_client.call_llm(
            messages=messages, model=model,
            temperature=temperature, max_tokens=config.MAX_TOKENS,
        )
        response = extract_json(text)
    except Exception as e:
        console.print(f"[red]Forced verdict failed: {e}[/red]")
        return None

    thought = response.get("thought", "")
    _dbg_panel(thought, "THOUGHT (forced)", "bold yellow")
    _dbg_panel(
        json.dumps(
            {k: response[k] for k in response if k != "thought"},
            indent=2, ensure_ascii=False,
        ),
        "FINAL (forced)", "bold yellow"
    )
    if log_path is not None:
        _log_step(log_path, {"step": max_steps + 1, **response, "forced": True})
    response["name"]   = cfg.name
    response["forced"] = True
    return response
