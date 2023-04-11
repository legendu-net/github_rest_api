"""Utils for profiling Rust applications.
"""
from pathlib import Path
import datetime
import subprocess as sp
import psutil


def launch_application(cmd: list[str]) -> int:
    """Launch the application to be profiled and return the pid of the process.
    Notice that this works inside Docker containers too.
    """
    print(
        "Launched application for profiling at ",
        datetime.datetime.now(),
        " using the following command:\n",
        " ".join(cmd),
        "\n",
        sep="",
    )
    proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE)  # pylint: disable=R1732
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
    cmd = f"nperf record -p {pid} -o {data_file}"
    print(
        "Started profiling at ",
        datetime.datetime.now(),
        " using the following command:\n",
        cmd,
        "\n",
        sep="",
    )
    sp.run(cmd, shell=True, check=True)
    return _gen_flamegraph(data_file)


def _gen_flamegraph(data_file: Path) -> Path:
    flamegraph = data_file.with_name(data_file.name + ".svg")
    cmd = f"nperf flamegraph {data_file} > {flamegraph}"
    print(
        "Started generating flamegraph at ",
        datetime.datetime.now(),
        " using the following command:\n",
        cmd,
        "\n",
        sep="",
    )
    sp.run(cmd, shell=True, check=True)
    return flamegraph
