"""Benchmark action using cargo criterion.
"""
from pathlib import Path
import shutil
import subprocess as sp
from .utils import config_git, switch_branch


def _copy_last_dev_bench(branch: str) -> None:
    switch_branch("gh-pages")
    src = Path("dev/criterion")
    if src.is_dir():
        target = Path("target/criterion")
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, dirs_exist_ok=True)
    switch_branch(branch)


def _cargo_criterion(branch: str) -> None:
    """Run `cargo criterion` to benchmark the specified branch.

    :param branch: The branch to benchmark.
    """
    _copy_last_dev_bench(branch)
    cmd = "cargo criterion --message-format=json"
    sp.run(cmd, shell=True, check=True)


def _copy_bench_results(dir_: str | Path) -> None:
    switch_branch("gh-pages")
    if isinstance(dir_, str):
        dir_ = Path(dir_)
    src = Path("target/criterion")
    dst = dir_ / "criterion"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _git_push_gh_pages(dir_: str | Path) -> None:
    cmd = f"""git add index.md {dir_} \
        && git commit -m 'add benchmarks' \
        && git push origin gh-pages
        """
    sp.run(cmd, shell=True, check=True)


def _rename_bench_reports(dirs: list[Path]):
    for dir_ in dirs:
        for path in dir_.glob("**/history.html"):
            path.rename(path.with_name("index.html"))


def _parse_metric_ci(path: str | Path) -> tuple[str, str, str]:
    if isinstance(path, str):
        path = Path(path)
    if path.is_dir():
        path = path / "index.html"
    with path.open(encoding="utf-8") as fin:
        lines = fin.readlines()

    def _find_start_line(lines: list[str]) -> int | None:
        for i, line in enumerate(lines):
            if "Change in Value:" in line:
                return i
        return None

    def _extract_value_from_line(line: str):
        start = line.find(">")
        if start == -1:
            return "0"
        end = line.find("<", start + 1)
        if end == -1:
            return "0"
        return line[start + 1 : end].strip()

    start = _find_start_line(lines)
    if start is None:
        return ("0", "0", "0")
    lower = _extract_value_from_line(lines[start + 1])
    middle = _extract_value_from_line(lines[start + 2])
    upper = _extract_value_from_line(lines[start + 3])
    return (lower, middle, upper)


def _gen_report_links_markdown(dir_: str | Path) -> str:
    if isinstance(dir_, str):
        dir_ = Path(dir_)
    paths = (dir_ / "criterion/reports").iterdir()
    paths = list(path for path in paths if path.is_dir())
    paths.sort()
    cips = [(_parse_metric_ci(p), p) for p in paths]
    links = "\n".join(_gen_report_link_markdown(cip) for cip in cips)
    links_sorted = "\n".join(_gen_report_link_markdown(cip) for cip in _sort_cips(cips))
    return f"""## {dir_} - Sorted By Performance Change
{links_sorted}
## {dir_} - Sorted By Name
{links}
"""


def _gen_report_link_markdown(cip: tuple[tuple[str, str, str], Path]) -> str:
    ci, p = cip

    def _gen_link_md(path: Path):
        text = path.stem.replace(" __ ", " << ").replace("__", "::")
        link = f"{path}/index.html"
        return f"[{text}]({link})"

    return f"- {_gen_report_ci_markdown(ci)}  {_gen_link_md(p)}"


def _gen_report_ci_markdown(ci: tuple[str, str, str]) -> str:
    lower, middle, upper = ci

    def _significance(lower: str, middle: str, upper: str) -> int:
        def _reg_value(value: str):
            return float(value.strip("%").strip())

        lower = _reg_value(lower)
        middle = _reg_value(middle)
        upper = _reg_value(upper)
        if abs(middle) < 1:
            return 0
        if lower < 0 and upper < 0:
            return -1
        if lower > 0 and upper > 0:
            return 1
        return 0

    color = {0: "black", -1: "green", 1: "red"}[_significance(lower, middle, upper)]
    return f'<span style="color:{color}"> [{lower}, <b>{middle}</b>, {upper}] </span>'


def _sort_cips(
    cips: list[tuple[tuple[str, str, str], Path]]
) -> list[tuple[tuple[str, str, str], Path]]:
    """Sort confidence intervals according to the average performance changes."""

    def _avg_perf_change(elem: tuple[tuple[str, str, str], Path]) -> float:
        perf_change = elem[0][1]
        return -float(perf_change.replace("%", "").strip())

    return sorted(cips, key=_avg_perf_change)


def _get_bench_dirs() -> list[Path]:
    dirs = list(p / "merge" for p in Path(".").glob("[1-9]*/"))
    dirs.sort()
    dirs.append(Path("dev"))
    return dirs


def _gen_markdown(dirs: list[Path]) -> str:
    sections = "\n".join(_gen_report_links_markdown(dir_) for dir_ in reversed(dirs))
    return f"# [poker-rs](https://github.com/fun-poker-game/poker-rs)\n{sections}\n"


def benchmark(dir_: str, branch: str, user_email: str, user_name: str):
    """Benchmark using `cargo criterion` and push benchmark results to gh-pages.

    :param dir_: _description_
    """
    config_git(root_dir=dir_, user_email=user_email, user_name=user_name)
    _cargo_criterion(branch)
    _copy_bench_results(dir_=dir_)
    dirs = _get_bench_dirs()
    _rename_bench_reports(dirs)
    Path("index.md").write_text(_gen_markdown(dirs), encoding="utf-8")
    _git_push_gh_pages(dir_)
