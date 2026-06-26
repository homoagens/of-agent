<h2 align="center">🌊 OF-Agent</h2>

<p align="center">
  <em>Talk to your OpenFOAM simulation while it runs.</em>
</p>

<p align="center">
  Live CFD monitoring  ·  Plain-language diagnosis  ·  Local or over SSH  ·  Runs on your own LLM
</p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-16a085?style=flat-square" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-3776ab?style=flat-square" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/interface-terminal%20REPL-43a047?style=flat-square" alt="Terminal REPL">
  <img src="https://img.shields.io/badge/provider-any%20OpenAI--compatible-f97316?style=flat-square" alt="Any provider">
</p>

---

OF-Agent is an interactive AI assistant for monitoring and controlling **live OpenFOAM CFD simulations** — straight from your terminal.

It attaches to a running case (locally or over SSH), reads the log and case directory in real time, and lets you ask plain-language questions. It diagnoses convergence, estimates remaining wall time, explains crashes, and can safely tweak `controlDict` parameters *while the solver is running*.

> Stop tailing `log.pimpleFoam` and squinting at residuals. Ask *"is it converging?"* and get a real answer.

---

## ✦ Features

- **🗣 Natural language** — ask "is it converging?", "how long left?", "why did it crash?" in any language
- **🌐 Local or remote** — attach to a case on your machine or an HPC node over SSH; the interface is identical
- **🧪 Structured diagnosis** — every conclusion follows a hypothesis → challenge → verdict pipeline, so the agent disproves itself before committing
- **🛡 Safe edits** — `controlDict` changes require explicit confirmation, create a `.bak` backup, and take effect immediately (OpenFOAM hot-reloads the file)
- **💾 Session memory** — conversation history is persisted inside the case directory; resume after a restart
- **⚡ Slash commands** — instant diagnostics that bypass the LLM: `/status`, `/log`, `/residuals`, `/courant`, `/eta`, `/ls`, `/debug`, `/clear`

---

## 🧠 How it works

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

## ⚡ Quickstart

```bash
git clone https://github.com/homoagens/of-agent.git
cd of-agent
pip install -r requirements.txt
pip install paramiko        # optional, for SSH support
```

Point it at your LLM (interactive — writes `.env` for you):

```bat
configure.bat     :: Windows
./configure.sh    # Linux / macOS
```

Then attach to a case:

```bash
# Local
python main.py /path/to/openfoam/case

# Remote over SSH
python main.py user@hostname:/remote/path/to/case
python main.py cfd@hpc.lab.example:/scratch/runs/motorBike --key ~/.ssh/id_rsa
python main.py simulo@10.0.0.5:/runs/cavity --port 2222 --password mypass
```

SSH options: `--port` (default 22), `--key`, `--password`, `--timeout` (default 30s).

OF-Agent works with any **OpenAI-compatible API**: [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), [llama.cpp server](https://github.com/ggerganov/llama.cpp), vLLM, OpenRouter, or a direct Anthropic/OpenAI endpoint. For best results use a model with strong instruction following (Llama 3.1 8B, Qwen 2.5 14B, Gemma 3 12B, or larger).

---

## ⌨️ Slash commands

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

## 🧰 Available skills

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

## 🎬 Example session

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

## 🗂 Architecture

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

## 🌱 Part of Homo Agens

OF-Agent is part of **[Homo Agens](https://github.com/homoagens)** — an open-source effort exploring autonomous agents, local inference, and a simple thesis:

> The model matters less than the architecture around it.
> Memory, tools, transparency, and execution control are what turn an LLM into something that actually gets things done.

---

## 📬 Contact

If you work on agents, local AI, open-source tooling, or scientific computing — let's talk.

[Email](mailto:homoagens1@gmail.com) &nbsp;·&nbsp; [X / Twitter](https://x.com/homoagens1)

---

## License

[MIT](./LICENSE)
