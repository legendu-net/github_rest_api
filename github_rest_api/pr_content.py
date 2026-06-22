"""Generate pull request titles and descriptions from a comparison result.

The functions here operate on the JSON returned by the GitHub "compare two
commits" endpoint (see `Repository.compare`). They provide two layers:

- A deterministic layer (`deterministic_title` / `deterministic_body`) that
  derives a Conventional-Commits title and a Markdown body purely from commit
  messages and changed files. It has no extra dependencies and is always
  available.
- An optional AI layer (`generate_pr_content`) that uses LiteLLM to produce a
  richer title and description. It raises on any failure (missing `litellm`,
  missing provider key, malformed reply) so the caller can fall back to the
  deterministic layer.
"""

import json
import re
from collections import Counter
from typing import Any, cast

_CONVENTIONAL_PATTERN = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?: (?P<description>.+)$"
)
# The Conventional Commits spec allows the breaking-change footer token to be
# spelled either "BREAKING CHANGE" or "BREAKING-CHANGE".
_BREAKING_CHANGE_PATTERN = re.compile(r"BREAKING[ -]CHANGE")


def parse_conventional(subject: str) -> tuple[str, str | None, bool, str] | None:
    """Parse a Conventional-Commits subject line.

    :param subject: The first line of a commit message.
    :return: A `(type, scope, breaking, description)` tuple, or None when the
        subject does not follow the Conventional-Commits grammar.
    """
    match = _CONVENTIONAL_PATTERN.match(subject)
    if match is None:
        return None
    return (
        match["type"],
        match["scope"],
        bool(match["breaking"]),
        match["description"],
    )


def _commit_messages(compare: dict[str, Any], skip_merges: bool = True) -> list[str]:
    """Extract commit messages from a comparison result.

    :param compare: The comparison result from `Repository.compare`.
    :param skip_merges: Whether to skip merge commits (subjects starting with
        ``Merge ``).
    """
    messages = []
    for commit in compare.get("commits") or []:
        message = (commit.get("commit") or {}).get("message", "")
        if not message:
            continue
        subject = message.splitlines()[0].strip()
        if skip_merges and subject.startswith("Merge "):
            continue
        messages.append(message)
    return messages


def _common_scope(compare: dict[str, Any]) -> str | None:
    """Derive a Conventional-Commits scope from the changed files.

    The scope is the common top-level directory shared by every changed file,
    or None when the files do not share one (or live at the repository root).
    """
    filenames = [
        file["filename"] for file in compare.get("files") or [] if file.get("filename")
    ]
    if not filenames or not all("/" in name for name in filenames):
        return None
    segments = {name.split("/", 1)[0] for name in filenames}
    return next(iter(segments)) if len(segments) == 1 else None


def deterministic_title(compare: dict[str, Any]) -> str:
    """Derive a Conventional-Commits title from a comparison result.

    The type is the most significant type present across the commits
    (``feat`` > ``fix`` > the most frequent parsed type > ``chore``); the scope
    is the common top-level directory of the changed files; ``!`` is appended
    for breaking changes.

    :param compare: The comparison result from `Repository.compare`.
    """
    messages = _commit_messages(compare)
    subjects = [message.splitlines()[0].strip() for message in messages]
    parsed = [parse_conventional(subject) for subject in subjects]
    types = [item[0] for item in parsed if item]
    scope = _common_scope(compare)
    prefix_scope = f"({scope})" if scope else ""
    if not subjects:
        return f"chore{prefix_scope}: update"
    if "feat" in types:
        type_ = "feat"
    elif "fix" in types:
        type_ = "fix"
    elif types:
        type_ = Counter(types).most_common(1)[0][0]
    else:
        type_ = "chore"
    breaking = any(item[2] for item in parsed if item) or any(
        _BREAKING_CHANGE_PATTERN.search(message) for message in messages
    )
    if len(subjects) == 1:
        description = parsed[0][3] if parsed[0] else subjects[0]
    else:
        description = next(
            (item[3] for item in parsed if item and item[0] == type_),
            f"update {len(subjects)} commits",
        )
    return f"{type_}{prefix_scope}{'!' if breaking else ''}: {description}"


def deterministic_body(compare: dict[str, Any]) -> str:
    """Build a Markdown PR body from a comparison result.

    :param compare: The comparison result from `Repository.compare`.
    """
    sections = []
    subjects = [
        message.splitlines()[0].strip() for message in _commit_messages(compare)
    ]
    if subjects:
        summary = "\n".join(f"- {subject}" for subject in subjects)
        sections.append(f"## Summary\n\n{summary}")
    files = compare.get("files") or []
    if files:
        # Conventional-Commit statuses ordered for a stable, readable "Changed
        # files" section. Any status not listed is appended afterwards.
        status_order = ("added", "modified", "removed", "renamed", "copied", "changed")
        by_status: dict[str, list[dict[str, Any]]] = {}
        for file in files:
            by_status.setdefault(file.get("status") or "modified", []).append(file)
        ordered = [status for status in status_order if status in by_status]
        ordered += [status for status in by_status if status not in status_order]
        lines = []
        for status in ordered:
            lines.append(f"**{status.capitalize()}**")
            for file in by_status[status]:
                lines.append(
                    f"- `{file.get('filename', '')}` "
                    f"(+{file.get('additions', 0)}/-{file.get('deletions', 0)})"
                )
        sections.append("## Changed files\n\n" + "\n".join(lines))
    commits = compare.get("commits") or []
    if commits:
        lines = []
        for commit in commits:
            sha = (commit.get("sha") or "")[:7]
            message = (commit.get("commit") or {}).get("message", "")
            subject = message.splitlines()[0].strip() if message else ""
            lines.append(f"- {sha} {subject}")
        sections.append("## Commits\n\n" + "\n".join(lines))
    return "\n\n".join(sections)


def summarize_for_ai(compare: dict[str, Any], max_chars: int = 12000) -> str:
    """Assemble a compact, size-capped context for the AI prompt.

    Combines the deterministic body with per-file patches, truncating once the
    character budget is exhausted so very large diffs do not blow up the prompt.

    :param compare: The comparison result from `Repository.compare`.
    :param max_chars: The approximate maximum size of the returned context.
    """
    parts = [deterministic_body(compare), "## Diff"]
    budget = max_chars - sum(len(part) for part in parts)
    for file in compare.get("files") or []:
        patch = file.get("patch")
        if not patch:
            continue
        chunk = f"### {file.get('filename', '')}\n{patch}"
        if len(chunk) > budget:
            parts.append("<remaining patches omitted: size limit reached>")
            break
        parts.append(chunk)
        budget -= len(chunk)
    return "\n\n".join(parts)


def generate_pr_content(compare: dict[str, Any], model: str) -> tuple[str, str]:
    """Generate a PR title and body with an LLM via LiteLLM.

    :param compare: The comparison result from `Repository.compare`.
    :param model: A LiteLLM ``provider/model`` string (e.g.
        ``anthropic/claude-haiku-4-5-20251001``, ``gemini/gemini-2.5-flash``).
        The matching provider API key is read from the environment by LiteLLM.
    :return: A `(title, body)` tuple.
    :raises Exception: If LiteLLM is not installed, no provider key is set, the
        request fails, or the reply cannot be parsed. The caller is expected to
        fall back to the deterministic layer.
    """
    import litellm

    prompt = (
        "You are writing a GitHub pull request from the changes below. "
        "Respond with a JSON object containing exactly two string keys: "
        "'title' and 'body'. The 'title' must be a single concise line "
        "following the Conventional Commits specification "
        "(e.g. 'feat(scope): summary'). The 'body' must be GitHub-flavored "
        "Markdown describing the motivation and the key changes.\n\n"
        f"{summarize_for_ai(compare)}"
    )
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        # Drop response_format for providers that do not support it rather than
        # erroring; the prompt already requests JSON, so parsing still works.
        drop_params=True,
    )
    response_any = cast(Any, response)
    data = json.loads(response_any["choices"][0]["message"]["content"])
    if not isinstance(data, dict):
        raise ValueError("The model did not return a JSON object.")
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    if not title or not body:
        raise ValueError("The model returned an empty title or body.")
    return title, body
