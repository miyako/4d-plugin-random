"""Execute literal workflow run blocks with closed-PATH native tool stubs."""

import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text()
SPEC = importlib.util.spec_from_file_location("native_source", ROOT / ".github/scripts/source_version.py")
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


def step(name):
    matches = re.findall(
        rf"^      - name: {re.escape(name)}\n"
        r"(?:(?!      - ).*\n)*?        run: \|\n"
        r"((?:          .*(?:\n|$)|[ \t]*\n)+)", WORKFLOW, re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError(f"Expected one literal workflow shell block: {name}")
    return textwrap.dedent(matches[0])


STUB = r'''
import json
import os
from pathlib import Path
import plistlib
import re
import sys

tool, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps([tool, *args]) + "\n")
contents = Path(os.environ["BUNDLE_PATH"]) / "Contents"
mode = os.environ.get("BUILD_MODE", "complete")
version, stale = os.environ["SOURCE_VERSION"], os.environ["STALE_VERSION"]
if tool == "xcodebuild":
    for path in (contents / "MacOS/random", contents / "Info.plist",
                 contents / "Resources/English.lproj/InfoPlist.strings",
                 contents / "_CodeSignature", contents.parent.parent / "Default",
                 contents.parent.parent / "random.build"):
        assert not path.exists(), "stale output survived: " + str(path)
    if mode == "fail":
        sys.exit(70)
    if mode != "missing-binary":
        (contents / "MacOS").mkdir(parents=True, exist_ok=True)
        (contents / "MacOS/random").write_bytes(b"rebuilt-universal")
    if mode != "missing-plist":
        data = plistlib.loads(Path("Info.plist").read_bytes())
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            if key in data:
                data[key] = re.sub(r"\$\([^)]+\)|\$\{[^}]+\}", version, data[key])
        if mode == "stale-plist":
            data["CFBundleShortVersionString"] = stale
        if mode == "stale-build":
            data["CFBundleVersion"] = stale
        (contents / "Info.plist").write_bytes(plistlib.dumps(data))
    if mode != "missing-localized":
        target = contents / "Resources/English.lproj/InfoPlist.strings"
        target.parent.mkdir(parents=True, exist_ok=True)
        data = Path("English.lproj/InfoPlist.strings").read_bytes()
        if mode == "stale-localized":
            data = data.replace(version.encode(), stale.encode())
        target.write_bytes(data)
elif tool == "lipo":
    assert Path(args[-1]).is_file()
    archs = os.environ.get("ARCHS", "arm64 x86_64")
    if args[0] == "-info":
        print("Architectures in the fat file: " + archs)
    else:
        assert args == ["-verify_arch", "arm64", "x86_64",
                        os.environ["BUNDLE_PATH"] + "/Contents/MacOS/random"]
        if not {"arm64", "x86_64"}.issubset(archs.split()):
            sys.exit(75)
elif tool == "python3":
    assert args == [".github/scripts/source_version.py", "--verify-bundle",
                    os.environ["BUNDLE_PATH"]]
    os.execv(os.environ["REAL_PYTHON"], [os.environ["REAL_PYTHON"], *args])
elif tool == "security":
    assert args == ["find-identity", "-v", "-p", "codesigning", os.environ["KEYCHAIN_PATH"]]
    if os.environ.get("SIGN_MODE") != "missing-identity":
        print('1) FAKE "Developer ID Application: Fixture (TEST)"')
elif tool == "codesign":
    verify = args[0] == "--verify"
    expected = (["--verify", "--deep", "--strict", "--verbose=2"] if verify else
                ["--verbose", "--deep", "--timestamp", "--force", "--options=runtime",
                 "--sign", "Developer ID Application: Fixture (TEST)"])
    assert args == expected + [os.environ["BUNDLE_PATH"]]
    if os.environ.get("SIGN_MODE") == ("verify" if verify else "sign"):
        sys.exit(76)
elif tool in ("ditto", "hdiutil"):
    assert contents.is_dir()
    expected = (["-c", "-k", "--keepParent", os.environ["BUNDLE_PATH"]] if tool == "ditto"
                else ["create", "-format", "UDBZ", "-volname", "random",
                      "-srcfolder", os.environ["BUNDLE_PATH"]])
    assert args[:-1] == expected
    Path(args[-1]).write_bytes(b"stub archive")
elif tool == "xcrun":
    scenario = os.environ.get("NOTARY_MODE", "accepted")
    if args[:2] == ["notarytool", "submit"]:
        assert args[3:] == ["--keychain-profile", "notarytool-profile", "--keychain",
                            os.environ["KEYCHAIN_PATH"], "--wait", "--output-format", "json"]
        assert Path(args[2]).is_file()
        second = args[2].endswith(".dmg")
        if scenario in ("transport", "accepted-nonzero") or (scenario == "second-transport" and second):
            if scenario == "accepted-nonzero":
                print(json.dumps({"id": "misleading", "status": "Accepted"}))
            sys.exit(71)
        if scenario == "malformed":
            print("this is not JSON")
        else:
            status = ("In Progress" if scenario == "pending" else
                      "Invalid" if scenario == "invalid" or (scenario == "second-invalid" and second)
                      else "Accepted")
            payload = {"id": "submission-dmg" if second else "submission-zip", "status": status}
            if scenario == "missing-id":
                del payload["id"]
            if scenario == "missing-status":
                del payload["status"]
            if scenario == "null-status":
                payload["status"] = None
            print(json.dumps([] if scenario == "array" else payload))
    elif args[:2] == ["notarytool", "log"]:
        assert args[3:] == ["--keychain-profile", "notarytool-profile", "--keychain",
                            os.environ["KEYCHAIN_PATH"]]
        sys.exit(72)
    elif args[:2] == ["stapler", "staple"]:
        assert len(args) == 3 and args[2].endswith(".dmg")
        if scenario == "staple":
            sys.exit(73)
    else:
        raise AssertionError(args)
elif tool == "plutil":
    assert args[0] == "-extract" and args[2:] == ["raw", "-o", "-", "-"]
    try:
        value = json.load(sys.stdin)[args[1]]
        assert isinstance(value, str)
        print(value)
        if os.environ.get("PLUTIL_FAIL") == args[1]:
            sys.exit(74)
    except (ValueError, KeyError, AssertionError):
        sys.exit(74)
else:
    raise AssertionError("Unexpected native command: " + tool)
'''


class NativeShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".native-tests-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in (*source.SOURCE_PATHS, Path(".github/scripts/source_version.py")):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        self.version = source.read(self.root)
        parts = list(map(int, self.version.split(".")))
        self.stale = f"{parts[0]}.{parts[1]}.{parts[2] + 1}"
        self.relative = "test/Plugins/random.bundle"
        self.bundle = self.root / self.relative
        self.contents = self.bundle / "Contents"
        self.log, self.env_file = self.root / "calls.jsonl", self.root / "github-env"
        self.env_file.touch()
        self.bin = self.root / "stub-bin"
        self.bin.mkdir()
        for name in ("xcodebuild", "lipo", "python3", "security", "codesign", "ditto",
                     "hdiutil", "xcrun", "plutil"):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(STUB))
            path.chmod(0o755)
        for name in ("rm", "ls", "grep", "head", "sed", "sort", "mkdir", "cp"):
            (self.bin / name).symlink_to(shutil.which(name))
        self.env = {
            "PATH": str(self.bin), "CALL_LOG": str(self.log), "REAL_PYTHON": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1", "SOURCE_VERSION": self.version,
            "STALE_VERSION": self.stale, "BUNDLE_PATH": self.relative,
            "KEYCHAIN_PATH": str(self.root / "fake.keychain-db"), "GITHUB_ENV": str(self.env_file),
        }
        self.preserved = {}
        for name in ("manifest.json", "Resources/constants.xlf", "QuickLook/Thumbnail.png",
                     "Windows/random.4DX", "Windows64/random.4DX"):
            value = (ROOT / self.relative / "Contents" / name).read_bytes()
            self.write(name, value)
            self.preserved[name] = value
        for name in ("MacOS/Dependency.dylib", "Frameworks/Dependency.framework/Versions/A/Dependency",
                     "Frameworks/Dependency.framework/Versions/A/Resources/dependency.dat"):
            self.preserved[name] = b"committed runtime dependency"
            self.write(name, self.preserved[name])
        (self.contents / "Frameworks/Dependency.framework/Versions/Current").symlink_to("A")
        self.seed_stale()

    def seed_stale(self):
        for name in ("MacOS/random", "Info.plist", "Resources/English.lproj/InfoPlist.strings",
                     "_CodeSignature/CodeResources"):
            self.write(name, b"stale build output")
        for name in ("Default/stale", "random.build/stale"):
            path = self.bundle.parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"stale intermediate")

    def write(self, name, data):
        path = self.contents / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def run_step(self, name, **env):
        script = step(name)
        for key, value in {
            "env.PRODUCT_NAME": "random", "env.BASE_DIR": "test",
            "env.MACOS_DEPLOYMENT_TARGET": "11.0", "needs.version.outputs.version": self.version,
        }.items():
            script = script.replace("${{ " + key + " }}", value)
        self.assertNotIn("${{", script)
        return subprocess.run(["/bin/bash", "-c", script], cwd=self.root, env={**self.env, **env},
                              capture_output=True, text=True, timeout=30)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def calls(self, tool):
        return [row[1:] for line in self.log.read_text().splitlines()
                if (row := json.loads(line))[0] == tool] if self.log.exists() else []

    def build(self, **env):
        self.ok(self.run_step("build macOS plugin (universal)", **env))

    def test_fresh_build_preserves_resources_dependencies_and_exact_settings(self):
        self.build()
        self.assertEqual((self.contents / "MacOS/random").read_bytes(), b"rebuilt-universal")
        for name, value in self.preserved.items():
            self.assertEqual((self.contents / name).read_bytes(), value, name)
        link = self.contents / "Frameworks/Dependency.framework/Versions/Current"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.readlink(), Path("A"))
        for path in (self.contents / "_CodeSignature", self.bundle.parent / "Default",
                     self.bundle.parent / "random.build"):
            self.assertFalse(path.exists())
        self.ok(self.run_step("verify universal binary"))
        self.assertEqual(len(self.calls("python3")), 1)
        self.assertEqual(self.calls("xcodebuild"), [[
            "-project", "random.xcodeproj", "-target", "random", "-configuration", "Default",
            "ARCHS=arm64 x86_64", "ONLY_ACTIVE_ARCH=NO", "MACOSX_DEPLOYMENT_TARGET=11.0",
            "CODE_SIGNING_ALLOWED=NO", f"CONFIGURATION_BUILD_DIR={self.root}/test/Plugins", "build",
        ]])

    def test_failed_or_misdirected_build_cannot_reuse_stale_binary(self):
        result = self.run_step("build macOS plugin (universal)", BUILD_MODE="fail")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.contents / "MacOS/random").exists())
        self.seed_stale()
        self.build(BUILD_MODE="missing-binary")
        result = self.run_step("verify universal binary")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected binary not found", result.stdout)
        self.assertEqual(self.calls("lipo"), [])
        self.assertEqual(self.calls("python3"), [])

    def test_missing_and_stale_rebuilt_metadata_fail_closed(self):
        for mode in ("missing-plist", "missing-localized", "stale-plist", "stale-build",
                     "stale-localized"):
            with self.subTest(mode=mode):
                self.build(BUILD_MODE=mode)
                result = self.run_step("verify universal binary")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FileNotFoundError" if mode.startswith("missing") else
                              "differs from selected source", result.stderr)

    def test_strict_required_slices_without_inventing_i386_exclusion(self):
        self.build()
        for archs in ("arm64", "x86_64", "arm64e x86_64", "arm64 x86_64h"):
            with self.subTest(archs=archs):
                self.assertNotEqual(self.run_step("verify universal binary", ARCHS=archs).returncode, 0)
        self.assertEqual(self.calls("python3"), [])
        self.ok(self.run_step("verify universal binary", ARCHS="arm64 x86_64 i386"))
        self.assertIn(["-verify_arch", "arm64", "x86_64",
                       f"{self.relative}/Contents/MacOS/random"], self.calls("lipo"))

    def test_compiled_binary_xml_and_utf16_localization(self):
        self.build()
        original = source.text(self.root / source.LOCALIZED)
        fields = dict(re.findall(r'(?m)^(\w+)\s*=\s*"([^"]*)";', original))
        for encoding in ("utf-16", "utf-16-be", plistlib.FMT_XML, plistlib.FMT_BINARY):
            for stale in (False, True):
                with self.subTest(encoding=encoding, stale=stale):
                    text = original.replace(self.version, self.stale) if stale else original
                    if isinstance(encoding, str):
                        data = (b"\xfe\xff" if encoding == "utf-16-be" else b"") + text.encode(encoding)
                    else:
                        values = {key: value.replace(self.version, self.stale) if stale else value
                                  for key, value in fields.items()}
                        data = plistlib.dumps(values, fmt=encoding)
                    self.write("Resources/English.lproj/InfoPlist.strings", data)
                    result = self.run_step("verify universal binary")
                    if stale:
                        self.assertNotEqual(result.returncode, 0)
                    else:
                        self.ok(result)

    def test_merge_requires_exact_nonempty_artifact_not_recursive_match(self):
        artifact = self.root / "winbuild/random.4DX"
        artifact.parent.mkdir()
        nested = artifact.parent / "stale/random.4DX"
        nested.parent.mkdir()
        nested.write_bytes(b"wrong recursive match")
        for data in (None, b""):
            if data is not None:
                artifact.write_bytes(data)
            result = self.run_step("merge Windows binaries into the bundle")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exact artifact path", result.stdout)
            self.assertEqual(self.env_file.read_text(), "")
        artifact.write_bytes(b"fresh selected-source Windows binary")
        self.ok(self.run_step("merge Windows binaries into the bundle"))
        self.assertEqual((self.contents / "Windows64/random.4DX").read_bytes(), artifact.read_bytes())
        self.assertEqual(self.env_file.read_text(), f"BUNDLE_PATH={self.relative}\n")

    def test_identity_signing_and_strict_verification_are_mandatory(self):
        self.ok(self.run_step("codesign"))
        self.assertEqual(len(self.calls("codesign")), 2)
        for scenario, count in (("missing-identity", 0), ("sign", 1), ("verify", 2)):
            self.log.unlink()
            self.assertNotEqual(self.run_step("codesign", SIGN_MODE=scenario).returncode, 0)
            self.assertEqual(len(self.calls("codesign")), count)
            self.assertEqual(self.calls("ditto"), [])
            self.assertEqual(self.calls("xcrun"), [])

    def test_accepted_notarizations_then_staple_export_versioned_assets(self):
        self.ok(self.run_step("package, notarize & staple"))
        self.assertEqual(self.env_file.read_text().splitlines(),
                         [f"ZIP_PATH=random-{self.version}.zip", f"DMG_PATH=random-{self.version}.dmg"])
        calls = self.calls("xcrun")
        self.assertEqual([row[2] for row in calls if row[:2] == ["notarytool", "submit"]],
                         [f"random-{self.version}.zip", f"random-{self.version}.dmg"])
        self.assertEqual(calls[-1], ["stapler", "staple", f"random-{self.version}.dmg"])

    def test_each_notary_transport_status_json_and_staple_failure_blocks_export(self):
        for scenario, submissions, diagnostic, stapled in (
            ("invalid", 1, "submission-zip", False), ("transport", 1, None, False),
            ("accepted-nonzero", 1, None, False), ("malformed", 1, None, False),
            ("missing-id", 1, None, False), ("missing-status", 1, None, False),
            ("null-status", 1, None, False), ("array", 1, None, False),
            ("pending", 1, "submission-zip", False),
            ("second-transport", 2, None, False), ("second-invalid", 2, "submission-dmg", False),
            ("staple", 2, None, True),
        ):
            with self.subTest(scenario=scenario):
                self.log.unlink(missing_ok=True)
                result = self.run_step("package, notarize & staple", NOTARY_MODE=scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.env_file.read_text(), "")
                self.assertEqual(len(self.calls("ditto")), 1)
                self.assertEqual(len(self.calls("hdiutil")), 1)
                calls = self.calls("xcrun")
                self.assertEqual(len([row for row in calls if row[:2] == ["notarytool", "submit"]]),
                                 submissions)
                self.assertEqual([row[2] for row in calls if row[:2] == ["notarytool", "log"]],
                                 [diagnostic] if diagnostic else [])
                self.assertEqual(bool([row for row in calls if row[:2] == ["stapler", "staple"]]), stapled)
                if scenario == "accepted-nonzero":
                    self.assertEqual(self.calls("plutil"), [])

    def test_notary_command_status_stays_fatal_when_errexit_is_suppressed_by_caller(self):
        package = step("package, notarize & staple")
        function = "notarize() {" + package.split("notarize() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
        script = ("set -euo pipefail\n" + function +
                  '\nif notarize fixture.zip; then echo PUBLICATION_GATE_PASSED; else exit 1; fi\n')
        (self.root / "fixture.zip").write_bytes(b"fixture archive")
        for scenario, plutil, expected, count in (
            ("accepted", "", True, 2), ("accepted-nonzero", "", False, 0),
            ("accepted", "id", False, 1), ("accepted", "status", False, 2),
        ):
            with self.subTest(scenario=scenario, plutil=plutil):
                self.log.unlink(missing_ok=True)
                result = subprocess.run(
                    ["/bin/bash", "-c", script], cwd=self.root,
                    env={**self.env, "NOTARY_MODE": scenario, "PLUTIL_FAIL": plutil},
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode == 0, expected, result.stdout + result.stderr)
                self.assertEqual("PUBLICATION_GATE_PASSED" in result.stdout, expected)
                self.assertEqual(len(self.calls("plutil")), count)


class ConfigurationTests(unittest.TestCase):
    def test_windows_exact_output_matches_project_and_toolset_override(self):
        ns = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
        project = ET.parse(ROOT / "random.vcxproj")
        condition = "'$(Configuration)|$(Platform)'=='Release|x64'"
        outdirs = [node.text for node in project.findall(".//m:OutDir", ns)
                   if node.get("Condition") == condition]
        extensions = [node.text for node in project.findall(".//m:TargetExt", ns)
                      if node.get("Condition") == condition]
        self.assertEqual(extensions, [".4DX"])
        self.assertEqual(len(outdirs), 1)
        actual = (outdirs[0].replace("$(ProjectDir)", "").replace("$(ProjectName)", "random")
                  .replace("\\", "/") + "random.4DX")
        normalize = step("normalize windows output to canonical case")
        match = re.search(r'^\$source = "([^"]+)"$', normalize, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(actual, match[1].replace("$base", "test").replace("$product", "random"))
        for text in ("Test-Path -LiteralPath $source -PathType Leaf", "throw ",
                     "$sourceResolved -ieq $destResolved", "Copy-Item -LiteralPath $source $destination -Force"):
            self.assertIn(text, normalize)
        self.assertNotIn("-Recurse", normalize)
        clean = step("clean stale outputs")
        for text in ('"$base/plugins/$product.bundle/contents/windows64/$product.4DX"',
                     '"$base/Plugins/Default"', '"Objs"', "if (Test-Path -LiteralPath $path)",
                     "Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop"):
            self.assertIn(text, clean)
        self.assertNotIn("SilentlyContinue", clean)
        self.assertIn("/p:Configuration=Release /p:Platform=x64", step("build x64"))
        self.assertIn("/p:PlatformToolset=v143 /p:WindowsTargetPlatformVersion=10.0", step("build x64"))
        self.assertEqual({node.text for node in project.findall(".//m:PlatformToolset", ns)}, {"v140"})

    def test_original_project_settings_resources_and_canonical_manifest(self):
        project = (ROOT / source.PROJECT).read_text()
        for key, value, count in (
            ("SDKROOT", "macosx", 6), ("MACOSX_DEPLOYMENT_TARGET", "10.11", 3),
            ("CLANG_CXX_LIBRARY", '"libc++"', 6), ("INFOPLIST_FILE", "Info.plist", 3),
            ("WRAPPER_EXTENSION", "bundle", 3),
        ):
            self.assertEqual(len(re.findall(rf"(?m)^\s*{key} = {re.escape(value)};", project)), count)
        for name in ("Default", "Development", "Deployment"):
            self.assertEqual(len(re.findall(rf"(?m)^\s*name = {name};", project)), 2)
        for resource in ("manifest.json in CopyFiles", "constants.xlf in CopyFiles",
                         "Cocoa.framework in Frameworks", "InfoPlist.strings in Resources"):
            self.assertIn(resource, project)
        self.assertIn("dstPath = ..;", project)
        copy_phase = project.split("/* Begin PBXCopyFilesBuildPhase section */", 1)[1].split(
            "/* End PBXCopyFilesBuildPhase section */", 1)[0]
        self.assertIn("dstSubfolderSpec = 7;", copy_phase)
        self.assertIn("dstPath = ..;", copy_phase)
        self.assertIn("manifest.json in CopyFiles", copy_phase)
        self.assertIn("constants.xlf in CopyFiles", copy_phase)
        bundle = ROOT / "test/Plugins/random.bundle/Contents"
        self.assertTrue((bundle / "manifest.json").is_file())
        self.assertFalse((bundle / "Resources/manifest.json").exists())
        self.assertTrue((bundle / "Resources/constants.xlf").is_file())
        self.assertNotIn("i386", step("verify universal binary"))


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell required for Windows shell harness")
class WindowsShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".windows-tests-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "test/plugins/random.bundle/contents/windows64/random.4DX"
        self.binary.parent.mkdir(parents=True)
        self.bin = self.root / "closed-bin"
        self.bin.mkdir()

    def run_step(self, name, prefix=""):
        script = step(name)
        for key, value in (("PRODUCT_NAME", "random"), ("BASE_DIR", "test")):
            script = script.replace("${{ env." + key + " }}", value)
        self.assertNotIn("${{", script)
        return subprocess.run(
            [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-Command", prefix + "\n" + script], cwd=self.root,
            env={**os.environ, "PATH": str(self.bin)}, capture_output=True, text=True, timeout=30)

    def test_cleanup_invalidates_only_binary_and_intermediates_preserving_resources(self):
        self.binary.write_bytes(b"stale Windows binary")
        resource = self.binary.parent.parent / "QuickLook/Thumbnail.png"
        resource.parent.mkdir()
        resource.write_bytes(b"resource")
        for name in ("test/Plugins/Default/stale", "Objs/stale"):
            target = self.root / name
            target.parent.mkdir(parents=True)
            target.write_bytes(b"stale intermediate")
        for _ in range(2):
            result = self.run_step("clean stale outputs")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.binary.exists())
        self.assertFalse((self.root / "test/Plugins/Default").exists())
        self.assertFalse((self.root / "Objs").exists())
        self.assertEqual(resource.read_bytes(), b"resource")

    def test_exact_output_not_stale_recursive_match_then_no_self_copy(self):
        wrong = self.root / "stale/Plugins/random.4DX"
        wrong.parent.mkdir(parents=True)
        wrong.write_bytes(b"wrong recursive match")
        result = self.run_step("normalize windows output to canonical case")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact MSBuild output", result.stderr)
        self.binary.write_bytes(b"fresh Windows binary")
        result = self.run_step("normalize windows output to canonical case")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        canonical = self.root / "test/Plugins/random.bundle/Contents/Windows64/random.4DX"
        self.assertEqual(canonical.read_bytes(), b"fresh Windows binary")
        if canonical.samefile(self.binary):
            self.assertIn("skipping copy", result.stdout)

    def test_cleanup_and_directory_creation_errors_are_fatal(self):
        self.binary.write_bytes(b"stale Windows binary")
        for name, prefix, message in (
            ("clean stale outputs", "function Remove-Item { Write-Error 'fixture cleanup failure' }",
             "fixture cleanup failure"),
            ("normalize windows output to canonical case",
             "function New-Item { Write-Error 'fixture directory failure' }", "fixture directory failure"),
        ):
            result = self.run_step(name, prefix)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)
            self.assertEqual(self.binary.read_bytes(), b"stale Windows binary")


if __name__ == "__main__":
    unittest.main()
