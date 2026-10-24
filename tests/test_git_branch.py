"""Sandbox plan stage 3: a git project's reviewed changes delivered as a commit on a branch of your repository (your
working tree, index and current branch untouched), and your edits brought into the copy before each task."""
from tests.test_sandbox_big import git, give, project  # noqa: F401
from tests.test_sandbox_big import home, no_docker_check, repo  # noqa: F401

from sinobee.sandbox import staging


def test_reviewed_changes_become_a_commit_on_a_branch(config, home, repo):
    git(repo, "config", "user.name", "Luphia")
    git(repo, "config", "user.email", "luphia@example.com")
    f = give(config, "c1", repo)
    copy = staging.ensure_copy(config, "c1", f)
    head, status, index = git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain"), \
        (repo / ".git" / "index").read_bytes()
    (copy / "src" / "app.ts").write_text("export const a = 2;\n")
    (copy / "src" / "new.ts").write_text("new\n")
    (copy / "notes.txt").unlink()
    res = staging.to_branch(config, "c1", f, paths=["src/app.ts", "src/new.ts"])
    assert res["branch"] == "sinobee/c1" and res["created"] and sorted(res["files"]) == ["src/app.ts", "src/new.ts"]
    # your folder: exactly as before
    assert git(repo, "rev-parse", "HEAD") == head and git(repo, "status", "--porcelain") == status
    assert (repo / ".git" / "index").read_bytes() == index and (repo / "src" / "app.ts").read_text() == "export const a = 1;\n"
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main\n"
    # the branch: your commit plus the reviewed changes, by you
    assert git(repo, "show", "sinobee/c1:src/app.ts") == "export const a = 2;\n"
    assert git(repo, "show", "sinobee/c1:src/new.ts") == "new\n"
    assert git(repo, "rev-parse", "sinobee/c1^") == head
    assert git(repo, "log", "-1", "--format=%an", "sinobee/c1") == "Luphia\n"
    # delivered: only the change not picked is still waiting
    assert [c["path"] for c in staging.changes(config, "c1", f)] == ["notes.txt"]
    # again: on top of the branch
    (copy / "src" / "app.ts").write_text("export const a = 3;\n")
    res2 = staging.to_branch(config, "c1", f, branch="sinobee/c1", message="第二次")
    assert not res2["created"] and res2["parent"] == res["commit"]
    assert git(repo, "show", "sinobee/c1:src/app.ts") == "export const a = 3;\n"
    assert "notes.txt" not in git(repo, "ls-tree", "-r", "--name-only", "sinobee/c1")
    assert git(repo, "status", "--porcelain") == status


def test_bad_or_current_branch_names_are_refused(config, home, repo):
    import pytest
    f = give(config, "c2", repo)
    copy = staging.ensure_copy(config, "c2", f)
    (copy / "README.md").write_text("changed\n")
    for name in ("main", "../x", "a..b", "x.lock", ""):
        with pytest.raises(staging.StagingError):
            staging.to_branch(config, "c2", f, branch=name or " ")
    assert staging.changes(config, "c2", f)                                  # nothing delivered


def test_your_edits_come_into_the_copy_before_a_task(config, home, repo):
    f = give(config, "c3", repo)
    copy = staging.ensure_copy(config, "c3", f)
    (copy / "src" / "app.ts").write_text("sandbox\n")                        # the sandbox's change
    (repo / "README.md").write_text("# 你剛改的\n")                           # yours, meanwhile
    (repo / "src" / "app.ts").write_text("yours too\n")                       # both changed this one
    (repo / "docs.md").write_text("new doc\n")
    (repo / "notes.txt").unlink()
    objects = sorted(p.name for p in (repo / ".git" / "objects").rglob("*") if p.is_file())
    assert staging.sync(config, "c3", f) == 3
    assert sorted(p.name for p in (repo / ".git" / "objects").rglob("*") if p.is_file()) == objects   # yours untouched
    assert (copy / "README.md").read_text() == "# 你剛改的\n" and (copy / "docs.md").read_text() == "new doc\n"
    assert not (copy / "notes.txt").exists() and (copy / "src" / "app.ts").read_text() == "sandbox\n"
    got = {c["path"]: c for c in staging.changes(config, "c3", f)}
    assert list(got) == ["src/app.ts"] and got["src/app.ts"]["conflict"]      # only the one changed on both sides
    assert staging.sync(config, "c3", f) == 0
