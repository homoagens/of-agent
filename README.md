# OF-Agent

An interactive AI assistant for monitoring and controlling live OpenFOAM CFD simulations — directly from your terminal.

OF-Agent connects to a running simulation (locally or via SSH), reads the log files and case directory in real time, and lets you ask plain-language questions. It diagnoses convergence, estimates remaining time, and can safely modify `controlDict` parameters while the solver is running.

---

## Features

- **Natural language interface** — ask "is it converging?", "how long left?", "why did it crash?" in any language
- **Local and remote** — attach to a case on your machine or on an HPC node over SSH; the interface is identical
- **Structured decision process** — every diagnosis follows a hypothesis → challenge → verdict pipeline to avoid premature conclusions
- **Safe modifications** — `controlDict` edits require explicit confirmation, create a `.bak` backup, and take effect immediately (OpenFOAM hot-reloads the file)
- **Session memory** — conversation history is persisted inside the case directory; pick up where you left off after a restart
- **Slash commands** — instant diagnostic commands that bypass the LLM: `/status`, `/log`, `/residuals`, `/courant`, `/eta`, `/ls`, `/debug`, `/clear`

---

## How It Works

OF-Agent uses a **ReAct loop** (Reasoning + Acting): the LLM responds with a `thought` and an `action`, the action calls a skill (Python function), the result is fed back as an observation, and the loop repeats until the agent produces a final `reply`.

```
User question
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  LLM (ReAct loop)                                   │
│                                                     │
│  PHASE 1: Hypothesis  →  call skill(s)              │
│  PHASE 2: Challenge   →  call skill(s) to disprove  │
│  PHASE 3: Verdict     →  final reply                │
└─────────────────────────────────────────────────────┘
     │
     ▼
Answer to user
```

All file I/O goes through a **transport abstraction** (`LocalContext` / `SSHContext`) so every skill works identically on local and remote cases.

---

## Installation

```bash
git clone https://github.com/homoagens/of-agent.git
cd of-agent
pip install -r requirements.txt
```

For SSH support:
```bash
pip install paramiko
```

---

## Configuration

Copy the example environment file and edit it:

```bash
cp .env.example .env
```

```env
# .env
OF_AGENT_BACKEND_URL=http://localhost:11434   # your LLM API endpoint
OF_AGENT_BACKEND_KEY=local                    # API key (any string for local)
OF_AGENT_MODEL=llama3.1:8b                    # model name on your backend
```

OF-Agent works with any **OpenAI-compatible API**: [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), [llama.cpp server](https://github.com/ggerganov/llama.cpp), OpenRouter, or a direct Anthropic/OpenAI endpoint.

For best results use a model with at least 8B parameters and good instruction following (Llama 3.1 8B, Qwen 2.5 14B, Gemma 3 12B, etc.).

---

## Usage

**Local case:**
```bash
python main.py /path/to/openfoam/case
```

**Remote case over SSH:**
```bash
python main.py user@hostname:/remote/path/to/case
python main.py cfd@hpc.lab.example:/scratch/runs/motorBike --key ~/.ssh/id_rsa
python main.py simulo@10.0.0.5:/runs/cavity --port 2222 --password mypass
```

**Options:**
```
--port      SSH port (default: 22)
--key       path to SSH private key
--password  SSH password
--timeout   SSH connection timeout in seconds (default: 30)
```

---

## Slash Commands

These execute instantly without an LLM call:

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/status` | Full simulation snapshot (residuals, Co, ETA, checkpoints) |
| `/log [N]` | Tail last N lines of the log (default: 40) |
| `/residuals` | Residual trends per field |
| `/courant` | Courant number history |
| `/eta` | Estimated remaining wall time |
| `/controlDict` | Show current `system/controlDict` |
| `/ls [path]` | List files at a relative path |
| `/clear` | Wipe session memory for this case |
| `/save` | Force-save session memory now |
| `/debug` | Toggle verbose ReAct output |
| `/exit` | Quit OF-Agent |

---

## Available Skills

| Skill | Description |
|---|---|
| `simulation_status` | One-shot comprehensive snapshot |
| `read_residuals` | Residual trend analysis per field |
| `read_courant_numbers` | Courant number history and warning level |
| `tail_log` | Last N lines of the log |
| `grep_log` | Regex search in the log |
| `get_log_summary` | Solver name, version, start time |
| `list_case_files` | Top-level case structure |
| `list_timesteps` | Saved time directories |
| `read_controlDict` | Current `controlDict` content |
| `modify_controlDict` | Safely modify one key (with confirmation) |
| `estimate_runtime` | Wall time remaining estimate |
| `read_case_setup` | Turbulence model, solver type, parallel setup |
| `read_file` | Read any file in the case |
| `patch_file` | Surgical find-and-replace in any file |
| `write_file` | Full file overwrite (with `.bak` backup) |
| `list_files` | List a specific subdirectory |
| `delete_file` | Delete non-protected files |
| `confirm_with_user` | Show a proposed action and ask for confirmation |

---

## Example Session

```
OF-Agent  —  live SSH mode
  Monitoring : hpc.lab.example:/scratch/runs/motorBike
  Type a question, /help for slash commands, or 'exit' to quit.

You > /status

Simulation status  (hpc.lab.example:/scratch/runs/motorBike)
  Log file              : log.pimpleFoam
  Solver                : pimpleFoam  (transient)
  Liveness              : RUNNING (or stalled — no End / FATAL marker)
  Current sim time      : 0.0842
  endTime               : 0.5  (16.8% done)
  deltaT                : 5e-05
  adjustTimeStep        : yes
  Residuals (latest)    :
    Ux       : 2.341e-03  (stable)
    Uy       : 1.892e-03  (stable)
    p        : 4.120e-02  (stable)
  Courant number        : last_max=0.412  recent_peak=0.614  (ok)
  Wall elapsed / ETA    : 1.8 h elapsed, ~8.9 h remaining
  Saved checkpoints     : 17 (latest: 0.085)  [parallel run]

You > the residuals look stagnant, should I be worried?

  >> taking simulation snapshot
  >> analysing residual convergence

OF-Agent: The residuals are stable but not meaningfully decreasing — they
have been oscillating around 1e-3 to 4e-2 for the last 25 steps with no
downward trend. This is stagnation, not divergence, but it suggests the
simulation may not be achieving the target accuracy within your endTime.
At 16.8% complete with 8.9 h remaining, you have time to investigate.
I recommend reading fvSolution to check your convergence tolerances before
deciding whether to act.

You > exit
OF-Agent: goodbye.
```

---

## Architecture

```
of-agent/
├── main.py          # REPL entry point; slash commands; SSH/local auto-detect
├── agent.py         # Generic ReAct loop (domain-agnostic)
├── skills.py        # 19 OpenFOAM-specific skill functions
├── context.py       # Transport abstraction (LocalContext / SSHContext)
├── prompts.py       # System prompt + activity labels
├── session.py       # Persistent conversation memory (stored in case dir)
├── conversation.py  # Cross-turn message history with LLM compression
├── memory.py        # LLM-based message compression
├── llm_client.py    # OpenAI-compatible API client
├── json_parser.py   # Robust JSON extraction from LLM output
└── config.py        # Configuration via environment variables
```

---

## License

MIT — see [LICENSE](LICENSE).
