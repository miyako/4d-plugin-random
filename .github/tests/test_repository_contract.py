"""Random's committed policy and unrelated original bytes remain authoritative."""

import importlib.util
from pathlib import Path
import re
import stat
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASE = "17f7bf204ca2f87e9c8fbe1b6d4996f44ff6070e"
POLICY = "9aef13e"
COPIED_LOCALIZED = "test/Plugins/random.bundle/Contents/Resources/English.lproj/InfoPlist.strings"
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("contract_source", ROOT / ".github/scripts/source_version.py")
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


def original(path, revision=BASE):
    return subprocess.check_output(["git", "-C", str(ROOT), "show", f"{revision}:{path}"])


def block(workflow, name):
    return workflow.split(f"      - name: {name}\n", 1)[1].split("\n      - name:", 1)[0].rstrip()


class RepositoryContractTests(unittest.TestCase):
    def test_selected_localization_is_source_variant_input_not_prebuilt_destination(self):
        self.assertEqual(tuple(map(str, source.SOURCE_PATHS)), (
            "random.xcodeproj/project.pbxproj", "Info.plist", "English.lproj/InfoPlist.strings"))
        project = (ROOT / source.PROJECT).read_text()
        self.assertRegex(project, r"8D01CCCA0486CAD60068D4B7 /\* InfoPlist.strings in Resources \*/"
                         r" = \{isa = PBXBuildFile; fileRef = 089C167DFE841241C02AAC07")
        variants = project.split("/* Begin PBXVariantGroup section */", 1)[1].split(
            "/* End PBXVariantGroup section */", 1)[0]
        self.assertIn("089C167DFE841241C02AAC07 /* InfoPlist.strings */", variants)
        self.assertIn("089C167EFE841241C02AAC07 /* English */", variants)
        self.assertRegex(project, r"089C167EFE841241C02AAC07 /\* English \*/ = \{[^\n]*"
                         r"path = English.lproj/InfoPlist.strings;")
        resources = project.split("/* Begin PBXResourcesBuildPhase section */", 1)[1].split(
            "/* End PBXResourcesBuildPhase section */", 1)[0]
        self.assertIn("8D01CCCA0486CAD60068D4B7 /* InfoPlist.strings in Resources */", resources)
        self.assertNotEqual((ROOT / source.LOCALIZED).read_bytes(), (ROOT / COPIED_LOCALIZED).read_bytes())

    def test_explicit_coordinated_policy_overrides_original_fallback_body(self):
        workflow = original(".github/workflows/bump-version.yml", POLICY).decode()
        header, body = workflow.split("name: Bump version", 1)
        self.assertIn("MARKETING_VERSION/CURRENT_PROJECT_VERSION AND Info.plist", header)
        self.assertIn("CFBundleShortVersionString/CFBundleVersion", header)
        self.assertIn('KEY="MARKETING_VERSION"', body)
        self.assertIn('KEY="CURRENT_PROJECT_VERSION"', body)
        release = original(".github/workflows/release.yml").decode()
        self.assertIn("  push:\n    tags:\n      - 'v*.*.*'", release)
        self.assertNotIn("pull_request:", release)
        self.assertNotIn("branches:", release)

    def test_only_authorized_source_version_tokens_and_original_modes_changed(self):
        current = source.read(ROOT)
        for name in source.SOURCE_PATHS:
            with self.subTest(path=name):
                before = original(name)
                if name == source.PROJECT:
                    expected = before
                    for key, old in (("MARKETING_VERSION", "1.0.5"),
                                     ("CURRENT_PROJECT_VERSION", "1.0.3")):
                        self.assertEqual(before.count(f"{key} = {old};".encode()), 6)
                        expected = expected.replace(f"{key} = {old};".encode(),
                                                    f"{key} = {current};".encode())
                elif name == source.LOCALIZED:
                    self.assertNotIn(b"\n", before)
                    self.assertIn(b"\r", before)
                    expected = before.replace(b'CFBundleShortVersionString = "1.0.1";',
                                               f'CFBundleShortVersionString = "{current}";'.encode())
                    expected = expected.replace(b"random version 1.0.1, Copyright 2020 miyako.",
                                                 f"random version {current}, Copyright 2020 miyako.".encode())
                else:
                    expected = before.replace(b"<string>1.0.5</string>",
                                               f"<string>{current}</string>".encode())
                self.assertEqual((ROOT / name).read_bytes(), expected)
                self.assertEqual(stat.S_IMODE((ROOT / name).stat().st_mode), 0o755)
        self.assertNotIn(b"<key>CFBundleVersion</key>", (ROOT / source.PLIST).read_bytes())

    def test_unrelated_original_files_and_entire_prebuilt_subtree_preserved(self):
        paths = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", BASE], text=True).splitlines()
        allowed = {*map(str, source.SOURCE_PATHS), "README.md",
                   ".github/workflows/bump-version.yml", ".github/workflows/release.yml"}
        for name in paths:
            if name not in allowed:
                with self.subTest(path=name):
                    self.assertEqual((ROOT / name).read_bytes(), original(name))
        before = original(COPIED_LOCALIZED)
        self.assertTrue(before.startswith(b"\xff\xfe"))
        decoded = before.decode("utf-16")
        self.assertNotIn("\n", decoded)
        self.assertIn("\r", decoded)
        self.assertIn('CFBundleShortVersionString = "1.0.1";', decoded)
        self.assertEqual((ROOT / COPIED_LOCALIZED).read_bytes(), before)
        self.assertEqual((ROOT / "test/Plugins/random.bundle/Contents/Info.plist").read_bytes(),
                         original("test/Plugins/random.bundle/Contents/Info.plist"))

    def test_original_native_recipes_and_secret_names_preserved(self):
        before = original(".github/workflows/release.yml").decode()
        after = (ROOT / ".github/workflows/release.yml").read_text()
        for name in ("build x64", "import Developer ID certificate",
                     "store notarytool credentials", "codesign"):
            with self.subTest(step=name):
                self.assertEqual(block(before, name), block(after, name))
        secrets = r"\$\{\{ secrets\.([A-Z_]+) \}\}"
        self.assertEqual(re.findall(secrets, before), re.findall(secrets, after))
        for required in ('xcrun stapler staple "$DMG_PATH"', 'ARCHS="arm64 x86_64"', "-configuration Default",
                         "CODE_SIGNING_ALLOWED=NO", "MACOS_DEPLOYMENT_TARGET: '11.0'",
                         "PRODUCT_NAME: random", "BASE_DIR: test", "if-no-files-found: error"):
            self.assertIn(required, before)
            self.assertIn(required, after)
        for archive in ("ZIP_PATH", "DMG_PATH"):
            self.assertIn(f'xcrun notarytool submit "${archive}"', before)
            self.assertIn(f'notarize "${archive}"', after)
        self.assertIn('if [ "$STATUS" != "Accepted" ]; then', after)
        self.assertNotIn("i386", block(after, "verify universal binary"))
        self.assertIn('lipo -verify_arch arm64 x86_64 "$BIN"', after)

    def test_manual_immutable_preflight_native_and_publication_wiring(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertRegex(workflow, r"(?m)^name: Build$")
        self.assertEqual(workflow.split("on:\n", 1)[1].split("\npermissions:", 1)[0].strip(),
                         "workflow_dispatch:")
        self.assertEqual(workflow.count("uses: actions/checkout@v4"), 3)
        self.assertEqual(workflow.count("ref: ${{ github.sha }}"), 3)
        self.assertEqual(workflow.count("GH_TOKEN: ${{ github.token }}"), 2)
        self.assertIn("group: release-publication\n  cancel-in-progress: false", workflow)
        self.assertIn("needs: [version, build-windows]", workflow)
        self.assertIn("needs: version", workflow)
        gates = ["validate source and publication availability", "build x64",
                 "upload windows binaries", "download windows binaries",
                 "import Developer ID certificate", "store notarytool credentials",
                 "build macOS plugin (universal)", "verify universal binary",
                 "merge Windows binaries into the bundle", "- name: codesign",
                 "package, notarize & staple", "- name: create GitHub Release"]
        positions = [workflow.index(name) for name in gates]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(workflow.index("--verify-bundle"), workflow.index("- name: codesign"))
        self.assertIn('release.py check --sha "$SELECTED_SHA"',
                      block(workflow, "validate source and publication availability"))
        final = block(workflow, "create GitHub Release")
        self.assertEqual(final.count("python3 "), 1)
        self.assertIn('publish --sha "$SELECTED_SHA" "$ZIP_PATH" "$DMG_PATH"', final)
        for prohibited in ("continue-on-error", "always()", "${{ inputs.", "softprops/",
                           "github.ref_name", "branches:", "refs/heads/master"):
            self.assertNotIn(prohibited, workflow)

    def test_bump_dispatch_and_no_automatic_or_destructive_publication(self):
        workflow = (ROOT / ".github/workflows/bump-version.yml").read_text()
        self.assertIn("ref: ${{ github.sha }}", workflow)
        self.assertIn("SELECTED_BRANCH: ${{ github.ref_name }}", workflow)
        self.assertIn('test "$REF_TYPE" = branch', workflow)
        self.assertIn("fetch-depth: 0", workflow)
        for mode in ("patch", "minor", "major"):
            self.assertIn(f"- {mode}", workflow)
        helper = (ROOT / ".github/scripts/release.py").read_text()
        for prohibited in ('"DELETE"', '"PATCH"', "--clobber", "--force",
                           '"gh", "release", "create"', '"git", "tag"'):
            self.assertNotIn(prohibited, helper)
        self.assertIn('"push.followTags=false"', helper)
        self.assertIn('"--no-follow-tags"', helper)
        self.assertNotIn("workflow_dispatch", helper)


if __name__ == "__main__":
    unittest.main()
