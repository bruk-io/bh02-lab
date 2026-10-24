"""The judge's harness: runs a task's check cases against a workspace and reports what each step
did, never whether it was right.

It runs inside the judge's check sandbox, with the standard library only, and is handed the cases
without their expected values: the expected values never enter a sandbox. The judge function,
outside, compares what it observed with what it expected. So code in the workspace can make a
step return anything, print anything, or crash the harness, and none of that is a pass: to pass,
it has to return the right answers, which is solving the task.

Input, on stdin or in the file `--request PATH` names, one JSON object: `work` (the workspace
directory), `tar` (optional: a canonical snapshot tar to extract into `work` first, before any
workspace code runs), `cases` (each a list of steps), `timeout_s` (per case). Output, in the file
`report` names (else on stdout): one line per case, `CASE <n> <json>`, the case's observations.
Run as root, as the judge runs it, each case runs as `nobody` under limits on memory, processes,
file size and CPU, so its code can't reach the report, the harness or another case.

Each case runs in a fresh child process, isolated (`-I`), with the workspace first on its import
path and its own empty working directory. A step is one of:

- `{"call": "module:name", "args": [...], "kwargs": {...}}`: call a function or class;
- `{"on": "x", "method": "m", "args": [...]}`: call a method of a value bound earlier;
- `{"len": "x"}`: `len()` of a value bound earlier;
- `{"write": "path", "text": "..."}`: write a file in the case's working directory;

and may add `"bind": "x"` (keep the result for later steps) or `"capture_stdout": true`. An
observation is `{"i": step, "returned": value}` (JSON-encoded, else `{"repr": ...}`), or
`{"i": step, "raised": [class names, most specific first]}`, with `"stdout"` when captured.
"""

import contextlib
import functools
import importlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
from typing import Any, TextIO

__all__ = ["main"]

_MAX_REPORT = 4 * 1024 * 1024  # what a case may report; the rest is not read
_NOBODY = 65534
_MEMORY = 1024 * 1024 * 1024
_PROCESSES = 64
_FILE = 64 * 1024 * 1024
_CPU_S = 60


def _encoded(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return {"repr": repr(value)}


def _child(sink: Any, steps: list[dict[str, Any]]) -> None:
    bound: dict[str, Any] = {}
    for i, step in enumerate(steps):
        seen: dict[str, Any] = {"i": i}
        buffer = io.StringIO()
        try:
            if "write" in step:
                with open(step["write"], "w", encoding="utf-8") as file:
                    file.write(step.get("text", ""))
                result = None
            else:
                if "call" in step:
                    module, _, name = step["call"].partition(":")
                    target = getattr(importlib.import_module(module), name)
                elif "on" in step:
                    target = getattr(bound[step["on"]], step["method"])
                else:
                    target = functools.partial(len, bound[step["len"]])

                args, kwargs = step.get("args", []), step.get("kwargs", {})
                if step.get("capture_stdout"):
                    with contextlib.redirect_stdout(buffer):
                        result = target(*args, **kwargs)
                else:
                    result = target(*args, **kwargs)
            if "bind" in step:
                bound[step["bind"]] = result
            else:
                seen["returned"] = _encoded(result)
        except BaseException as error:  # the step's outcome, whatever it raised
            seen["raised"] = [cls.__name__ for cls in type(error).__mro__]
        if step.get("capture_stdout"):
            seen["stdout"] = buffer.getvalue()
        sink.write(json.dumps(seen) + "\n")
        sink.flush()


def _confined(command: list[str], cwd: str) -> list[str]:
    """As root (the judge's sandbox), a case runs as `nobody` under resource limits, so it can't
    touch the harness, its report or another case; as anyone else (a developer's tests), as is."""
    if os.geteuid() != 0 or not (shutil.which("setpriv") and shutil.which("prlimit")):
        return command
    os.chown(cwd, _NOBODY, _NOBODY)
    limits = [f"--as={_MEMORY}", f"--nproc={_PROCESSES}", f"--fsize={_FILE}", f"--cpu={_CPU_S}", "--core=0"]
    owner = ["setpriv", f"--reuid={_NOBODY}", f"--regid={_NOBODY}", "--clear-groups"]
    return ["prlimit", *limits, "--", *owner, *command]


def _end_nobodys() -> None:
    """Kill whatever a case left running as `nobody`, wherever it moved to (a new session too)."""
    if os.geteuid() != 0:
        return
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            with contextlib.suppress(OSError):
                if os.stat(f"/proc/{entry}").st_uid == _NOBODY:
                    os.kill(int(entry), signal.SIGKILL)


def _run_case(work: str, steps: list[dict[str, Any]], timeout_s: float) -> list[dict[str, Any]]:
    read, write = os.pipe()
    with tempfile.TemporaryDirectory() as cwd:
        try:
            command = [sys.executable, "-I", os.path.abspath(__file__), "--child", str(write), work]
            child = subprocess.Popen(
                _confined(command, cwd),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=cwd, pass_fds=(write,), text=True, start_new_session=True,
            )  # fmt: skip
        finally:
            os.close(write)
        assert child.stdin is not None
        with contextlib.suppress(BrokenPipeError):
            child.stdin.write(json.dumps(steps))
            child.stdin.close()
        chunks: list[str] = []
        with os.fdopen(read, encoding="utf-8", errors="replace") as source:
            reader = threading.Thread(target=lambda: chunks.append(source.read(_MAX_REPORT)), daemon=True)
            reader.start()
            with contextlib.suppress(subprocess.TimeoutExpired):
                child.wait(timeout_s)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)  # the case and anything it started
            _end_nobodys()
            child.wait()
            reader.join(5)
        seen = []
        for text in "".join(chunks).splitlines():
            with contextlib.suppress(ValueError):
                record = json.loads(text)
                if isinstance(record, dict):
                    seen.append(record)
    return seen


def main() -> int:
    """Run every case and print its observations, one line per case."""
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        channel = os.fdopen(int(sys.argv[2]), "w", encoding="utf-8")
        sys.path.insert(0, sys.argv[3])
        _child(channel, json.loads(sys.stdin.read()))
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "--request":
        with open(sys.argv[2], encoding="utf-8") as file:
            request = json.load(file)
    else:
        request = json.loads(sys.stdin.read())
    work = request["work"]
    if request.get("tar"):
        os.makedirs(work, exist_ok=True)
        with tarfile.open(request["tar"]) as archive:
            archive.extractall(work, filter="data")
    report = request.get("report")
    with contextlib.ExitStack() as stack:
        sink: TextIO = sys.stdout
        if report:
            sink = stack.enter_context(open(report, "w", encoding="utf-8"))
            os.chmod(report, 0o600)
        for n, steps in enumerate(request["cases"]):
            seen = _run_case(work, steps, float(request.get("timeout_s", 20)))
            sink.write(f"CASE {n} {json.dumps(seen)}\n")
            sink.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
