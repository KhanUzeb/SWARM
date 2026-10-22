# Security Policy

## Supported versions

Swarm is a self-hosted project. Only the latest `main` is supported with
security updates.

| Version | Supported |
| ------- | --------- |
| `main` (latest) | Yes |
| Older commits / forks | Best effort |

## Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.**

Email the maintainer or open a
[private security advisory](https://docs.github.com/en/code-security/security-advisories)
on this repository with:

- What is affected (endpoint, file, version/commit)
- Steps to reproduce or proof of concept
- Impact assessment, if known

You should receive an acknowledgement within 72 hours. We will coordinate a
fix and disclosure timeline with you.

## Security boundaries (by design)

- Provider keys and OAuth tokens connected through the UI are sealed with
  AES-256-GCM (scrypt-derived key, fresh nonce per value) before SQLite
  storage and are never returned by any API — only key hints and connection
  metadata. With `SWARM_SECRET` unset the store refuses to seal rather than
  falling back to a public literal; a row that cannot be decrypted reports
  `needs_reconnect`.
- Auth tokens are composite `<handle>:<raw>`; only the SHA-256 hash of the
  raw half is stored.
- The optional admin password is stored as PBKDF2-HMAC-SHA256, never plain
  text, must be at least 8 characters, and is locked out after five wrong
  attempts per handle in fifteen minutes.
- Registration is closed to remote callers unless they present a credential.
  The first admin can register from loopback with no forwarding headers;
  remote bootstrap requires `SWARM_SETUP_TOKEN` to be configured and
  presented (a proxy fails closed). A remote member needs
  `SWARM_INVITE_CODE`; re-registering an existing member is loopback-only. See
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#registration-and-first-admin).
- Every tool call passes `policy.evaluate_tool` before the registry can run
  it. `DENY` never executes, `ALLOW` is a short list of reversible local work
  and read-only fetches, and everything else needs a human approval bound to
  the exact agent, channel, tool, and canonical argument hash — single use,
  expiring (900 s default, `SWARM_APPROVAL_TTL_SECONDS`), resolvable only by
  the requester or an admin, and consumed once.
- Credential-shaped files are refused by the file tools and preview routes on
  read as well as on write: `.env*`, the database plus its WAL/SHM sidecars,
  SSH private keys and anything under `.ssh/`, `.git-credentials`,
  `.netrc`/`_netrc`, `.npmrc`, `.pypirc`,
  `credentials`/`credentials.json`/`secrets.json`, and `.git/config`.
- `fetch_url` and `browser_navigate` only reach public http(s) addresses. The
  host is resolved first, every answer must be global (loopback, private,
  link-local, CGNAT, reserved, multicast, and unspecified are refused), and a
  redirect is checked again before it is followed.
- The sandbox is a working-directory + timeout boundary, **not** a container
  or network-isolation boundary. Do not treat it as one.
- `system_*` tools run on the machine hosting the backend, bound to
  `SWARM_SYSTEM_ROOT`; `computer_run` runs in the sandbox directory. Both are
  off unless `SWARM_SYSTEM=1`, `POST /api/computer/run` is admin-only, and each
  execution call still needs its own approval while host tools are on.

## Threat model

### What the sandbox is

The sandbox is a **working directory plus a timeout**. That is the entire
boundary, and it is worth being exact about it:

- **Directory.** Shell tools are launched with `cwd=SANDBOX_DIR`
  (`agent._run_shell_tool`, `tools/computer.py::computer_run`). This is a
  starting directory, not a jail — `shell=True` means a command may address
  any absolute path the backend user can reach.
- **Timeout.** `SHELL_TIMEOUT` (`tools/computer.py`) and
  `SHELL_TIMEOUT_SECONDS` (`agent.py`) bound runtime, nothing else. The
  computer tool kills the process tree on timeout (`taskkill /T` on Windows, a
  new process group plus `SIGKILL` on POSIX).
- **Reduced environment.** `agent._shell_env` rebuilds a minimal `PATH`. Its
  own docstring says it plainly: *"Not a real isolation boundary — just keep
  the sandbox small."*

### What it protects against

- Accidental writes landing outside the intended workspace for ordinary,
  well-behaved commands.
- Runaway commands, via the timeout and the output caps
  (`OUTPUT_CAP`, `READ_CAP`, `WRITE_CAP`).
- Directory traversal through the **file tools**: `agent._safe_workspace_path`
  and `tools/system.safe_path` both resolve and then enforce
  `relative_to(base)`, so `../../../../etc/hosts` and absolute paths outside
  the root are refused. Verified, not assumed.
- Credential and database reads through tool and preview surfaces:
  `tools/system.is_protected_path` is shared by `system_read`, `system_write`,
  the system-file and sandbox preview routes, `read_workspace`,
  `write_workspace`, and `computer_open`.
- Unclassified and consequential tool calls: the policy gate sits in the
  execution path, not in the prompt, so a model that asks for a tool it was
  not offered, invents a custom tool, or is steered by injected text cannot
  skip the human decision. A blocked call ends the turn; a delegated agent's
  block ends the parent's turn.
- Server-side request forgery through the fetch and browser tools, including
  redirects.
- Destructive shell commands, via `tools/guard.py` and the narrower
  `_DANGEROUS` regex — a backstop that is trivially bypassable and should not
  be relied upon.

### What it does NOT protect against

A grep across `backend/` for `chroot`, `seccomp`, `namespace`, `unshare`,
`nsjail`, `firejail`, `setrlimit`, and `container` returns **no isolation
mechanism of any kind**.

- **No filesystem isolation.** A bot can read or write any path the backend
  user can reach, including `~/.ssh`, `~/.aws`, and the repo itself. The
  protected-file check covers named files on the tool and preview paths; it
  does not cover a shell command that opens them another way. `guard.py`
  blocks the common `cat`/`type`/`head`/`Get-Content` dotenv readers, not
  every program that can read a file.
- **No network isolation.** Bots inherit the host's full network. Outbound
  calls to arbitrary hosts are unrestricted; there is no egress filter. The
  public-URL guard stops the tool from *asking* for a private address; it is
  not a firewall for a process that already runs.
- **No resource isolation.** No cgroups, no `setrlimit`, no memory or CPU
  limits — only wall-clock timeouts.
- **No process isolation.** Commands run as the backend user, in the backend's
  own process tree.
- **No protection from a careless approver.** An approval is a human decision,
  not a proof. Someone who approves a gated call without reading the bound tool
  and arguments has authorised whatever the payload actually says.

`SWARM_SYSTEM_ROOT` is a **work-area** convention for the `system_*` tools, not
a security boundary either — and an admin can rebind it at runtime.

**Practical consequence:** prompt injection from a web page, a document, or a
connector result is a remote-code-execution path on the host once host
execution is enabled and approved. The policy gate puts a human in front of
each execution call; it does not make the command safe. Keep host tools off,
keep the instance off the public internet, or sandbox it at the
container/VM layer instead — which is the deployment's job, not the
application's.

See [SECURITY-REVIEW.md](docs/SECURITY-REVIEW.md) for the full audit. Its
findings describe the tree before this hardening layer, so read them as
history and re-check the code for current behaviour.

## Hardening a public deployment

- Set a strong random `SWARM_SECRET` before connecting any provider. There is
  no working default: sealing is refused when it is unset, and the value in
  `.env.example` is a placeholder to replace.
- Put TLS in front of port `8000` and restrict `SWARM_ALLOWED_ORIGINS`.
- Give the first admin an 8-character-or-longer password. To bootstrap that
  admin from another machine, set `SWARM_SETUP_TOKEN` and present it on
  `POST /api/register`; loopback bootstrap without forwarding headers is the
  alternative.
- Set `SWARM_INVITE_CODE` only if remote people should be able to join; leave
  it unset to keep remote registration closed.
- Keep `SWARM_SYSTEM=0`. If you enable host execution, read the bound tool and
  arguments on every approval request, and lower `SWARM_APPROVAL_TTL_SECONDS`
  (30–86400; default 900) so a grant cannot sit usable for long.
- Keep the database and `SWARM_SECRET` in separate backups, and rotate
  provider keys if either leaks.
