"""Offline coordinated source metadata and byte-preservation regression suite."""

import importlib.util
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("source_fixture", ROOT / ".github/scripts/source_version.py")
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".source-tests-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.copy_source()
        self.current = source.read(self.root)
        self.drift = f"{int(self.current.split('.')[0]) + 1}.9.7"

    def copy_source(self):
        for name in source.SOURCE_PATHS:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)

    def snapshot(self):
        return {name: ((self.root / name).read_bytes(),
                       stat.S_IMODE((self.root / name).stat().st_mode))
                for name in source.SOURCE_PATHS}

    def plist(self, **values):
        path = self.root / source.PLIST
        data = plistlib.loads(path.read_bytes())
        data.update(values)
        path.write_bytes(plistlib.dumps(data))

    def reject(self, error=ValueError):
        before = self.snapshot()
        with patch.object(Path, "write_bytes") as write:
            with self.assertRaises(error):
                source.bump("patch", self.root)
            write.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_live_versions_and_each_bump_mode(self):
        self.assertEqual(source.SETTING_COUNT, 6)
        parts = list(map(int, self.current.split(".")))
        for mode, expected in (("patch", [parts[0], parts[1], parts[2] + 1]),
                               ("minor", [parts[0], parts[1] + 1, 0]),
                               ("major", [parts[0] + 1, 0, 0])):
            with self.subTest(mode=mode):
                self.copy_source()
                before = self.snapshot()
                new = ".".join(map(str, expected))
                self.assertEqual(source.bump(mode, self.root), new)
                self.assertEqual(source.read(self.root), new)
                for key in source.KEYS:
                    self.assertEqual(source.text(self.root / source.PROJECT).count(
                        f"{key} = {new};"), 6)
                self.assertNotIn("CFBundleVersion",
                                 plistlib.loads((self.root / source.PLIST).read_bytes()))
                for name, (value, mode) in before.items():
                    self.assertEqual(self.snapshot()[name],
                                     (value.replace(self.current.encode(), new.encode()), mode))

    def test_invalid_missing_extra_and_inline_assignments_never_write(self):
        path = self.root / source.PROJECT
        original = source.text(path)
        for key in source.KEYS:
            existing = f"{key} = {self.current};"
            for value in ("1.2", "14.3", "77", "v1.2.3", "01.2.3", "1.2.3-beta",
                          "$(UNKNOWN)", "", self.drift,
                          "$(CURRENT_PROJECT_VERSION)", "${CURRENT_PROJECT_VERSION}"):
                for count in (1, 6):
                    with self.subTest(key=key, value=value, count=count):
                        path.write_text(original.replace(existing, f"{key} = {value};", count))
                        self.reject()
            for extra in (f"{key} = {self.current}\n", f"{key} =\n"):
                path.write_text(original + "\n" + extra)
                self.reject()
            path.write_text(original.replace(existing, "", 1))
            self.reject()
            for quoted in (False, True):
                name = f'"{key}"' if quoted else key
                for value in (self.current, f'"{self.current}"', self.drift,
                              f'"{self.drift}"', "$(UNKNOWN)", '"$(UNKNOWN)"'):
                    for location in ("after-version", "after-other", "own-line"):
                        with self.subTest(key=key, name=name, value=value, location=location):
                            extra = f"{name} = {value};"
                            mutated = (original.replace(existing, f"{existing} {extra}", 1)
                                       if location == "after-version" else
                                       original + ("\nOTHER = unchanged; " if location == "after-other"
                                                   else "\n") + extra + "\n")
                            path.write_text(mutated)
                            self.reject()
        for reference in source.REFERENCES:
            path.write_text(original.replace(
                f"MARKETING_VERSION = {self.current};", f'MARKETING_VERSION = "{reference}";'))
            self.reject()

    def test_comments_and_strings_are_not_assignments_or_bump_targets(self):
        path = self.root / source.PROJECT
        noise = (
            '\n/* MARKETING_VERSION = 9.8.7; "CURRENT_PROJECT_VERSION" = 9.8.7; */\n'
            '// "MARKETING_VERSION" = 9.8.7; CURRENT_PROJECT_VERSION = 9.8.7;\n'
            'OTHER = "MARKETING_VERSION = 9.8.7; \\"CURRENT_PROJECT_VERSION\\" = 9.8.7;";\n'
            'MARKETING_VERSION_SUFFIX = unchanged; PREFIX_CURRENT_PROJECT_VERSION = unchanged;\n'
        ).replace("9.8.7", self.drift)
        path.write_bytes(path.read_bytes() + noise.encode())
        before = self.snapshot()
        new = source.bump("patch", self.root)
        for name, (data, mode) in before.items():
            self.assertEqual(self.snapshot()[name],
                             (data.replace(self.current.encode(), new.encode()), mode))

    def test_exact_quoted_and_unquoted_assignments_on_same_line_are_editable(self):
        project = self.root / source.PROJECT
        for quoted in (False, True):
            self.copy_source()
            keys = [f'"{key}"' if quoted else key for key in source.KEYS]
            row = " ".join(f'{key} \t= \t"{self.current}" /* keep */;' for key in keys)
            before = ("\ufeff" + " ".join([row] * 6) + "\r").encode()
            project.write_bytes(before)
            new = source.bump("patch", self.root)
            self.assertEqual(project.read_bytes(), before.replace(self.current.encode(), new.encode()))
            self.assertEqual(source.read(self.root), new)

    def test_supported_references_preserved_and_optional_build_key_co_bumped(self):
        for reference in source.REFERENCES:
            for marketing in ("$(MARKETING_VERSION)", "${MARKETING_VERSION}"):
                with self.subTest(reference=reference, marketing=marketing):
                    self.copy_source()
                    self.plist(CFBundleShortVersionString=reference, CFBundleVersion=reference)
                    project = self.root / source.PROJECT
                    project.write_text(source.text(project).replace(
                        f"CURRENT_PROJECT_VERSION = {self.current};",
                        f'CURRENT_PROJECT_VERSION = "{marketing}";'))
                    before = self.snapshot()
                    new = source.bump("minor", self.root)
                    self.assertEqual(source.read(self.root), new)
                    for name, (data, mode) in before.items():
                        self.assertEqual(self.snapshot()[name],
                                         (data.replace(self.current.encode(), new.encode()), mode))
        self.copy_source()
        self.plist(CFBundleVersion=self.current)
        new = source.bump("patch", self.root)
        self.assertEqual(plistlib.loads((self.root / source.PLIST).read_bytes())["CFBundleVersion"], new)

    def test_plist_invalid_duplicate_missing_and_unsupported_serialization(self):
        path = self.root / source.PLIST
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            for value in ("$(UNKNOWN)", "$(PRODUCT_NAME)", "", "invalid", 123, True, [], {}, self.drift,
                          "1.2", "14.3", "7", "01.2.3", "v1.2.3"):
                with self.subTest(key=key, value=value):
                    self.copy_source()
                    self.plist(**{key: value})
                    self.reject()
            for value in (self.current, self.drift):
                self.copy_source()
                if key == "CFBundleVersion":
                    self.plist(CFBundleVersion=self.current)
                path.write_text(source.text(path).replace(
                    "</dict>", f"<key>{key}</key><string>{value}</string>\n</dict>"))
                self.reject()
        self.copy_source()
        data = plistlib.loads(path.read_bytes())
        del data["CFBundleShortVersionString"]
        path.write_bytes(plistlib.dumps(data))
        self.reject()
        self.copy_source()
        original = path.read_bytes()
        for data in (b"not a plist", original.replace(b"</dict>", b""), plistlib.dumps([]),
                     plistlib.dumps(plistlib.loads(original), fmt=plistlib.FMT_BINARY),
                     original.replace(self.current.encode(),
                                      f"&#{ord(self.current[0])};{self.current[1:]}".encode())):
            path.write_bytes(data)
            self.reject()

    def test_localized_missing_drift_duplicate_and_quoted_keys(self):
        path = self.root / source.LOCALIZED
        original = source.text(path)
        for assignment in (f'CFBundleShortVersionString = "{self.current}";',
                           f'CFBundleGetInfoString = "random version {self.current}, Copyright 2020 miyako.";'):
            for value in ("1.0", self.drift, "", "invalid", "$(UNKNOWN)", "$(MARKETING_VERSION)",
                          "${CURRENT_PROJECT_VERSION}", "01.2.3", "1.2.3-beta"):
                path.write_text(original.replace(assignment, assignment.replace(self.current, value)))
                self.reject()
            path.write_text(original.replace(assignment, ""))
            self.reject()
        for key in ("CFBundleShortVersionString", "CFBundleGetInfoString"):
            path.write_text(original.replace(key, "UnknownKey"))
            self.reject()
            for name in (key, f'"{key}"'):
                for prefix in ("\n", 'CFBundleOther = "other"; '):
                    path.write_text(original + prefix + f'{name} = "{self.current}";\n')
                    self.reject()
            for malformed in (original + f'\n{key} = "unterminated\n',
                              original.replace(f'{key} = "', f"{key} = ").replace('";', ";")):
                path.write_text(malformed)
                self.reject()
        for old, new in (("random", "Other Product"), ("Copyright 2020", "Copyright 2023"),
                         ("miyako", "someone")):
            path.write_text(original.replace(old, new))
            self.reject()
        quoted = original
        for key in ("CFBundleShortVersionString", "CFBundleGetInfoString"):
            quoted = quoted.replace(key + " =", f'"{key}" =')
        quoted += '// CFBundleShortVersionString = "not assignment";\n'
        quoted += '/* "CFBundleGetInfoString" = "not assignment"; */\n'
        path.write_text(quoted)
        new = source.bump("patch", self.root)
        self.assertEqual(path.read_bytes(), quoted.replace(self.current, new).encode())

    def test_valid_inline_quoted_localization_preserves_nonversion_string_noise(self):
        path = self.root / source.LOCALIZED
        before = (
            '\ufeff/* CFBundleShortVersionString = "not assignment"; */\r'
            f'"CFBundleShortVersionString" \t= "{self.current}"; '
            f'"CFBundleGetInfoString" = "random version {self.current}, Copyright 2020 miyako."; '
            f'Other = "CFBundleShortVersionString = \\"{self.drift}\\";";\r'
        ).encode()
        path.write_bytes(before)
        new = source.bump("minor", self.root)
        self.assertEqual(path.read_bytes(), before.replace(self.current.encode(), new.encode()))
        self.assertEqual(source.read(self.root), new)

    def test_byte_preserving_bom_endings_modes_whitespace_and_quotes(self):
        for newline in ("\n", "\r", "\r\n", "mixed"):
            for bom in (b"", b"\xef\xbb\xbf"):
                for quoted in (False, True):
                    with self.subTest(newline=newline, bom=bom, quoted=quoted):
                        self.copy_source()
                        for i, name in enumerate(source.SOURCE_PATHS):
                            path = self.root / name
                            value = source.text(path)
                            if name == source.PROJECT:
                                for key in source.KEYS:
                                    token = f'"{self.current}"' if quoted else self.current
                                    value = value.replace(f"{key} = {self.current};",
                                                          f"{key} \t= \t{token} \t;")
                            lines = value.splitlines(keepends=True)
                            value = "".join(line.replace("\n", ("\r", "\r\n", "\n")[j % 3]
                                            if newline == "mixed" else newline)
                                            for j, line in enumerate(lines))
                            path.write_bytes(bom + value.encode())
                            path.chmod((0o755, 0o640, 0o644)[i])
                        before = self.snapshot()
                        new = source.bump("minor", self.root)
                        for name, (value, mode) in before.items():
                            self.assertEqual(self.snapshot()[name],
                                             (value.replace(self.current.encode(), new.encode()), mode))

    def test_excluded_prebuilt_schema_dependencies_and_unrelated_versions(self):
        names = {
            "test/Plugins/random.bundle/Contents/Info.plist",
            "test/Plugins/random.bundle/Contents/manifest.json",
            "test/Plugins/random.bundle/Contents/Resources/constants.xlf",
            "test/Plugins/random.bundle/Contents/Resources/English.lproj/InfoPlist.strings",
            "test/Plugins/random.bundle/Contents/Windows64/random.4DX",
            "test/Plugins/random.bundle/Contents/Windows/random.4DX",
            "test/Plugins/random.bundle/Contents/MacOS/random",
            "test/Plugins/random.bundle/Contents/_CodeSignature/CodeResources",
            "test/Plugins/random.bundle/Contents/QuickLook/Thumbnail.png",
            "test/Data/data.4DD", "test/Project/Sources/catalog.4DCatalog", "manifest.json",
            "constants.xlf", "constants.json", "4D Plugin API/4DPluginAPI.h",
            "4D Plugin API/Classes/C_TYPES.h", "random.vcxproj", "random.vcxproj.filters",
            "random.sln", "4DPlugin-random.h", "a/libjsoncpp.a",
            "random.zip", "random.dmg", "include/json/version.h",
            "lib/windows/jsoncpp.lib", "lib/windows64/jsoncpp.lib",
        }
        names.update(subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-files", "test/Plugins"], text=True).splitlines())
        for name in names:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        before = {name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mode)
                  for name in names}
        project = self.root / source.PROJECT
        project.write_bytes(project.read_bytes() + f"\n/* Historical {self.current} */\n".encode())
        self.plist(UnrelatedVersion=self.current)
        localized = self.root / source.LOCALIZED
        localized.write_bytes(localized.read_bytes() + f'\rUnrelatedVersion = "{self.current}";\r'.encode())
        source.bump("major", self.root)
        self.assertEqual(before, {name: ((self.root / name).read_bytes(),
                                        (self.root / name).stat().st_mode) for name in names})
        self.assertIn(f"Historical {self.current}".encode(), project.read_bytes())
        self.assertEqual(plistlib.loads((self.root / source.PLIST).read_bytes())["UnrelatedVersion"],
                         self.current)
        self.assertIn(f'UnrelatedVersion = "{self.current}";'.encode(), localized.read_bytes())

    def test_missing_source_encoding_invalid_mode_and_read_cli(self):
        for name in source.SOURCE_PATHS:
            self.copy_source()
            (self.root / name).unlink()
            before = {p: (self.root / p).read_bytes() for p in source.SOURCE_PATHS if p != name}
            with patch.object(Path, "write_bytes") as write:
                with self.assertRaises(FileNotFoundError):
                    source.bump("patch", self.root)
                write.assert_not_called()
            self.assertEqual(before, {p: (self.root / p).read_bytes() for p in before})
        self.copy_source()
        path = self.root / source.LOCALIZED
        path.write_bytes(source.text(path).encode("utf-16"))
        self.reject(UnicodeError)
        self.copy_source()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            source.bump("other", self.root)
        result = subprocess.run([sys.executable, str(ROOT / ".github/scripts/source_version.py")],
                                cwd=self.root, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), self.current)
        self.assertEqual(self.snapshot(), before)

    def test_built_plist_and_utf16_xml_binary_localization(self):
        contents = self.root / "built.bundle/Contents"
        localized = contents / "Resources/English.lproj/InfoPlist.strings"
        localized.parent.mkdir(parents=True)
        built = contents / "Info.plist"
        built.write_bytes((self.root / source.PLIST).read_bytes())
        value = source.text(self.root / source.LOCALIZED)
        fields = {"CFBundleShortVersionString": self.current,
                  "CFBundleGetInfoString": f"random version {self.current}, Copyright 2020 miyako."}
        serializations = [value.encode(), value.encode("utf-16"),
                          b"\xfe\xff" + value.encode("utf-16-be"),
                          plistlib.dumps(fields), plistlib.dumps(fields, fmt=plistlib.FMT_BINARY)]
        for data in serializations:
            localized.write_bytes(data)
            source.verify_bundle(contents.parent, self.root)
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            for key in fields:
                localized.write_bytes(plistlib.dumps(
                    {**fields, key: fields[key].replace(self.current, self.drift)}, fmt=fmt))
                with self.assertRaises(ValueError):
                    source.verify_bundle(contents.parent, self.root)
        localized.write_bytes(value.replace(self.current, self.drift).encode("utf-16"))
        with self.assertRaises(ValueError):
            source.verify_bundle(contents.parent, self.root)
        localized.write_bytes(value.encode())
        for data in ([], {"CFBundleShortVersionString": self.drift},
                     {**fields, "CFBundleVersion": self.drift}):
            built.write_bytes(plistlib.dumps(data))
            with self.assertRaises(ValueError):
                source.verify_bundle(contents.parent, self.root)
        self.plist(CFBundleVersion=self.current)
        built.write_bytes(plistlib.dumps({"CFBundleShortVersionString": self.current}))
        with self.assertRaises(ValueError):
            source.verify_bundle(contents.parent, self.root)
        built.write_bytes((self.root / source.PLIST).read_bytes())
        source.verify_bundle(contents.parent, self.root)


if __name__ == "__main__":
    unittest.main()
