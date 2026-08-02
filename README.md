# 4d-plugin-random

`random` exposes two commands that generate cryptographically-secure random data using the operating system's native RNG — [`SecRandomCopyBytes`](https://developer.apple.com/documentation/security/1399291-secrandomcopybytes) on macOS, [`BCryptGenRandom`](https://learn.microsoft.com/en-us/windows/win32/api/bcrypt/nf-bcrypt-bcryptgenrandom) on Windows. Neither command touches 4D's document/file system — everything happens in memory.

| Command | Returns | Purpose |
|---|---|---|
| [generate random number](#generate-random-number) | `Object` | Returns a single random 32-bit integer, wrapped in a status object |
| [generate random bytes](#generate-random-bytes) | `Blob` | Returns a caller-specified number of random bytes as a `Blob` |

**Platforms:** macOS · Windows

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
