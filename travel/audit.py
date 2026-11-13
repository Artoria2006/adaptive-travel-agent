import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from . import __version__

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def git(*args):
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL, timeout=2).decode().strip()
    except (OSError, subprocess.SubprocessError):
        return None


def json_read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class Audit:
    def __init__(self, root, previous_run=None):
        self.run_id = uuid4().hex
        self.path = root / self.run_id
        self.path.mkdir(parents=True)
        self.write("request", {"previous_run": previous_run, "retention": "raw natural language omitted"})
        self.write("task_graph", [])
        self.write("routing_trace", [])
        self.write("evidence", {})
        self.events = []
        self.routes = []
        (self.path / "events.jsonl").touch()

    def write(self, name, value):
        destination = self.path / (name + ".json")
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)

    def emit(self, kind, task, status, expert=None, **fields):
        agent_id = expert.agent_id if expert else fields.pop("agent_id", None)
        version = expert.version if expert else fields.pop("agent_version", None)
        event = {"sequence": len(self.events) + 1, "timestamp": datetime.now(UTC).isoformat(),
                 "event": kind, "task_id": task, "status": status,
                 "agent_id": agent_id, "agent_version": version, **fields}
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.events.append(event)
        if kind == "routing":
            self.routes.append(event)
            self.write("routing_trace", self.routes)

    def manifest(self, registry, supervisor):
        lock = ROOT / "requirements.txt"
        sources = sorted((ROOT / "travel").glob("*.py")) + sorted((ROOT / "web").rglob("*"))
        sources.append(ROOT / "run.py")
        return {"run_id": self.run_id, "build": __version__, "commit": git("rev-parse", "HEAD"),
                "dirty": bool(git("status", "--porcelain")),
                "source_hash": digest({file.relative_to(ROOT).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
                                       for file in sources if file.is_file()}),
                "dependency_lock_hash": hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None,
                "supervisor": supervisor, "experts": [expert.manifest() for expert in registry.experts.values()]}


def replay(path):
    result = {key: json_read(path / f"{key}.json") for key in
              ("request", "manifest", "task_graph", "routing_trace", "evidence", "final_plan")
              if (path / f"{key}.json").exists()}
    result["events"] = [json.loads(line) for line in (path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    result["mode"] = "audit_replay"
    return result
