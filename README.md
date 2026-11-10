![version](https://img.shields.io/badge/version-17%2B-3E8B93)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm%20|%20win-64&color=blue)
[![license](https://img.shields.io/github/license/miyako/4d-plugin-random)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/4d-plugin-random/total)

# 4d-plugin-random

`random` exposes two commands that generate cryptographically-secure random data using the operating system's native RNG — [`SecRandomCopyBytes`](https://developer.apple.com/documentation/security/1399291-secrandomcopybytes) on macOS, [`BCryptGenRandom`](https://learn.microsoft.com/en-us/windows/win32/api/bcrypt/nf-bcrypt-bcryptgenrandom) on Windows. Neither command touches 4D's document/file system — everything happens in memory.

| Command | Returns | Purpose |
|---|---|---|
| [generate random number](#generate-random-number) | `Object` | Returns a single random 32-bit integer, wrapped in a status object |
| [generate random bytes](#generate-random-bytes) | `Blob` | Returns a caller-specified number of random bytes as a `Blob` |

**Platforms:** macOS · Windows

## Manual versioning and publication

**Bump version** and **Build** are separate manual Actions. Neither runs on a
push or pull request. Select the intended branch in Actions; Bump accepts only
`patch`, `minor`, or `major`. It validates the selected immutable `github.sha`,
the clean checkout, and the unchanged remote branch, then commits source
metadata only. It never tags, releases, builds, or dispatches another workflow.
The executable `.github/scripts/bump-hooks/pre-push` is mandatory both before
editing and immediately before pushing. Its advertised-ref check permits exactly
one branch update, from the exact selected old SHA to literal new HEAD, with
exactly one direct parent. Advance, rewind, deletion, and same-result races fail.
The non-force push disables follow-tags both in config and on the command line.

Run **Build** separately on the desired branch. Its exact name is `Build` and
its `workflow_dispatch` has **zero inputs** (only GitHub's branch selector).
All checkouts use `github.sha`, not a mutable branch or existing tag. Strict,
consistent source `X.Y.Z` determines `vX.Y.Z`, `random-X.Y.Z.zip`, and
`random-X.Y.Z.dmg`; suffixes, leading zeros, duplicate keys, and drift fail.
Coordinated source authority consists of six each `MARKETING_VERSION` and
`CURRENT_PROJECT_VERSION` assignments in `random.xcodeproj/project.pbxproj`,
`Info.plist`'s display version and build version **if already present**, and
`English.lproj/InfoPlist.strings`' display and descriptive versions.
Valid Xcode references are preserved, not replaced with literals. Bumps preserve
file modes, BOMs, CR-only/CRLF/mixed endings, and unrelated bytes. Prebuilt
bundles, libraries, dependencies, and 4D database counters are not bump inputs.

### Authority and migration

At intake, the repository API's `default_branch` and symbolic `origin/HEAD`
both identified `master`. The committed workflows were a manual, tag-producing
Bump and a tag-push/typed-tag-dispatch Build & Release; neither had PR triggers.
Rename-follow history leads to
[`9aef13edc6a339fa2740223c617e33afd7df0f21`](https://github.com/miyako/4d-plugin-random/commit/9aef13edc6a339fa2740223c617e33afd7df0f21):
its Bump header explicitly coordinates MARKETING, CURRENT, and plist fields.
That declared policy wins over the buggy marketing-first implementation.
Existing marketing and source plist authority is **1.0.5**. This migration
repairs six stale CURRENT values (`1.0.3`) and source localized display metadata
(`1.0.1`) to **1.0.5**, without an intake increment or insertion of the absent
`CFBundleVersion`. The Xcode `PBXVariantGroup` selects
`English.lproj/InfoPlist.strings` through file reference
`089C167EFE841241C02AAC07` and resource build file `8D01CCCA0486CAD60068D4B7`.
Only this source localization is repaired and co-bumped, preserving its CR-only
bytes and executable mode.
`test/Plugins/random.bundle/Contents/Resources/English.lproj/InfoPlist.strings`
is a **prebuilt destination**, not a source input: its UTF-16LE BOM, CR-only
content and stale `1.0.1` remain unchanged, as do the prebuilt plist and binaries.
Build invalidates, rebuilds and validates those exact metadata destinations.

The complete committed workflows, helpers, and tests used as prior art are:

- [`miyako/4d-plugin-native-encoding-detection@c42ca565857f674bde5ea6ed3435c87375eda4a7`](https://github.com/miyako/4d-plugin-native-encoding-detection/tree/c42ca565857f674bde5ea6ed3435c87375eda4a7/.github)
- [`miyako/4d-plugin-file-information@19848973601acfd3a2b5070230fa40cdc7e4edb2`](https://github.com/miyako/4d-plugin-file-information/tree/19848973601acfd3a2b5070230fa40cdc7e4edb2/.github)
- [`miyako/4d-plugin-common-string-algorithm@2606a68201866ec9fcbb38b51462d79215f504ef`](https://github.com/miyako/4d-plugin-common-string-algorithm/tree/2606a68201866ec9fcbb38b51462d79215f504ef/.github)
- [`miyako/4d-plugin-window-button@3f14facb9aaaaff0e22eeb8e07531163680c3a8a`](https://github.com/miyako/4d-plugin-window-button/tree/3f14facb9aaaaff0e22eeb8e07531163680c3a8a/.github)
- [`miyako/4d-plugin-raw-picture-data@8144537fdc8dcc3d0c053dfe97aff0a8c0c3a6f9`](https://github.com/miyako/4d-plugin-raw-picture-data/tree/8144537fdc8dcc3d0c053dfe97aff0a8c0c3a6f9/.github), including lexical same-line quoted/unquoted duplicate-assignment detection for both project version keys.

### Native pipeline and publication safety

Build retains Windows x64 MSBuild Release (`v143` and Windows SDK `10.0`
overrides) followed by macOS `Default`, `arm64 x86_64`, deployment override
`11.0`, and disabled Xcode signing before the original Developer ID signing
steps. Windows output comes only from the actual Release|x64 OutDir,
`test/plugins/random.bundle/contents/windows64/random.4DX`, and the downloaded
artifact must be exactly `winbuild/random.4DX`; recursive stale-first searches
are forbidden. Cleanup invalidates exact rebuilt outputs and fails on errors,
preserving committed Windows files, QuickLook and other resources.
`lipo -verify_arch arm64 x86_64` requires both slices. Rebuilt plist and localized
metadata (including UTF-16 or binary plist strings) must match selected source.
The original pipeline has no i386 exclusion gate; none is invented here.
The Xcode copy phase remains unchanged: both `manifest.json` and `constants.xlf`
use `dstSubfolderSpec = 7` and `dstPath = ..` (the Contents root). The already
committed `Contents/Resources/constants.xlf`, root manifest, and other resource
files are preserved; no nonexistent `Resources/manifest.json` gate is added.

The existing certificate and notary secret **names** are unchanged:
`APPLE_DEVELOPER_ID_CERTIFICATE`, `APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD`,
`KEYCHAIN_PASSWORD`, `NOTARYTOOL_APPLE_ID`, `NOTARYTOOL_TEAM_ID`, and
`NOTARYTOOL_PASSWORD`. Both ZIP and DMG notarization require a successful command
**and** JSON status `Accepted`; DMG stapling must succeed before publication.
The original empty release body and ZIP/DMG assets are retained.

One constant cross-branch publication concurrency group uses
`cancel-in-progress: false`. Before any native work, and again after all native,
signing, notarization and stapling gates, authenticated, fully paginated release
lists (including drafts) and exact remote tag absence are required.
HTTP 404/auth/network failures, malformed responses, and errors on later pages
fail closed. Publication uses create-only `POST git/refs` at the selected SHA,
verifies that remote tag, then a separate `POST releases` with explicit tag,
name, target, empty body, `draft: false`, and `prerelease: false`.
Assets upload only to that newly returned release ID. No existing release is
reused or updated; no deletion, clobber, retry, or rollback is performed.

**Partial-publication recovery:** failures retain any created tag, release, and
uploaded assets as evidence. Inspect the failed run, exact remote tag SHA,
release ID and assets before any operator action. Do not rerun Build expecting
it to overwrite or finish a partial release: availability checks intentionally
reject that state. Resolve retained state manually under repository policy, or
use a new reviewed source version and Build. The automation never deletes
evidence or automatically cleans up a failed publication.

Offline regression checks (no real native/signing/notary/4D execution):

```sh
actionlint
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s .github/tests -v
```

The workflow suites use disposable Git/bare-origin repositories, mocked authenticated
API responses, and closed-PATH native/PowerShell stubs. Run the same suite in a
disposable copy after a genuinely guarded helper Bump to ensure fixtures derive
their versions from source, rather than relying on this intake version.

### Native string ownership audit

The ownership regression additionally compiles real native sources on macOS;
it does not run 4D or any release workflow. Run it independently with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 .github/tests/test_ownership.py -v
```

Audit baseline: `a75f3ada73fdfea2d74c78b955cfc5b31de50f24`. Guidance:
[`miyako/skills` at `e6b492f`](https://github.com/miyako/skills/blob/e6b492fcb81e5e1a7f7db723594b420e63bf6eb9/4d-skills/skills/4dplugin/SKILL.md)
(merged PRs 69 and 71). The bundled SDK implementation SHA-256 is
`5bac571c5c4cafd280fa7f23d775458a23701287e8735be27d4a2190002f4e3f`.
Its `rev : v14` comment is not an upstream release identity.

| First-party surface | Build/command reachability | Ownership result |
|---|---|---|
| `4DPlugin-random.cpp`, headers, source and bundled manifests | Both projects compile dispatcher selectors 1/2; commands use object parameters and object/blob results | `ob_get_n` is reachable through `options.size`, including a string-valued mismatch. Returned status objects transfer to 4D without local disposal. |
| `support/4DPlugin-JSON.cpp/.h` | Entire helper compiled on macOS/Windows; only boolean/numeric setters, property existence and numeric getter currently called by commands | Four string setters now start Undefined before receiving an independent owned string. String/boolean/number getters clear the owning property result after copying, even for mismatches. Collection mismatch clears; a successful collection reference still transfers to the caller. |
| `C_TEXT`, `C_BLOB`/`CBytes`, `C_LONGINT`, `C_TYPES` | Compiled wrappers, dormant from current command bodies | Text parameter reads copy borrowed content; output uses the engine content setter, not array transfer. Blob text conversions use local copied strings. |
| `ARRAY_TEXT` | Tracked but absent from both native project source lists | Reads copy array elements before mutation; writes allocate independent strings and transfer them without local disposal. No normal-path string lifetime defect confirmed. |

Third-party jsoncpp headers/static libraries and committed prebuilt binaries are
not implementation inputs to the fix. No `CPicture` or `C_POINTER` class,
first-party SDK application-path/picture-data getter call, or charset bytes-to-UTF-16-unit
conversion helper exists here. Manifest absence of text arrays is not the basis
for excluding the dormant array wrapper.

The unchanged SDK creates an initial empty allocation for
`PA_CreateVariable(eVK_Unistring)` (line 4030), then
`PA_SetStringVariable` shallowly overwrites the descriptor (4254-4259).
Starting Undefined avoids losing that initial owner while retaining final
`PA_ClearVariable` cleanup. `PA_GetObjectProperty` explicitly requires clearing
the returned variable (636-651); copied text is consumed before that cleanup.
The collection getter's successful result remains owning: dispose it with
`PA_DisposeCollection` or transfer it, rather than clearing its local alias.
`PA_SetStringInArray` disposes the old element and transfers only for accepted
type/data/index/string conditions (4933-4948); rejected inputs stay caller-owned.
`PA_GetStringInArray` returns a borrowed descriptor (4736-4755).
`PA_SetUnistring` forwards buffer content to the engine (289-299); its host-side
copy behavior is modeled separately, not inferred from the array setter.

The harness includes the full unchanged SDK, actual helper, dispatcher,
`C_TEXT` and dormant `ARRAY_TEXT`, with a shim only at `gCall4D`. It uses real
CoreFoundation conversion, explicit ASCII/BMP/surrogate-pair/empty expectations,
ASan/UBSan, and string/handle/reference accounting with allocated and null empty
representations. LeakSanitizer is disabled on this macOS runner. The same final
harness against an immutable baseline archive fails 14 subcases: four allocated
empty setter leaks, eight property-result leaks, and two dispatcher-reachable
mismatch leaks. The fixed source passes all six tests (24 host invocations),
including replacement, shrink/clear, independent array outputs, rejected
type/index/null-data destinations, scalar copies, successful object/collection
handoffs, and synchronous stringify success/mismatch cleanup. To reproduce the
baseline comparison, set `RANDOM_OWNERSHIP_SOURCE_ROOT` to a separate extracted
baseline tree; the harness itself still comes from the fixed tree.

An unsigned arm64 Xcode `Development` build was also verified with
`CODE_SIGNING_ALLOWED=NO` and products/intermediates outside the repository.
The existing script skipped its Deployment-only signing/packaging/notarization
branch. Neither that build nor the shim certifies a real 4D host, Windows, or
Intel execution. SDK allocation-failure behavior, including resize count updates
after failed growth, remains deferred; normal-array cases do not certify it.
No release workflow, signing, notarization, publication or tracked binary update
is part of this audit.

---

## Requirements & platform notes

- **Both commands are declared `threadSafe: true`** in `manifest.json`, so 4D may call them concurrently from preemptive processes. Nothing in the implementation holds shared mutable state across calls, so this is safe as written.
- **Neither command raises a catchable 4D error.** Failure is communicated through the return value itself — a `success` key on the object for `generate random number`, or an empty `Blob` for `generate random bytes`. Always check the return value rather than wrapping the call in an error-trapping method.
- **`generate random bytes` caps the requested size at 64 MB per call.** A request above that (or a negative, non-numeric, or otherwise invalid `size`) is treated as a failure and returns an empty `Blob` — it does not raise an error and does not clamp silently down to 64 MB.
- No behavioral difference between macOS and Windows from the 4D language's point of view — both platforms return the same shapes for the same inputs.

---

## generate random number

### Syntax

```4d
$status:=generate random number
```

Per `manifest.json` (`"generate random number(&J):J"`), this command's signature declares an optional `Object` parameter. **The current implementation never reads it** — nothing is passed to it in the sample method, and the C++ source contains no parameter-reading call for this command at all. Any argument passed today is silently ignored; call it with no parameters, as shown above and in `TEST.4dm`.

### Result

| Key | Type | Present when | Description |
|---|---|---|---|
| `success` | Boolean | always | `True` if a random number was generated. |
| `value` | Real | `success` is `True` | The random number, as a signed 32-bit integer (`-2147483648` … `2147483647`). Every bit pattern in that range is possible, including negative values — this is a raw random 32-bit quantity, not a value bounded to a positive range. |

### Example

```4d
$status:=generate random number

If ($status.success)
	ALERT("Random value: "+String($status.value))
Else
	ALERT("Failed to generate a random number.")
End if
```

---

## generate random bytes

### Syntax

```4d
$blob:=generate random bytes
$blob:=generate random bytes(options)
```

### Parameters

| Parameter | Type | | Description |
|---|---|---|---|
| `options` | Object | → | *Optional.* If omitted, or if it has no `size` key, `size` defaults to **4** (the size of a 32-bit integer). |
| `options.size` | Real | → | *Optional key.* Number of random bytes to generate. Must be a finite, non-negative number no greater than **67108864 (64 MB)**; anything outside that range is treated as invalid input (see Error handling). A fractional value (e.g. `10.7`) is accepted and silently truncated toward zero (`10`). |
| Result | Blob | ← | The random bytes. |

### Example

```4d
 // From TEST.4dm — request 10 random bytes
$data:=generate random bytes(New object("size";10))

 // Default size (4 bytes) if you don't need to specify one
$data:=generate random bytes
```

---

## Error handling

| Command | On failure, returns | How to detect it |
|---|---|---|
| `generate random number` | An object with `success:false` and no `value` key | Check `$status.success` |
| `generate random bytes` | An empty `Blob` (`BLOB size($blob)=0`) | Check `BLOB size($blob)=0` |

Both commands fail the same underlying way: the OS-level RNG call didn't succeed, or (for `generate random bytes` only) the requested `size` was invalid — negative, non-finite, or over the 64 MB cap. Neither condition is distinguishable from the return value alone; if you need to know *why* a call failed, validate your own inputs before calling (e.g. check `size` is a sane positive number yourself) rather than relying on the plugin to explain the failure.

---

## Quick reference

```4d
 // Random 32-bit integer
$status:=generate random number
If ($status.success)
	$n:=$status.value
End if

 // Random bytes, explicit size
$data:=generate random bytes(New object("size";10))
If (BLOB size($data)>0)
	 // use $data
End if

 // Random bytes, default size (4)
$data:=generate random bytes
```
