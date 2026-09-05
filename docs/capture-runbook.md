# Capture Runbook (Gate C)

This is the mandatory operator procedure for capturing and durably preserving one registry
release. It stays mandatory permanently, even after the scheduled workflow is deployed and
proven: the source only serves its latest files, GitHub scheduling is not an exact-time guarantee,
and a release missed on its publication day cannot be recovered from the rolling
URLs (D-06/D-15). `capture-current.yml`'s `capture`/`status` jobs, its `workflow_dispatch`
trigger, and this runbook's local `run` command all invoke the identical production
`python -m calico_capture run` entry point -- there is no second, less-tested capture path.

## When to run this

- On the expected first- or third-Wednesday publication day, any time the scheduled workflow has
  not yet run, appears delayed, or its safe status document on `published-data` has not updated.
- Whenever the scheduled workflow is disabled, failing, or unavailable (GitHub notes scheduled
  runs can be delayed or dropped during high load, and public-repository schedules can be disabled
  after inactivity).
- Before relying on any report or dashboard number for a release date not yet reflected in the
  safe status document.

## Required local setup

- A working product virtual environment with `requirements-dbt.txt` and `requirements-capture.txt`
  installed (`python -m pip install --requirement requirements-dbt.txt` and
  `--requirement requirements-capture.txt`).
- The dedicated automation Backblaze B2 application key (ask the owner directly through the
  established private, non-echoing handoff -- never over chat, ticket, or committed file). Export
  it only into your own shell's environment, never into a script or committed file:

  ```
  set CALICO_B2_APPLICATION_KEY_ID=<owner-supplied-value>
  set CALICO_B2_APPLICATION_KEY=<owner-supplied-value>
  ```

  These two exact environment variable names are the only way any `calico_capture` command
  accepts the automation credential. It is never a command-line argument, and it is never printed
  by any command below.

## Commands

Every command emits exactly one compact machine-readable JSON document to stdout and one fixed
status line to stderr; commands never print a credential, absolute path, source URL, or caught
exception's own text.

### Run one capture attempt (the mandatory manual/local path)

```
python -m calico_capture run --trigger local
```

Runs the identical bounded restore/download/admit/archive/build sequence the scheduled and
`workflow_dispatch` triggers run: up to three domain attempts at 0, 90, and 180 minutes, stopping
on `accepted`, terminal `rejected`, or a `no_new_release` result matching the expected capture
date. Exit code `0` accepted, `1` rejected, `2` no_new_release, `3` operational_error -- identical
to the local `calico_landing` `admit` CLI's own exit-code contract.

### Attest the automation credential's scope

```
python -m calico_capture attest
```

Authorizes the automation credential and reports only the exact bucket name, name prefix, and
capability count it proved -- never the credential itself. Run this once after any credential
rotation, before relying on the scheduled workflow.

### Seed accepted history additively

```
python -m calico_capture seed --store <owner-supplied-admitted-store-path>
```

For every committed catalog release whose local manifest is present, verifies its hash against the
committed catalog anchor, then additively synchronizes it into the archive as one immutable
transaction (D-03). A catalog release with no local manifest yet is safely skipped, not treated as
a failure -- it stays skipped until its own local data and hash gate are ready. `--store` must be
an existing, owner-controlled directory outside every Git worktree; this command never moves,
rewrites, or deletes anything at that path.

### Prove a clean-machine restore and rebuild

```
python -m calico_capture restore-build --store <fresh-external-store-path>
```

Restores every committed catalog release from the archive into `--store` -- a caller-owned,
freshly prepared external directory outside every Git worktree, with no dependency on any
laptop-local input -- verifying every manifest and object hash before a single byte is written,
then runs the existing real-mode `calico_dbt` build once the full history is restored (D-13). This
is the durability proof: a restore-and-build succeeding here, not an upload count or a provider
console screenshot, is what actually demonstrates history survives the loss of this laptop.

### Inspect retention posture (owner-only, read-only)

```
python -m calico_capture inspect-retention
```

Requires a **separate** owner-only credential pair, never the automation key:

```
set CALICO_B2_RETENTION_KEY_ID=<owner-supplied-value>
set CALICO_B2_RETENTION_KEY=<owner-supplied-value>
```

Performs exactly one read call and reports two closed categories -- whether an existing lifecycle
rule could hide or delete archived objects, and whether Object Lock is already enabled. It never
mutates a lifecycle rule, Object Lock setting, or any other bucket configuration. Clear this
credential from your shell as soon as the command completes; it is never wired into automation.

### Audit a hosted run's log and status output

```
python -m calico_capture audit-hosted-output --log-file <path> --status-file <path> [--credential-env NAME]
python -m calico_capture audit-hosted-output --mode authorization-probe --log-file <path>
```

`--mode` defaults to `capture-status`: validates a real hosted status document against the closed
capture-status schema and scans a log file for non-allowlisted content (paths, tracebacks,
provider text, the source host) and, if `--credential-env` names an environment variable currently
holding a private value, for that exact value appearing anywhere in either file. `--mode
authorization-probe` instead validates the no-secret `authorization-probe` workflow job's own
fixed `CALICO_AUTHZ_PROBE::<category>=<denied|allowed>` marker lines (no `--status-file`, since
that job never produces a capture-status document). Both modes report only a fixed pass/fail
category.

### Verify publication in order, then publish the accepted history

Perform these three checks in order. First, run the fully offline replay. It uses only synthetic
fixtures and a disposable local Git repository; it neither reads the private archive nor writes a
hosted ref:

```
python -m unittest tests.publish.test_replay -v
```

Second, obtain an owner-provided publication-read credential through the private environment
handoff. This must be a newly and separately scoped key with exactly `listFiles` and `readFiles`
on the same fixed bucket and prefix as the admitted archive. It is not the capture credential and
must use these publication-only environment names:

```
set CALICO_B2_PUBLISH_KEY_ID=<owner-supplied-value>
set CALICO_B2_PUBLISH_KEY=<owner-supplied-value>
```

Prepare fresh external store and staging directories, then prove the entire production sequence
without changing the remote ref first:

```
python -m calico_publish publish --mode real --store <fresh-external-store-path> --staging <fresh-staging-path> --remote origin --target-ref published-data --dry-run
```

Third, only after the dry run succeeds and the repository owner directly authorizes the public
write, confirm that those same two publication-only secret names are wired into the
`capture-automation` environment and dispatch the hosted republish path:

```
gh workflow run capture-current.yml --ref main -f mode=republish
```

Inspect the resulting `publish` job and confirm that it completed successfully. Do not substitute
the older capture-key names for the publication-only names.

If the hosted workflow is unavailable, the manual fallback is to repeat the same one-process
sequence without `--dry-run`, again only after direct owner authorization:

```
python -m calico_publish publish --mode real --store <fresh-external-store-path> --staging <fresh-staging-path> --remote origin --target-ref published-data
```

The command restores hash-verified history, builds once, writes every allowlisted export and its
manifest, runs the publication gate and streaming privacy scan over those exact staged files, and
then makes one non-force atomic update. Clear the dedicated credential from the shell afterward.
This manual sequence remains the fallback publication path even after the hosted workflow works.

## Missed or delayed runs

A delayed, disabled, or failed scheduled run is expected operational behavior, not a data-loss
event by itself -- GitHub documents that scheduled workflows can be delayed under load and that
schedules on a repository can be disabled after inactivity. Treat any of the following as a signal
to run `python -m calico_capture run --trigger local` immediately:

- The safe status document on `published-data` still shows the prior expected as-of date well
  past the usual publication window.
- The scheduled workflow's most recent run in the Actions tab is missing, failed, or older than
  expected.
- You cannot confirm the workflow ran at all for a given first- or third-Wednesday.

If the source has already rotated past the missed release by the time you notice, that release is
gone permanently -- there is no recovery path beyond preventing the next miss. This is exactly why
the manual runbook stays mandatory rather than a documented fallback that is expected to age out.

## Non-deletion rule

Never delete, move, or rewrite the local owner-controlled admitted store, its `attempts/` or
`releases/` directories, or any workshop input after a successful seed or scheduled run (D-15).
Durable B2 storage removes the single-laptop dependency; it does not authorize discarding the
local recovery copy. If disk space is a genuine concern, raise it as a separate, explicit decision
-- never as a side effect of running a capture command.
