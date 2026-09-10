"""The Git workspace a Product Runtime run executes in, and what changed in it.

Two facts, deliberately not one field
-------------------------------------
:class:`BoundWorkspace` records the **execution base**: at bind time, ``path``
was a Git repository root whose ``HEAD`` was ``head_commit`` and whose working
tree was clean. That is where execution *started*.

Contract V1 §4.1 defines ``source_revision`` differently — it "identifies the
exact source output represented by the report", which is where execution
*ended*. Publishing the base as that field would describe source the report
does not represent, so the two are kept apart by name and by API:
:attr:`BoundWorkspace.execution_base_revision` is the input,
:meth:`BoundWorkspace.resolve_output_revision` is the output, and only the
second is eligible for the report.

Resolving the output revision fails closed. A commit describes the exact output
only when the tree is clean; §5.2/§5.3 keep ``PATCH_SHA256`` and
``WORKTREE_SNAPSHOT_SHA256`` ``RESERVED_NOT_ACTIVE`` and forbid inventing a
local canonicalization for the difference. A coding worker normally leaves the
tree dirty, so the normal answer is ``None`` and the report is unassessable.
That is the contract working.

Evidence is base-relative, and renames are decomposed
-----------------------------------------------------
:func:`collect_workspace_delta` reports every path changed since the bound base
— committed, staged, unstaged and untracked. ``git status`` alone is not enough:
a worker that commits its work leaves a clean-looking status while having
changed the source.

Rename detection is switched **off** (``--no-renames``) on purpose. With it on,
moving ``outside.txt`` to ``src/renamed.py`` is reported as one ``R`` record
naming the destination, and the fact that a path outside the allowed scope was
*removed* disappears. With it off the same move is two records, ``D
outside.txt`` and ``A src/renamed.py``, so both effects reach scope validation.
For authority decisions, seeing both halves matters more than knowing they were
one operation.

Scope of the guarantee
----------------------
Verification is process-local and time-of-check: the checks here re-read the
repository when called, which narrows the window between binding and use but
cannot close it against a concurrent external writer. No hostile-writer
resistance is claimed. Repository *identity* is also not bound — a commit hash
names a revision, not which repository it came from.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tomtit.reporting import ReportSourceRevision

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")

#: Bounded: these are metadata reads, not the run's own work.
_GIT_TIMEOUT_SECONDS = 30

#: Git status letters, with rename detection disabled so only these appear.
_LETTER_TO_OPERATION = {
    "A": "CREATE",
    "M": "MODIFY",
    "T": "MODIFY",
    "U": "MODIFY",
    "D": "DELETE",
}


class WorkspaceBindingError(ValueError):
    """Base for every refusal to bind or trust a workspace as a source."""


class NotAGitRepositoryError(WorkspaceBindingError):
    """Raised when the path is not the root of a Git repository."""


class UnbornHeadError(WorkspaceBindingError):
    """Raised when HEAD names no commit, so there is no revision to bind."""


class DirtyWorkspaceError(WorkspaceBindingError):
    """Raised when the working tree has changes the contract cannot represent.

    Contract V1 §5.3: uncommitted source has no active canonical revision type,
    and the system must not invent a local canonicalization. Starting from a
    modified tree would record a base commit that was not what ran.
    """


class WorkspaceDriftError(WorkspaceBindingError):
    """Raised when the repository is not on the commit a run was bound to."""


class OutputInsideWorkspaceError(WorkspaceBindingError):
    """Raised when a Runtime-owned output directory is inside the source repo.

    Runtime writes evidence artifacts and the terminal report itself. If those
    land inside the bound repository they become source changes the run neither
    made nor observed: the final source state is resolved before publication, so
    the published ``source_revision`` would describe a workspace Runtime then
    dirtied.

    This lives here, beside the workspace it protects, so that both the
    composition factory and Runtime itself can enforce it. An authority property
    that only one convenience factory applies is not an invariant.
    """


class UnrelatedHistoryError(WorkspaceBindingError):
    """Raised when the final ``HEAD`` does not descend from the bound base.

    A rewritten, reset or unrelated history means the run's own base no longer
    explains the source that is present, so no delta computed against it is
    trustworthy. This is an execution-integrity failure, not a provenance
    nicety, and it fails closed.
    """


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one read-only git command in ``path``. No shell, fixed argv."""

    return subprocess.run(
        ["git", *args],
        cwd=str(path),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )


def _require_repository_root(path: Path) -> Path:
    if not path.is_dir():
        raise NotAGitRepositoryError(f"workspace {path} is not an existing directory")

    completed = _git(path, "rev-parse", "--show-toplevel")
    if completed.returncode != 0:
        raise NotAGitRepositoryError(
            f"workspace {path} is not inside a Git repository: "
            f"{completed.stderr.strip() or 'git rev-parse failed'}"
        )
    root = Path(completed.stdout.strip()).resolve()
    if root != path.resolve():
        raise NotAGitRepositoryError(
            f"workspace {path} is not a repository root; its root is {root}. "
            "A run binds the repository it executes in, so a subdirectory is "
            "refused rather than silently widened."
        )
    return root


def _resolve_head(path: Path) -> str:
    completed = _git(path, "rev-parse", "HEAD")
    if completed.returncode != 0:
        raise UnbornHeadError(
            f"workspace {path} has no HEAD commit to bind: "
            f"{completed.stderr.strip() or 'git rev-parse HEAD failed'}. "
            "An unborn branch has no source revision, so a run cannot claim one."
        )
    head = completed.stdout.strip()
    if not _COMMIT_PATTERN.match(head):
        raise UnbornHeadError(
            f"workspace {path} resolved HEAD to {head!r}, which is not a full "
            "40-character lowercase commit hash"
        )
    return head


def _parse_name_status(raw: str) -> tuple[tuple[str, str], ...]:
    """Parse ``git diff --name-status -z --no-renames`` output.

    NUL-separated, so paths containing spaces, quotes or newlines survive
    intact. With rename detection disabled every record is exactly a status
    followed by one path, which removes the two-path special case that
    previously let a rename's source path go unreported.
    """

    fields = [field for field in raw.split("\0") if field != ""]
    entries: list[tuple[str, str]] = []
    for index in range(0, len(fields) - 1, 2):
        code = fields[index]
        letter = code[0] if code else "M"
        entries.append((fields[index + 1], _LETTER_TO_OPERATION.get(letter, "MODIFY")))
    return tuple(entries)


@dataclass(frozen=True)
class WorkspaceDelta:
    """Every source path changed since the execution base, and how.

    The four buckets are kept apart because each answers a question the others
    cannot: a committed change is invisible to ``git status``, an untracked file
    is invisible to ``git diff``. :attr:`all_changed_paths` is their union, and
    that union — not any single bucket — is what scope validation must see.
    """

    base_commit: str
    current_head: str
    committed: tuple[tuple[str, str], ...]
    staged: tuple[tuple[str, str], ...]
    unstaged: tuple[tuple[str, str], ...]
    untracked_paths: tuple[str, ...]
    base_is_ancestor: bool
    #: Changed paths that existed in the execution base tree, and those present
    #: in the final source state. Together these decide the effective operation
    #: for each path; the per-layer buckets above are diagnostics only.
    present_in_base: frozenset[str] = frozenset()
    present_in_final: frozenset[str] = frozenset()

    @property
    def committed_paths(self) -> tuple[str, ...]:
        return tuple(path for path, _ in self.committed)

    @property
    def staged_paths(self) -> tuple[str, ...]:
        return tuple(path for path, _ in self.staged)

    @property
    def unstaged_paths(self) -> tuple[str, ...]:
        return tuple(path for path, _ in self.unstaged)

    @property
    def all_changed_paths(self) -> tuple[str, ...]:
        """Deduplicated union of every changed path, in stable sorted order."""

        paths = set(self.committed_paths) | set(self.staged_paths)
        paths |= set(self.unstaged_paths) | set(self.untracked_paths)
        return tuple(sorted(paths))

    @property
    def operations(self) -> tuple[tuple[str, str], ...]:
        """``(path, operation)`` for every changed path, base -> final.

        The operation is decided by what actually changed between the execution
        base and the final source state, never by which observation layer was
        written last. Letting a later layer overwrite an earlier one is how a
        path that existed in the base, was deleted in a commit, and then
        reappeared untracked ends up classified ``CREATE`` — which is a *weaker*
        authority claim than the ``MODIFY`` it really is, and would pass a
        contract granting only CREATE.

        The classification is therefore positional::

            absent in base, present in final    -> CREATE
            present in base, absent in final    -> DELETE
            present in base, present in final   -> MODIFY

        A path in neither is a transient that nets to nothing; it is reported as
        ``DELETE``, the most restrictive reading, rather than guessed at.
        """

        changed = set(self.committed_paths) | set(self.staged_paths)
        changed |= set(self.unstaged_paths) | set(self.untracked_paths)

        classified: dict[str, str] = {}
        for path in changed:
            in_base = path in self.present_in_base
            in_final = path in self.present_in_final
            if in_base and in_final:
                classified[path] = "MODIFY"
            elif in_base:
                classified[path] = "DELETE"
            elif in_final:
                classified[path] = "CREATE"
            else:
                classified[path] = "DELETE"
        return tuple(sorted(classified.items()))

    @property
    def final_clean(self) -> bool:
        """Whether the working tree has nothing pending.

        Committed changes do not make a tree unclean: after a commit ``HEAD``
        describes the output exactly, which is precisely when §4.1 allows a
        revision to be published.
        """

        return not (self.staged or self.unstaged or self.untracked_paths)

    @property
    def has_uncommitted_changes(self) -> bool:
        return not self.final_clean


@dataclass(frozen=True)
class BoundWorkspace:
    """A repository root pinned to the exact commit a run executes from.

    Immutable, and it cannot claim a commit the repository is not on:
    construction re-reads ``HEAD`` and refuses if it differs, so this type
    cannot assert provenance a real repository does not support. Being
    process-local, it cannot defend against a writer that moves the repository
    between the check and the use.

    Cleanliness is a *binding-time* precondition enforced by
    :func:`bind_workspace`, deliberately not re-asserted here: after a worker
    runs the tree is expected to be dirty, and this object still correctly
    describes the base execution started from.
    """

    path: Path
    head_commit: str

    def __post_init__(self) -> None:
        if not _COMMIT_PATTERN.match(self.head_commit):
            raise WorkspaceBindingError(
                f"{self.head_commit!r} is not a full 40-character lowercase "
                "commit hash"
            )
        root = _require_repository_root(self.path)
        object.__setattr__(self, "path", root)
        actual = _resolve_head(root)
        if actual != self.head_commit:
            raise WorkspaceDriftError(
                f"workspace {root} is at commit {actual}, not {self.head_commit}; "
                "a bound workspace must name the commit the repository is on"
            )

    @property
    def execution_base_revision(self) -> ReportSourceRevision:
        """The commit execution started from. Internal provenance, not §4.1.

        Deliberately *not* named ``source_revision``: the contract reserves that
        name for the exact source output a report represents, and this is the
        input.
        """

        return ReportSourceRevision(
            revision_type="GIT_COMMIT", revision_value=self.head_commit
        )

    def collect_delta(self) -> WorkspaceDelta:
        """Every source path changed since the bound base, however it changed."""

        return collect_workspace_delta(self)

    def resolve_output_revision(
        self, delta: WorkspaceDelta | None = None
    ) -> ReportSourceRevision | None:
        """The §4.1 revision of the exact current output, or ``None``.

        Returns a ``GIT_COMMIT`` only when a commit genuinely describes the
        output: the tree must be clean, so ``HEAD`` *is* the output, and the
        bound base must be an ancestor of it, so the history is the one this run
        started from. Uncommitted changes, or a ``HEAD`` on unrelated history,
        have no active Contract V1 representation and yield ``None``.

        A caller that already computed the delta may pass it in, so the
        completion path resolves the revision and validates history from one
        consistent observation rather than two.
        """

        observed = delta if delta is not None else self.collect_delta()
        if not observed.final_clean or not observed.base_is_ancestor:
            return None
        return ReportSourceRevision(
            revision_type="GIT_COMMIT", revision_value=observed.current_head
        )

    def assert_base_unchanged(self) -> None:
        """Re-prove the repository is still on the bound commit."""

        actual = _resolve_head(self.path)
        if actual != self.head_commit:
            raise WorkspaceDriftError(
                f"workspace {self.path} moved from the bound commit "
                f"{self.head_commit} to {actual}; the recorded base would no "
                "longer describe the source this run started from"
            )

    def assert_clean(self) -> None:
        """Re-prove the working tree still has no pending changes.

        Called immediately before a worker is invoked, to narrow the window
        between binding and execution. Never called afterwards: worker output is
        expected to make the tree dirty.
        """

        delta = self.collect_delta()
        if delta.has_uncommitted_changes:
            pending = delta.all_changed_paths
            raise DirtyWorkspaceError(
                f"workspace {self.path} has {len(pending)} pending change(s) and "
                "cannot be an authoritative execution source: "
                f"{', '.join(pending[:5])}{' ...' if len(pending) > 5 else ''}. "
                "Contract V1 §5.3 has no active revision type for uncommitted "
                "source, so this fails closed rather than claiming a base commit "
                "that was not what ran."
            )


def collect_workspace_delta(workspace: BoundWorkspace) -> WorkspaceDelta:
    """Collect every change in ``workspace`` relative to its bound base.

    Four separate queries, because no single one sees everything:

    * ``diff --name-status --no-renames base..HEAD`` — committed by the worker;
    * ``diff --name-status --no-renames --cached`` — staged, uncommitted;
    * ``diff --name-status --no-renames`` — modified, unstaged;
    * ``ls-files --others --exclude-standard`` — untracked new files.

    ``--no-renames`` decomposes a move into a deletion plus an addition, so the
    path a rename moved *away from* is still reported; with rename detection on,
    that path vanishes and an out-of-scope source can be laundered into scope.

    Every call is an exact argv with no shell, and ``-z`` keeps awkward paths
    intact.
    """

    root = workspace.path
    head = _resolve_head(root)

    committed: tuple[tuple[str, str], ...] = ()
    if head != workspace.head_commit:
        completed = _git(
            root,
            "diff",
            "--name-status",
            "-z",
            "--no-renames",
            f"{workspace.head_commit}..{head}",
        )
        if completed.returncode != 0:
            raise WorkspaceBindingError(
                f"could not diff {workspace.head_commit}..{head} in {root}: "
                f"{completed.stderr.strip() or 'git diff failed'}"
            )
        committed = _parse_name_status(completed.stdout)

    staged = _git(root, "diff", "--name-status", "-z", "--no-renames", "--cached")
    unstaged = _git(root, "diff", "--name-status", "-z", "--no-renames")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    for label, completed in (
        ("staged", staged),
        ("unstaged", unstaged),
        ("untracked", untracked),
    ):
        if completed.returncode != 0:
            raise WorkspaceBindingError(
                f"could not read {label} changes in {root}: "
                f"{completed.stderr.strip() or 'git failed'}"
            )

    ancestry = _git(root, "merge-base", "--is-ancestor", workspace.head_commit, head)

    committed_parsed = committed
    staged_parsed = _parse_name_status(staged.stdout)
    unstaged_parsed = _parse_name_status(unstaged.stdout)
    untracked_parsed = tuple(
        path for path in untracked.stdout.split("\0") if path != ""
    )

    changed = {path for path, _ in committed_parsed}
    changed |= {path for path, _ in staged_parsed}
    changed |= {path for path, _ in unstaged_parsed}
    changed |= set(untracked_parsed)

    return WorkspaceDelta(
        base_commit=workspace.head_commit,
        current_head=head,
        committed=committed_parsed,
        staged=staged_parsed,
        unstaged=unstaged_parsed,
        untracked_paths=untracked_parsed,
        base_is_ancestor=ancestry.returncode == 0,
        present_in_base=_paths_in_commit(root, workspace.head_commit, changed),
        # os.path.lexists, not Path.exists: the question is whether the working
        # tree still has an entry at this path, and a dangling symlink is a real
        # entry that Git tracks. Path.exists() answers "does the target
        # resolve", which reports a dangling symlink as absent and would
        # classify creating one as DELETE and replacing a file with one as
        # DELETE -- both weaker than the truth.
        present_in_final=frozenset(
            path for path in changed if os.path.lexists(root / path)
        ),
    )


def _paths_in_commit(
    root: Path, commit: str, candidates: set[str]
) -> frozenset[str]:
    """Which of ``candidates`` existed in ``commit``'s tree.

    One ``ls-tree`` over the base rather than a ``cat-file`` per path: it is a
    single bounded call and, being NUL-separated, survives awkward filenames.
    """

    if not candidates:
        return frozenset()
    completed = _git(root, "ls-tree", "-r", "-z", "--name-only", commit)
    if completed.returncode != 0:
        raise WorkspaceBindingError(
            f"could not list the tree of {commit} in {root}: "
            f"{completed.stderr.strip() or 'git ls-tree failed'}"
        )
    tracked = {path for path in completed.stdout.split("\0") if path != ""}
    return frozenset(candidates & tracked)


def assert_output_outside_workspace(
    directory: Path | str, workspace_root: Path, label: str
) -> Path:
    """Prove a Runtime-owned output directory is not inside the source repo.

    Both sides are resolved first, so a symlink pointing into the repository is
    caught rather than sidestepping a string comparison. The directory need not
    exist yet; ``resolve`` still follows symlinks through the ancestors that do.
    """

    resolved = Path(directory).resolve()
    root = Path(workspace_root).resolve()
    if resolved == root or root in resolved.parents:
        raise OutputInsideWorkspaceError(
            f"{label} {resolved} is inside the bound source repository {root}. "
            "Runtime-owned output would become an unobserved source change, so "
            "the published source revision could not describe the final source. "
            "Choose a directory outside the repository."
        )
    return resolved


def bind_workspace(path: Path | str) -> BoundWorkspace:
    """Validate a Git workspace and pin the run to its exact ``HEAD``.

    This is the execution-base authority for a Product Runtime run, and it runs
    *before* any worker executes. It refuses a path that is not an existing
    repository root, a repository whose ``HEAD`` names no commit, and a working
    tree with staged, unstaged or untracked changes (§5.3).

    The returned binding is immutable. Nothing downstream — worker, check
    runner, report builder, Evaluation or an assessment caller — may substitute
    a different revision.
    """

    root = _require_repository_root(Path(path))
    head = _resolve_head(root)
    bound = BoundWorkspace(path=root, head_commit=head)
    bound.assert_clean()
    return bound
