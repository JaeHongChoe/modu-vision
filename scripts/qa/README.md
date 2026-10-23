# Real OS assistant key storage QA

`assistant-safe-storage.cjs` runs the production `AssistantKeyStore` with Electron's actual `safeStorage`. It saves one hardcoded, non-secret dummy, exits, reads/decrypts it in a new Electron process, removes it, and confirms removal in another new process. It never starts the application renderer or backend or calls an assistant provider.

```sh
node --test scripts/tests/assistant-safe-storage-qa.test.cjs src/main/assistant/keyStore.test.cjs
node scripts/qa/assistant-safe-storage.cjs
```

The runner prints its new artifact directory. To select a destination, add `--out-dir /absolute/path/to/a/new-directory`; any existing path is refused. `--prepare` only creates the isolated app/profile and records `prepared`. Preparation and the Node tests do not count as real OS storage verification.

Each run gets a UUID app name, separate `userData`, session data, logs and crash dumps. The child environment excludes API credentials, application paths and Node/Electron startup flags. There is no mock-keychain or plaintext fallback. The native home/session remains available for real OS storage. On macOS, [Electron 44.5.1 sets its Keychain service and account from the app name](https://github.com/electron/electron/blob/v44.5.1/shell/browser/electron_browser_main_parts.cc#L636-L639), so this QA uses its own service rather than the application's or another app's entry.

The runner does not approve OS permission prompts. If a call blocks, its 20-second bound ends the owned process and leaves an incomplete summary plus the last phase's `*-progress.json`. Record the exact prompt without approving it; a timeout is not a pass. [Electron documents that synchronous Keychain calls can block for user input](https://www.electronjs.org/docs/latest/api/safe-storage#synchronous-api).

`summary.json` must report four passed phases with separate PIDs. The per-phase receipts record Electron/platform versions, OS protection availability, file hashes, public `hasKey`, removal, file/folder modes and attempted network requests. They contain no plaintext or ciphertext. The read receipt must match the save file hash, and the two removal receipts must report `hasKey: false`. `run.json` pins the production key-store/provider source hashes.

The dummy provider key is removed from this run's `providers.json`. On macOS, the UUID service's OS encryption master may remain in Keychain; the harness deliberately does not enumerate, read via Keychain tools, or delete Keychain entries. Its exact QA service/account are in `run.json`.

This verifies the source key-store integration in the installed Electron runtime on the current OS. It does not verify paid API access, image transmission consent, signed packaged application identity, other operating systems or human acceptance.
