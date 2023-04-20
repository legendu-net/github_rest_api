"""Utils for profiling Rust applications.
"""
from typing import Iterable
from pathlib import Path
import datetime
import subprocess as sp
import psutil
from .utils import build_project
from ..utils import config_git, switch_branch, push_branch, commit_profiling
from ...utils import partition, run_cmd


def launch_application(cmd: list[str]) -> int:
    """Launch the application to be profiled and return the pid of the process.
    Notice that this works inside Docker containers too.
    """
    print(
        "Launched application for profiling at ",
        datetime.datetime.now(),
        " using the following command:\n",
        "    sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE)\n",
        "where cmd is as below:\n    ",
        cmd,
        "\n",
        sep="",
    )
    proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE)  # pylint: disable=R1732
    status = proc.poll()
    if status:
        stdout, stderr = proc.communicate()
        print(
            "The launched application failed with the error code ",
            status,
            "!\nStdout:\n",
            stdout.decode(),
            "\nStderr:\n",
            stderr.decode(),
            sep="",
        )
    if status == 0:
        raise ValueError(
            "The launched application has already finished! "
            "Please use a long running command instead."
        )
    return _find_process_id(proc)


def _find_process_id(proc: sp.Popen) -> int:
    pids = []
    for process in psutil.process_iter():
        if process.cmdline() == proc.args:
            pids.append(process.pid)
    return min(pids, key=lambda pid: abs(pid - proc.pid))


def nperf(pid: int, prof_name: str, prof_dir: str | Path = ".") -> Path:
    """Profile the given process using nperf (not-perf).
    :param pid: The id of the process to be profiled.
    :param prof_name: The name of the profiling.
    :param prof_dir: The directory (the current working directory by default)
    for saving profiling data.
    """
    if isinstance(prof_dir, str):
        prof_dir = Path(prof_dir)
    time = datetime.datetime.now()
    yymmdd = time.strftime("%Y%m%d")
    prof_dir.mkdir(exist_ok=True, parents=True)
    data_file = prof_dir / f"{yymmdd}_{prof_name}"
    run_cmd(f"nperf record -p {pid} -o '{data_file}'")
    return _gen_flamegraph(data_file)


def _gen_flamegraph(data_file: Path) -> Path:
    flamegraph = data_file.with_name(data_file.name + ".svg")
    run_cmd(f"nperf flamegraph '{data_file}' > '{flamegraph}'")
    return flamegraph


def _save_flamegraph(prof_dir: Path, history: int = 5):
    yyyymmdd = (datetime.datetime.today() - datetime.timedelta(days=history)).strftime(
        "%Y%m%d"
    )
    switch_branch("gh-pages", fetch=True)
    for path in prof_dir.iterdir():
        if path.suffix == "":
            path.unlink()
    svgs_keep, svgs_drop = partition(
        lambda p: p.name > yyyymmdd, prof_dir.glob("*.svg")
    )
    for svg in svgs_drop:
        svg.unlink()
    _gen_markdown(svgs=sorted(svgs_keep, reverse=True), prof_dir=prof_dir)
    commit_profiling(prof_dir=prof_dir)
    yyyymmdd = datetime.datetime.now().strftime("%Y%m%d")
    push_branch(branch="gh-pages", branch_alt="gh-pages_prof_" + yyyymmdd)


def _gen_markdown(svgs: Iterable[Path], prof_dir: Path) -> None:
    def _gen_link(svg: Path):
        svg = svg.name
        yyyymmdd = svg[:8]
        prof_name = svg[9:-4]
        return f"- [{prof_name} - {yyyymmdd}]({svg})"

    links = "\n".join(_gen_link(svg) for svg in svgs)
    markdown = f"# Profiling\n{links}"
    (prof_dir / "index.md").write_text(markdown, encoding="utf-8")


def profiling(
    local_repo_dir: str | Path,
    apps: dict[str, list[str]],
    profile: str = "release",
    prof_dir: str | Path = "profiling",
):
    """Profiling specified applications."""
    if isinstance(prof_dir, str):
        prof_dir = Path(prof_dir)
    config_git(
        local_repo_dir=local_repo_dir,
        user_email="profiling-bot@github.com",
        user_name="profiling-bot",
    )
    build_project(profile=profile)
    for name, cmd in apps.items():
        pid = launch_application(cmd=cmd)
        nperf(pid=pid, prof_name=name, prof_dir=prof_dir)
    _save_flamegraph(prof_dir=prof_dir)
