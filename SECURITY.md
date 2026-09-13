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

## Hardening a public deployment

- Set a strong `SWARM_SECRET` (provider-key encryption depends on it).
- Put TLS in front of port `8000` and restrict `SWARM_ALLOWED_ORIGINS`.
- Set an admin password at workspace creation.
- Rotate provider keys if `SWARM_SECRET` ever changes or leaks.
