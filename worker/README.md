# Tennis coaching workers

Install this package on a Mac or Windows computer, independently of any IDE:

```sh
python3 -m venv ~/.venvs/tennis-coach
~/.venvs/tennis-coach/bin/pip install /path/to/tennis-web/worker
```

The install copies the package; the source checkout can subsequently move. For a
GitHub install after release, use the worker subdirectory at a reviewed commit.
Windows uses the venv's `Scripts` directory. Python 3.11+, FFmpeg (libx264 and
zscale/tonemap filters) and Codex CLI are required. Sign in using `codex login` on
the worker machine. The AI adapter requires ChatGPT sign-in and removes API-key
environment variables from its subprocess; it does not move tokens to NAS.

Copy `config.example.json` to a private configuration directory. Set mounted paths
for the published library media (read-only) and **separate coaching artifacts**
(write access). Set the same random worker token on the web service using
`TENNIS_COACH_WORKER_TOKEN`; configure `TENNIS_COACH_MEDIA_ROOT` to point to the same
artifact storage. Do not put private configuration in Git. Both workers need the
LAN server URL. The website stays internal; provider requests are outbound only.

Run independently, in two terminals or separately managed services:

```sh
tennis-coach-worker --config /private/config.json --role render
tennis-coach-worker --config /private/config.json --role ai
```

`--once` claims at most one job. `--status` reads that role's local status. Foreground
workers stop on Ctrl-C or SIGTERM; starting them again resumes queue consumption.
Render and AI processes have separate state files and worker identities. They do
not import, install or modify the existing tennis-processing worker/service.
A role-specific OS service manager may run either command; see `install_service.py`
for optional macOS launch agents. No OS service is installed automatically.

## Data and recovery

Each role writes `local_state/<role>/state.json`, and a private journal per task
and attempt. Rendering happens locally, followed by one copy to a fenced artifact
directory on shared storage. Only declared artifacts are copied; worker credentials
and local job journals stay on the worker. A small recipe manifest is the completion
marker. Prompt, video and provenance can be used manually in a regular ChatGPT chat.

The AI process reads the prepared frames, verifies hashes, copies them locally and
uses Codex image inputs. It disables shell, application/browser and plugin tools and
ignores user project configuration for this invocation. It never claims native
video analysis. Up to 32 frames at approximately four samples per source second are
provided; longer selections have lower effective temporal sampling. These cannot
establish every contact event or complete continuous motion.

A lease lasts 90 seconds with a 20-second heartbeat. Expired render/pre-call AI
leases can be reclaimed; expired calls already sent to the provider become
`needs_review`, without an automatic repeat. Completed local answers are retained
in the task journal. Check them before explicitly submitting a new request. The
system cannot guarantee exactly-once provider execution after a lost connection.

Artifacts currently have **no automatic deletion**. Keep metadata/responses even
when removing old media manually; the website reports an expired preview without
discarding advice. Automatic TTL/pinning and private journal cleanup remain future
retention work. Do not delete an active task's files.

Find a local prepared video + prompt without opening an AI connection:

```sh
tennis-coach-worker --config /private/config.json --role render --package JOB_ID
```

Open that printed folder in Finder, attach `excerpt.mp4` to ChatGPT and paste or
attach `prompt.txt`. The web interface can copy your edited question with export
timing. Merely opening ChatGPT does not attach local files automatically.

For automatic macOS mounts, an optional private `mounts` array in configuration
contains `{ "remote": "//username@nas/share/subfolder", "path": "/local/mount" }`
entries. Use existing saved NAS login credentials, never inline passwords. Before
claiming any job, workers reconnect configured mounts and verify required storage.
If storage/login is unavailable, local status becomes `storage_unavailable` and
work stays queued. Windows should use pre-mounted paths and omit this option.

### macOS Local Network identity

On some macOS versions, a LaunchAgent invoking a Python interpreter directly can
receive `No route to host` even though interactive requests succeed and generic
Python entries show as allowed. Use the named `mac_launcher.m` app wrapper
with `install_service.py --launcher /path/to/TennisCoach` so network permission
is attributed to Tennis Coach. Grant Local Network access through macOS normally;
do not reset privacy settings or route around a denied permission. The wrapper
connects to the configured NAS before spawning the role-specific Python worker.

Build that wrapper with an installed, matching Apple compiler/SDK pair:

```sh
python worker/build_mac_app.py --output '/path/to/Tennis Coach.app' \
  --developer-dir /Applications/Xcode.app/Contents/Developer
python worker/install_service.py install --role render --config /private/config.json \
  --executable /path/to/venv/bin/tennis-coach-worker \
  --launcher '/path/to/Tennis Coach.app/Contents/MacOS/TennisCoach'
```

Repeat installation for `--role ai`. Omit `--developer-dir` when the system-selected
Apple toolchain and SDK already match. Build the app before starting services; stop
services before rebuilding an installed launcher. Local ad-hoc signing identifies
this build on the operator's Mac; it does not claim Apple notarization.

Synology custom ACLs can allow SMB access while Linux mode bits prevent a read-only
container from reading the same files. After the operator approves shared read
access for the **dedicated coaching-artifact folder**, set `readable_exports: true`
in private worker configuration. The media worker then sets its new task/attempt
directories to 0755 and files to 0644, publishing the manifest last. It does not
change originals, the published library, application state or unrelated paths.
This option defaults off; configure the artifact root's own read/traverse access
explicitly at deployment.

## Structured coaching (worker 0.2.0)

For the evidence-linked coaching UI, upgrade this installed package as well as the
website container. The AI role advertises `coaching-annotations-v1`; older workers
leave these requests queued instead of consuming them incorrectly. No new account,
queue or configuration secret is required. The existing artifact mount needs write
access to `evidence/frames/`; the website reads it through its existing read-only
mount. These content-addressed snapshots are retained separately from expiring
prepared videos. Keep them with coaching database backups; never apply export TTLs
to evidence. See [the contract](../docs/COACHING-ANNOTATIONS.md) for review, retention,
reprocessing and recovery rules. The worker verifies/prepares evidence before
marking a provider call started and uses Codex structured output for new requests.
