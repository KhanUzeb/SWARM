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

- Provider keys connected through the UI are sealed before SQLite storage
  and are never returned by any API (only key hints and connection metadata).
- Auth tokens are composite `<handle>:<raw>`; only the SHA-256 hash of the
  raw half is stored.
- The optional admin password is stored as PBKDF2-HMAC-SHA256, never plain text.
- The sandbox is a working-directory + timeout boundary, **not** a container
  or network-isolation boundary. Do not treat it as one.
- Host-system tools (`system_run`) execute on the machine running the
  backend, bound to `SWARM_SYSTEM_ROOT`. Do not expose an untrusted Swarm
  instance to the public internet without authentication, TLS, and
  `SWARM_SYSTEM=0` unless you understand the tradeoff.

## Threat model

### What the sandbox is

The sandbox is a **working directory plus a timeout**. That is the entire
boundary, and it is worth being exact about it:

- **Directory.** Shell tools are launched with `cwd=SANDBOX_DIR`
  (`backend/agent.py:203-220`, `backend/tools/computer.py:36-46`). This is a
  starting directory, not a jail — `shell=True` means a command may address
  any absolute path the backend user can reach.
- **Timeout.** `SHELL_TIMEOUT = 30` (`tools/computer.py:11`) and
  `SHELL_TIMEOUT_SECONDS = 10` (`agent.py:38`) bound runtime, nothing else.
- **Reduced environment.** `_shell_env` (`agent.py:186-200`) rebuilds a minimal
  `PATH`. Its own docstring says it plainly: *"Not a real isolation boundary —
  just keep the sandbox small."*

### What it protects against

- Accidental writes landing outside the intended workspace for ordinary,
  well-behaved commands.
- Runaway commands, via the timeout and the output caps
  (`OUTPUT_CAP`, `READ_CAP`, `WRITE_CAP`).
- Directory traversal through the **file tools**: `_safe_workspace_path`
  (`agent.py:256-267`) and `safe_path` (`tools/system.py:115-128`) both resolve
  and then enforce `relative_to(base)`, so `../../../../etc/hosts` and
  absolute paths outside the root are refused. Verified, not assumed.
- Destructive shell commands, via the narrow `_DANGEROUS` regex
  (`tools/computer.py:13-16`) — which is trivially bypassable and should not be
  relied upon.

### What it does NOT protect against

This is the important half. A grep across `backend/` for `chroot`, `seccomp`,
`namespace`, `unshare`, `nsjail`, `firejail`, `setrlimit`, and `container`
returns **no isolation mechanism of any kind**.

- **No filesystem isolation.** A bot can read or write any path the backend
  user can reach, including `~/.ssh`, `~/.aws`, and the repo itself.
- **No network isolation.** Bots inherit the host's full network. Outbound
  calls to arbitrary hosts are unrestricted; there is no egress filter.
- **No resource isolation.** No cgroups, no `setrlimit`, no memory or CPU
  limits — only wall-clock timeouts.
- **No process isolation.** Commands run as the backend user, in the backend's
  own process tree.
- **No secret isolation.** `system_read(".env")` returns the provider keys,
  because `_PROTECTED_NAMES` (`tools/system.py:25`) is only enforced on the
  write path (`system.py:131-140`), not on reads.

`SWARM_SYSTEM_ROOT` is a **work-area** convention for the `system_*` tools, not
a security boundary either — and the root can be rebound at runtime by any
authenticated user.

**Practical consequence:** prompt injection from a web page, a document, or a
connector result is a remote-code-execution path on the host. Treat agent input
as untrusted and keep the instance off the public internet, or sandbox it at the
container/VM layer instead — which is the deployment's job, not the
application's.

See [SECURITY-REVIEW.md](docs/SECURITY-REVIEW.md) for the full audit.

## Hardening a public deployment

- Set a strong `SWARM_SECRET` (provider-key encryption depends on it).
- Put TLS in front of port `8000` and restrict `SWARM_ALLOWED_ORIGINS`.
- Set an admin password at workspace creation.
- Rotate provider keys if `SWARM_SECRET` ever changes or leaks.
