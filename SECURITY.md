# Security policy

## Reporting a problem
Please report security problems **privately**, not in a public issue. On GitHub, open this repository's **Security** tab and choose
**Report a vulnerability**. Say what you found, how to reproduce it, and which version (the server shows it on the admin Updates page).

You will get a reply when the maintainer can read it. This is a one-person, spare-time project, so there is no fixed response time.

## What counts
- Getting past sign-in, permissions or CSRF protection.
- Reading or changing another person's data, or any account data without a session.
- Running code on the server, or writing outside `reports/` and the music folder.
- Getting the server to fetch an address it should not (the collector's network guard).
- Installing an update that is not signed with the release key.
- Secrets in the repository or its history.

## Supported versions
Only the latest release receives fixes.

## Running the server
See [docs/hardening.md](docs/hardening.md) and [docs/security.md](docs/security.md).
