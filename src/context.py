# context.py — transport abstraction for the OF-Agent.
#
# Two implementations share the same interface:
#
#   LocalContext(root: Path)
#       Reads/writes files on the local filesystem via pathlib.
#       Used by main.py.
#
#   SSHContext(hostname, remote_root, user, port, key_filename, password)
#       Reads/writes files on a remote host via paramiko SFTP.
#       Used by main_ssh.py.
#       Requires: pip install paramiko
#
# Both expose the same methods, so all skills work unchanged with either.
# A _coerce() helper wraps a bare Path into a LocalContext for backward compat.

import fnmatch
import os
import shutil
import stat as _stat
from pathlib import Path
from typing import Optional

try:
    import paramiko
    _PARAMIKO_AVAILABLE = True
except ImportError:
    _PARAMIKO_AVAILABLE = False


# ─── coercion helper (backward compat) ───────────────────────────────────────

def _coerce(context):
    """
    Accept a raw pathlib.Path and wrap it in LocalContext.
    Already-wrapped contexts are passed through unchanged.
    """
    if isinstance(context, Path):
        return LocalContext(context)
    return context


# ─── LocalContext ─────────────────────────────────────────────────────────────

class LocalContext:
    """File access on the local filesystem."""

    def __init__(self, root: Path):
        self._root = Path(root).resolve()

    # ── context manager (no-op — mirrors SSHContext interface) ────────────
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    # ── helpers ───────────────────────────────────────────────────────────
    def _abs(self, rel: str) -> Path:
        return self._root / rel

    # ── identity ──────────────────────────────────────────────────────────
    def resolve_str(self) -> str:
        """Human-readable path, shown in agent task descriptions."""
        return str(self._root)

    # ── discovery ─────────────────────────────────────────────────────────
    def glob_logs(self) -> list[tuple[str, float]]:
        """Return (filename, mtime) for every log.* / log file at case root."""
        entries = []
        for p in self._root.glob("log.*"):
            entries.append((p.name, p.stat().st_mtime))
        bare = self._root / "log"
        if bare.exists():
            entries.append(("log", bare.stat().st_mtime))
        return entries

    def listdir(self, rel_path: str = ".") -> list[tuple[str, bool]]:
        """Return (name, is_dir) sorted by name for the given relative path."""
        p = self._abs(rel_path)
        if not p.is_dir():
            return []
        try:
            return [(i.name, i.is_dir()) for i in sorted(p.iterdir())]
        except PermissionError:
            return [("(permission denied)", False)]

    # ── I/O ───────────────────────────────────────────────────────────────
    def exists(self, rel_path: str) -> bool:
        return self._abs(rel_path).exists()

    def read_text(self, rel_path: str) -> str:
        return self._abs(rel_path).read_text(encoding="utf-8", errors="replace")

    def write_text(self, rel_path: str, content: str) -> None:
        self._abs(rel_path).write_text(content, encoding="utf-8")

    def copy2(self, src_rel: str, dst_rel: str) -> None:
        shutil.copy2(self._abs(src_rel), self._abs(dst_rel))

    def tail_bytes(self, rel_path: str, n_bytes: int) -> bytes:
        """
        Read approximately the last n_bytes from a (potentially large) file.
        More efficient than read_text() when only the tail is needed.
        """
        p = self._abs(rel_path)
        size = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, size - int(n_bytes)))
            return f.read()

    def remove(self, rel_path: str) -> None:
        """Delete a file (not a directory)."""
        self._abs(rel_path).unlink()


# ─── SSHContext ───────────────────────────────────────────────────────────────

class SSHContext:
    """
    File access on a remote host via paramiko SFTP.

    Parameters
    ----------
    hostname     : remote host (IP or DNS name)
    remote_root  : absolute path to the OpenFOAM case on the remote host
    user         : SSH username (defaults to current local user)
    port         : SSH port (default 22)
    key_filename : path to a private key file (~/.ssh/id_rsa, etc.)
    password     : SSH password — prefer key auth on shared HPC systems
    timeout      : connection timeout in seconds

    Usage as context manager (recommended — ensures connection is closed):

        with SSHContext("compute-node", "/scratch/runs/cavity",
                        user="cfd", key_filename="~/.ssh/id_rsa") as ctx:
            run_agent(cfg, user_task=...)

    Manual usage:

        ctx = SSHContext(...)
        try:
            run_agent(cfg, user_task=...)
        finally:
            ctx.close()
    """

    def __init__(
        self,
        hostname:     str,
        remote_root:  str,
        user:         Optional[str]  = None,
        port:         int            = 22,
        key_filename: Optional[str]  = None,
        password:     Optional[str]  = None,
        timeout:      float          = 30.0,
    ):
        if not _PARAMIKO_AVAILABLE:
            raise ImportError(
                "paramiko is required for SSH mode.\n"
                "Install it with: pip install paramiko"
            )

        self.hostname     = hostname
        self._remote_root = remote_root.rstrip("/")

        client = paramiko.SSHClient()
        # Honour the user's known_hosts so previously-seen hosts are trusted,
        # then fall back to auto-adding unknown ones.
        try:
            client.load_system_host_keys()
        except Exception:
            pass
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kw: dict = dict(hostname=hostname, port=port, timeout=timeout)
        if user:
            connect_kw["username"] = user
        if key_filename:
            connect_kw["key_filename"] = os.path.expanduser(key_filename)
        if password:
            connect_kw["password"] = password

        # Resolve aliases from ~/.ssh/config (HostName, User, Port, IdentityFile,
        # ProxyCommand, ...) so that `alias:/path` works just like the `ssh`
        # command. CLI arguments (user, port, key) always take precedence.
        sock = self._apply_ssh_config(hostname, connect_kw)

        # Let paramiko look at the SSH agent and the default keys (~/.ssh/id_*)
        # when no explicit key/password was given — this is what makes plain
        # `alias:/path` or `user@host:/path` work with an existing key.
        connect_kw.setdefault("allow_agent", True)
        connect_kw.setdefault("look_for_keys", True)
        if sock is not None:
            connect_kw["sock"] = sock

        client.connect(**connect_kw)
        self._client = client
        self._sftp   = client.open_sftp()

    @staticmethod
    def _apply_ssh_config(host: str, connect_kw: dict):
        """
        Merge settings from ~/.ssh/config for `host` into connect_kw, without
        overriding anything the caller already supplied on the command line.
        Returns a ProxyCommand socket if the config defines one, else None.
        """
        cfg_path = os.path.expanduser("~/.ssh/config")
        if not os.path.exists(cfg_path):
            return None

        ssh_cfg = paramiko.SSHConfig()
        try:
            with open(cfg_path) as f:
                ssh_cfg.parse(f)
        except Exception:
            return None

        opts = ssh_cfg.lookup(host)

        # Real hostname behind the alias.
        if "hostname" in opts:
            connect_kw["hostname"] = opts["hostname"]
        # Only fill in values the user did not pass explicitly.
        if "user" in opts and "username" not in connect_kw:
            connect_kw["username"] = opts["user"]
        if "port" in opts and connect_kw.get("port") in (None, 22):
            connect_kw["port"] = int(opts["port"])
        if "identityfile" in opts and "key_filename" not in connect_kw:
            # paramiko returns a list of identity files.
            connect_kw["key_filename"] = [
                os.path.expanduser(p) for p in opts["identityfile"]
            ]

        proxy = opts.get("proxycommand")
        if proxy:
            return paramiko.ProxyCommand(proxy)
        return None

    # ── context manager ───────────────────────────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        for obj in (self._sftp, self._client):
            try:
                obj.close()
            except Exception:
                pass

    # ── helpers ───────────────────────────────────────────────────────────
    def _abs(self, rel_path: str) -> str:
        """Resolve a relative path against the remote case root."""
        if rel_path in ("", "."):
            return self._remote_root
        return f"{self._remote_root}/{rel_path.lstrip('/')}"

    # ── identity ──────────────────────────────────────────────────────────
    def resolve_str(self) -> str:
        return f"{self.hostname}:{self._remote_root}"

    # ── discovery ─────────────────────────────────────────────────────────
    def glob_logs(self) -> list[tuple[str, float]]:
        try:
            attrs = self._sftp.listdir_attr(self._remote_root)
        except Exception:
            return []
        return [
            (a.filename, float(a.st_mtime or 0))
            for a in attrs
            if fnmatch.fnmatch(a.filename, "log.*") or a.filename == "log"
        ]

    def listdir(self, rel_path: str = ".") -> list[tuple[str, bool]]:
        remote = self._abs(rel_path)
        try:
            attrs = self._sftp.listdir_attr(remote)
        except FileNotFoundError:
            return []
        result = []
        for a in sorted(attrs, key=lambda x: x.filename):
            is_dir = bool(a.st_mode and _stat.S_ISDIR(a.st_mode))
            result.append((a.filename, is_dir))
        return result

    # ── I/O ───────────────────────────────────────────────────────────────
    def exists(self, rel_path: str) -> bool:
        try:
            self._sftp.stat(self._abs(rel_path))
            return True
        except FileNotFoundError:
            return False

    def read_text(self, rel_path: str) -> str:
        with self._sftp.open(self._abs(rel_path), "r") as f:
            return f.read().decode("utf-8", errors="replace")

    def write_text(self, rel_path: str, content: str) -> None:
        """
        Atomic write: upload to a temp file, then rename.
        On Linux (all HPC systems where OpenFOAM runs), rename(2) is atomic,
        so OpenFOAM will either see the old or new controlDict — never a partial write.
        """
        remote  = self._abs(rel_path)
        tmp     = remote + ".ofagent_tmp"
        try:
            with self._sftp.open(tmp, "w") as f:
                f.write(content.encode("utf-8"))
            # Some SFTP servers refuse to rename over an existing file.
            try:
                self._sftp.remove(remote)
            except FileNotFoundError:
                pass
            self._sftp.rename(tmp, remote)
        except Exception:
            # Clean up temp file on failure.
            try:
                self._sftp.remove(tmp)
            except Exception:
                pass
            raise

    def copy2(self, src_rel: str, dst_rel: str) -> None:
        """Remote copy (SFTP has no server-side copy: download + re-upload)."""
        data = self._sftp.open(self._abs(src_rel), "r").read()
        with self._sftp.open(self._abs(dst_rel), "w") as f:
            f.write(data)

    def tail_bytes(self, rel_path: str, n_bytes: int) -> bytes:
        """
        Efficiently read the last n_bytes of a remote file.
        Avoids downloading the whole file for large logs (> 100 MB).
        """
        remote = self._abs(rel_path)
        attr   = self._sftp.stat(remote)
        size   = attr.st_size or 0
        with self._sftp.open(remote, "rb") as f:
            f.seek(max(0, size - int(n_bytes)))
            return f.read()

    def remove(self, rel_path: str) -> None:
        """Delete a remote file."""
        self._sftp.remove(self._abs(rel_path))
