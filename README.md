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
  <img src="https://img.shields.io/badge/interface-web%20UI%20%2B%20terminal-43a047?style=flat-square" alt="Web UI + terminal">
  <img src="https://img.shields.io/badge/provider-any%20OpenAI--compatible-f97316?style=flat-square" alt="Any provider">
</p>

---

OF-Agent attaches to a running **OpenFOAM** case (locally or over SSH), reads the log and case files in real time, and answers plain-language questions about it. It diagnoses convergence, estimates remaining wall time, explains crashes, and can safely tweak `controlDict` *while the solver is running*.

> Stop tailing `log.pimpleFoam` and squinting at residuals. Ask *"is it converging?"* and get a real answer.

---

## ✦ Features

- **🗣 Natural language** — ask "is it converging?", "how long left?", "why did it crash?" in any language
- **🖥 Web UI or terminal** — chat in the browser with live streaming of the reasoning and reply, or use the terminal REPL
- **🌐 Local or remote** — attach to a case on your machine or an HPC node over SSH; the interface is identical
- **🧪 Grounded** — the agent reads the actual files before answering, instead of guessing from memory
- **🛡 Safe edits** — `controlDict` changes need confirmation, create a `.bak`, and take effect immediately (OpenFOAM hot-reloads)
- **💾 Session memory** — history is saved inside the case directory; resume after a restart
- **⚡ Slash commands** — instant diagnostics that bypass the LLM

---

## ⚡ Quickstart

Three scripts, run once each, in order. Pick the `.bat` (Windows) or `.sh` (Linux / macOS) variant.

```bash
git clone https://github.com/homoagens/of-agent.git
cd of-agent
```

| Step | Windows | Linux / macOS | What it does |
|---|---|---|---|
| **1. Install** | `install.bat` | `./install.sh` | Creates a `venv` and installs dependencies |
| **2. Configure** | `configure.bat` | `./configure.sh` | Interactive prompt that writes `.env` (LLM endpoint, model, key) |
| **3. Start** | `start.bat` | `./start.sh` | Launches the web UI and opens your browser |

The browser opens at **http://localhost:7862**: paste your case path, ask a question, watch it stream in live. Press **Quit** to stop the server.

A **case** is a local path or an SSH target:

```
/path/to/openfoam/case                 local
user@hostname:/remote/path/to/case     SSH
myhpc:/scratch/runs/motorBike          SSH alias from ~/.ssh/config
```

> An SSH alias uses your `~/.ssh/config` (HostName, User, Port, IdentityFile, ProxyCommand) just like the `ssh` command, and default keys / the agent are tried automatically — so `alias:/path` usually just works.

<details>
<summary>Prefer the terminal?</summary>

After steps 1–2:

```bash
python main.py /path/to/openfoam/case
python main.py user@hostname:/remote/path/to/case
python main.py myhpc:/scratch/runs/motorBike            # ~/.ssh/config alias
python main.py simulo@10.0.0.5:/runs/cavity --port 2222 --password mypass
```

SSH options: `--port` (default 22), `--key`, `--password`, `--timeout` (default 30s).
</details>

**LLM:** any OpenAI-compatible endpoint. For best results use a model with strong instruction following.

---

## ⌨️ Slash commands

Execute instantly, without an LLM call:

| Command | Description |
|---|---|
| `/status` | Full simulation snapshot (residuals, Co, ETA, checkpoints) |
| `/log [N]` | Tail last N lines of the log (default: 40) |
| `/residuals` · `/courant` · `/eta` | Residual trends · Courant history · time remaining |
| `/controlDict` · `/ls [path]` | Show `controlDict` · list files |
| `/clear` · `/save` · `/debug` · `/exit` | Wipe / save memory · toggle verbose output · quit |

---

## 🌱 Part of Homo Agens

OF-Agent is part of **[Homo Agens](https://github.com/homoagens)** — exploring autonomous agents and local inference around a simple thesis:

> The model matters less than the architecture around it.

[Email](mailto:homoagens1@gmail.com) &nbsp;·&nbsp; [X / Twitter](https://x.com/homoagens1) &nbsp;·&nbsp; [MIT License](./LICENSE)
