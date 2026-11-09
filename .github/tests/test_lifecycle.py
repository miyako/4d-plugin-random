"""Real Git lifecycle tests use only disposable local repositories."""

import contextlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / ".github/scripts"))
SPEC = importlib.util.spec_from_file_location("lifecycle_release", ROOT / ".github/scripts/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
import source_version as source


def git(path, *args, input=None, env=None):
    location = ["--git-dir", str(path)] if path.name.endswith(".git") else ["-C", str(path)]
    return subprocess.run(["git", *location, *args], input=input, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


class GitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".lifecycle-tests-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote, self.local, self.peer = (self.root / name for name in ("remote.git", "local", "peer"))
        git(self.root, "init", "--bare", str(self.remote))
        git(self.root, "init", "-b", "selected", str(self.local))
        for key, value in (("user.name", "Lifecycle Test"), ("user.email", "test@example.invalid"),
                           ("push.followTags", "true"), ("commit.gpgsign", "false"),
                           ("tag.gpgsign", "false")):
            git(self.local, "config", key, value)
        git(self.local, "commit", "--allow-empty", "-m", "ancestor")
        self.ancestor = git(self.local, "rev-parse", "HEAD")
        for name in source.SOURCE_PATHS:
            target = self.local / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        shutil.copytree(ROOT / ".github/scripts", self.local / ".github/scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        git(self.local, "add", ".")
        git(self.local, "commit", "-m", "fixture")
        git(self.local, "remote", "add", "origin", str(self.remote))
        git(self.local, "push", "origin", "HEAD:refs/heads/selected")
        self.sha = git(self.local, "rev-parse", "HEAD")
        self.version = source.read(self.local)
        self.tag = f"v{self.version}"
        self.published, self.uploaded = None, []
        git(self.root, "clone", "--branch", "selected", str(self.remote), str(self.peer))
        for key, value in (("user.name", "Peer"), ("user.email", "peer@example.invalid"),
                           ("commit.gpgsign", "false")):
            git(self.peer, "config", key, value)
        previous = Path.cwd()
        os.chdir(self.local)
        self.addCleanup(os.chdir, previous)
        # A failed mock must never fall back to a real GitHub request.
        patcher = patch.object(release, "api", side_effect=AssertionError("Unmocked GitHub API"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def advance_peer(self):
        (self.peer / "peer.txt").write_text("concurrent branch update")
        git(self.peer, "add", ".")
        git(self.peer, "commit", "-m", "peer")
        git(self.peer, "push", "origin", "HEAD:refs/heads/selected")

    def assert_remote(self, sha):
        self.assertEqual(release.remote_ref("refs/heads/selected"), sha)
        self.assertEqual(git(self.remote, "tag", "--list"), "")

    def test_explicit_branch_metadata_only_direct_parent_trailer_no_follow_tags(self):
        git(self.local, "tag", "-a", "unrelated-annotated", "-m", "must not follow")
        git(self.remote, "update-ref", "refs/heads/other", self.sha)
        release.bump_branch(self.sha, "selected", "patch")
        new = git(self.local, "rev-parse", "HEAD")
        self.assert_remote(new)
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/other"), self.sha)
        self.assertEqual(git(self.local, "rev-list", "--parents", "-n", "1", new), f"{new} {self.sha}")
        self.assertEqual(git(self.local, "status", "--porcelain"), "")
        changed = set(git(self.local, "diff-tree", "--no-commit-id", "--name-only", "-r", new).splitlines())
        self.assertTrue(changed)
        self.assertTrue(changed.issubset(set(map(str, source.SOURCE_PATHS))))
        self.assertIn("Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
                      git(self.local, "log", "-1", "--format=%B"))

    def test_cli_bytecode_keeps_checkout_clean(self):
        env = os.environ.copy()
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        subprocess.run([sys.executable, ".github/scripts/release.py", "bump", "--sha", self.sha,
                        "--branch", "selected", "--mode", "patch"], env=env, check=True,
                       capture_output=True, text=True)
        self.assertEqual(git(self.local, "status", "--porcelain"), "")
        self.assert_remote(git(self.local, "rev-parse", "HEAD"))

    def test_preparation_race_stops_before_bump(self):
        self.advance_peer()
        with self.assertRaisesRegex(ValueError, "moved"):
            release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)

    def test_race_during_preparation_keeps_local_bump_unpublished(self):
        real_bump = release.bump

        def concurrent(mode):
            result = real_bump(mode)
            self.advance_peer()
            return result

        with patch.object(release, "bump", side_effect=concurrent):
            with self.assertRaisesRegex(ValueError, "moved during"):
                release.bump_branch(self.sha, "selected", "minor")
        self.assert_remote(git(self.peer, "rev-parse", "HEAD"))

    def advertised_race(self, kind):
        real_run = release.run
        expected, pushes = None, 0

        def concurrent(*args, **kwargs):
            nonlocal expected, pushes
            if "push" in args:
                pushes += 1
                if kind == "rewind":
                    expected = self.ancestor
                    git(self.remote, "update-ref", "refs/heads/selected", expected)
                elif kind == "delete":
                    git(self.remote, "update-ref", "-d", "refs/heads/selected")
                elif kind == "advance":
                    self.advance_peer()
                    expected = git(self.peer, "rev-parse", "HEAD")
                else:
                    expected = git(self.local, "rev-parse", "HEAD")
                    git(self.remote, "fetch", "--no-tags", str(self.local), "HEAD")
                    git(self.remote, "update-ref", "refs/heads/selected", expected)
            return real_run(*args, **kwargs)

        with patch.object(release, "run", side_effect=concurrent):
            with self.assertRaises(subprocess.CalledProcessError):
                release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(pushes, 1)
        self.assert_remote(expected)

    def test_advertised_rewind_after_final_read(self):
        self.advertised_race("rewind")

    def test_advertised_delete_after_final_read(self):
        self.advertised_race("delete")

    def test_advertised_advance_after_final_read(self):
        self.advertised_race("advance")

    def test_advertised_same_result_after_final_read(self):
        self.advertised_race("same-result")

    def test_required_guard_initial_missing_nonexecutable_and_dirty_checkout(self):
        guard = self.local / ".github/scripts/bump-hooks/pre-push"
        guard.chmod(0o644)
        for missing in (False, True):
            if missing:
                guard.unlink()
            with patch.object(release, "__file__", str(self.local / ".github/scripts/release.py")):
                with self.assertRaisesRegex(ValueError, "guard is missing or not executable"):
                    release.bump_branch(self.sha, "selected", "patch")
            self.assertEqual(git(self.local, "rev-parse", "HEAD"), self.sha)
        with self.assertRaisesRegex(ValueError, "clean checkout"):
            release.bump_branch(self.sha, "selected", "patch")

    def late_guard_loss(self, missing):
        runtime = self.root / "runtime"
        shutil.copytree(self.local / ".github/scripts", runtime)
        guard = runtime / "bump-hooks/pre-push"
        real_ref, reads = release.remote_ref, 0

        def lose(ref):
            nonlocal reads
            result = real_ref(ref)
            reads += 1
            if reads == 2:
                guard.unlink() if missing else guard.chmod(0o644)
            return result

        with patch.object(release, "__file__", str(runtime / "release.py")), \
                patch.object(release, "remote_ref", side_effect=lose), \
                patch.object(release.os, "access", wraps=os.access) as access:
            with self.assertRaisesRegex(ValueError, "guard is missing or not executable"):
                release.bump_branch(self.sha, "selected", "patch")
            self.assertEqual(access.call_count, 1 if missing else 2)
        self.assertEqual(reads, 2)
        self.assert_remote(self.sha)

    def test_guard_late_missing(self):
        self.late_guard_loss(True)

    def test_guard_late_nonexecutable(self):
        self.late_guard_loss(False)

    def test_postpush_exact_remote_verification_failure_retains_push(self):
        real_ref, reads = release.remote_ref, 0

        def incorrect(ref):
            nonlocal reads
            result = real_ref(ref)
            reads += 1
            return self.sha if reads == 3 else result

        with patch.object(release, "remote_ref", side_effect=incorrect):
            with self.assertRaisesRegex(ValueError, "Pushed branch failed exact-SHA"):
                release.bump_branch(self.sha, "selected", "patch")
        self.assertEqual(reads, 3)
        self.assert_remote(git(self.local, "rev-parse", "HEAD"))

    def test_bump_rejects_merge_unrelated_and_empty_commits_before_push(self):
        real_run = release.run
        for kind in ("merge", "unrelated", "empty"):
            with self.subTest(kind=kind):
                git(self.local, "reset", "--hard", self.sha)

                def corrupt(*args, **kwargs):
                    if args[:2] == ("git", "commit") and kind == "unrelated":
                        (self.local / "unrelated.txt").write_text("not metadata")
                        git(self.local, "add", "unrelated.txt")
                    result = real_run(*args, **kwargs)
                    if args[:2] == ("git", "commit") and kind != "unrelated":
                        tree = git(self.local, "rev-parse", f"{self.sha if kind == 'empty' else 'HEAD'}^{{tree}}")
                        parents = ["-p", self.sha] + (["-p", self.ancestor] if kind == "merge" else [])
                        new = git(self.local, "commit-tree", tree, *parents, input="corrupt fixture\n")
                        git(self.local, "update-ref", "HEAD", new)
                        git(self.local, "reset", "--hard", new)
                    return result

                message = "single-parent direct child" if kind == "merge" else "source version metadata only"
                with patch.object(release, "run", side_effect=corrupt) as run:
                    with self.assertRaisesRegex(ValueError, message):
                        release.bump_branch(self.sha, "selected", "patch")
                    self.assertFalse(any("push" in item.args for item in run.call_args_list))
                self.assert_remote(self.sha)

    def test_nonempty_metadata_subset_is_allowed(self):
        real_bump = release.bump

        def only_project(mode):
            new = real_bump(mode)
            git(self.local, "checkout", "--", str(source.PLIST), str(source.LOCALIZED))
            return new

        with patch.object(release, "bump", side_effect=only_project):
            release.bump_branch(self.sha, "selected", "patch")
        self.assert_remote(git(self.local, "rev-parse", "HEAD"))
        self.assertEqual(git(self.local, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"),
                         str(source.PROJECT))

    def test_commit_hook_removing_required_trailer_stops_before_push(self):
        real_run = release.run

        def strip_trailer(*args, **kwargs):
            result = real_run(*args, **kwargs)
            if args[:2] == ("git", "commit"):
                tree = git(self.local, "rev-parse", "HEAD^{tree}")
                new = git(self.local, "commit-tree", tree, "-p", self.sha,
                          input="bump rewritten without co-author\n")
                git(self.local, "update-ref", "HEAD", new)
            return result

        with patch.object(release, "run", side_effect=strip_trailer) as run:
            with self.assertRaisesRegex(ValueError, "co-author trailer"):
                release.bump_branch(self.sha, "selected", "patch")
            self.assertFalse(any("push" in item.args for item in run.call_args_list))
        self.assert_remote(self.sha)

    def test_real_commit_msg_hook_stripping_coauthor_stops_before_push(self):
        hook = self.local / ".git/hooks/commit-msg"
        hook.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\n"
            "import sys\n"
            "path = Path(sys.argv[1])\n"
            "path.write_text(''.join(line for line in path.read_text().splitlines(True)\n"
            "                        if not line.startswith('Co-authored-by:')))\n"
        )
        hook.chmod(0o755)
        with patch.object(release, "run", wraps=release.run) as run:
            with self.assertRaisesRegex(ValueError, "co-author trailer"):
                release.bump_branch(self.sha, "selected", "patch")
            self.assertFalse(any("push" in item.args for item in run.call_args_list))
        self.assertNotIn("Co-authored-by:", git(self.local, "log", "-1", "--format=%B"))
        self.assert_remote(self.sha)

    def test_guard_literal_local_ref_exact_head_sha_target_parent_and_one_row(self):
        guard = ROOT / ".github/scripts/bump-hooks/pre-push"
        git(self.local, "commit", "--allow-empty", "-m", "direct child")
        new = git(self.local, "rev-parse", "HEAD")
        env = {**os.environ, "BUMP_EXPECTED_SHA": self.sha,
               "BUMP_EXPECTED_REF": "refs/heads/selected", "BUMP_NEW_SHA": new}
        valid = f"{new} {new} refs/heads/selected {self.sha}\n"

        def invoke(row, environment=env):
            return subprocess.run([str(guard)], input=row, env=environment, text=True,
                                  capture_output=True)

        self.assertEqual(invoke(valid).returncode, 0)
        for row in (f"{new} {new} refs/heads/wrong {self.sha}\n",
                    f"HEAD {new} refs/heads/selected {self.sha}\n",
                    f"refs/heads/selected {new} refs/heads/selected {self.sha}\n",
                    f"{new} {self.ancestor} refs/heads/selected {self.sha}\n",
                    f"{new} {new} refs/heads/selected {self.ancestor}\n", valid * 2, ""):
            self.assertNotEqual(invoke(row).returncode, 0, row)
        self.assertNotEqual(invoke(valid, {}).returncode, 0)
        git(self.local, "commit", "--allow-empty", "-m", "HEAD drift")
        self.assertNotEqual(invoke(valid).returncode, 0)
        tree = git(self.local, "rev-parse", "HEAD^{tree}")
        merge = git(self.local, "commit-tree", tree, "-p", self.sha, "-p", self.ancestor,
                    input="merge fixture\n")
        git(self.local, "update-ref", "HEAD", merge)
        env["BUMP_NEW_SHA"] = merge
        row = f"{merge} {merge} refs/heads/selected {self.sha}\n"
        self.assertNotEqual(invoke(row).returncode, 0)
        result = subprocess.run(
            ["git", "-c", f"core.hooksPath={guard.parent}", "-c", "push.followTags=false",
             "push", "--porcelain", "--no-follow-tags", "origin", f"{merge}:refs/heads/selected"],
            env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assert_remote(self.sha)

    def test_immutable_sha_branch_clean_source_and_final_drift(self):
        for sha in ("selected", "a" * 40, "a" * 39, "a" * 41):
            with self.assertRaises(ValueError):
                release.selected_sha(sha)
        with self.assertRaises(subprocess.CalledProcessError):
            release.bump_branch(self.sha, "invalid..branch", "patch")
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", return_value=[]) as api:
            self.assertEqual(release.preflight(self.sha), (self.version, self.tag))
            api.assert_called_once_with("repos/owner/repo/releases?per_page=100&page=1")
        (self.local / source.PLIST).write_text("dirty source")
        with self.assertRaisesRegex(ValueError, "clean source"):
            release.preflight(self.sha)
        git(self.local, "checkout", "--", str(source.PLIST))
        source.bump("patch")
        with self.assertRaisesRegex(ValueError, "differs from the selected commit"):
            release.publish(self.sha, self.assets())
        self.assert_remote(self.sha)

    def assets(self):
        paths = [Path(f"random-{self.version}.{ext}") for ext in ("zip", "dmg")]
        for path in paths:
            path.write_bytes(b"fixture asset")
        return paths

    def publication_api(self, calls, fail=None, race=None):
        def api(endpoint, payload=None, asset=None):
            calls.append((endpoint, payload, asset))
            if "/releases?" in endpoint:
                return []
            if endpoint.endswith("/git/refs"):
                if race:
                    git(self.remote, "update-ref", f"refs/tags/{self.tag}", race)
                if fail == "tag":
                    raise subprocess.CalledProcessError(1, ["gh", "api"])
                git(self.remote, "update-ref", payload["ref"], payload["sha"], "0" * 40)
                return {"ref": payload["ref"], "object": {"sha": payload["sha"]}}
            if endpoint.endswith("/releases"):
                if fail == "release":
                    raise subprocess.CalledProcessError(1, ["gh", "api"])
                self.published = {"id": 123, **payload}
                return self.published
            if asset is not None:
                if fail == asset.suffix:
                    raise subprocess.CalledProcessError(1, ["gh", "api"])
                self.uploaded.append(asset.name)
                return {"id": 456, "name": asset.name, "size": asset.stat().st_size, "state": "uploaded"}
            raise AssertionError(endpoint)
        return api

    def test_real_git_tag_creation_and_original_release_payload(self):
        calls = []
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", side_effect=self.publication_api(calls)):
            release.publish(self.sha, self.assets())
        self.assertEqual(git(self.remote, "rev-parse", f"refs/tags/{self.tag}"), self.sha)
        self.assertEqual(calls[2][1], {
            "tag_name": self.tag, "target_commitish": self.sha, "name": self.tag,
            "draft": False, "prerelease": False, "body": "",
        })
        self.assertEqual(len(calls), 5)
        self.assertTrue(all("/releases/123/assets?name=" in item[0] for item in calls[3:]))

    def tag_race(self, different):
        if different:
            self.advance_peer()
        sha = git(self.peer, "rev-parse", "HEAD") if different else self.sha
        calls = []
        with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                patch.object(release, "api", side_effect=self.publication_api(calls, race=sha)):
            with self.assertRaises(subprocess.CalledProcessError):
                release.publish(self.sha, self.assets())
        self.assertEqual(len(calls), 2)
        self.assertEqual(git(self.remote, "rev-parse", f"refs/tags/{self.tag}"), sha)
        self.assertIsNone(self.published)

    def test_real_git_same_sha_tag_race(self):
        self.tag_race(False)

    def test_real_git_different_sha_tag_race(self):
        self.tag_race(True)

    def test_real_git_post_failures_preserve_all_created_state(self):
        for kind, count, published, uploaded in (
            ("tag", 2, False, []), ("release", 3, False, []),
            (".zip", 4, True, []), (".dmg", 5, True, [f"random-{self.version}.zip"]),
        ):
            git(self.remote, "update-ref", "-d", f"refs/tags/{self.tag}")
            self.published, self.uploaded, calls = None, [], []
            with patch.dict(os.environ, GITHUB_REPOSITORY="owner/repo"), \
                    patch.object(release, "api", side_effect=self.publication_api(calls, fail=kind)):
                with self.assertRaises(subprocess.CalledProcessError):
                    release.publish(self.sha, self.assets())
            self.assertEqual(len(calls), count)
            self.assertEqual(self.published is not None, published)
            self.assertEqual(self.uploaded, uploaded)
            if kind == "tag":
                self.assertEqual(git(self.remote, "tag", "--list"), "")
            else:
                self.assertEqual(git(self.remote, "rev-parse", f"refs/tags/{self.tag}"), self.sha)


if __name__ == "__main__":
    unittest.main()
