# TOTR documentation

## Start here
- New to the project? Start with [architecture.md](architecture.md).
- Setting it up? [setup.md](setup.md).
- Something broke? [operations.md](operations.md) (it lists the real errors that have happened and their fixes).
- About to change code? [testing.md](testing.md) and the rules at the end of [security.md](security.md).

## Reference
| Document | Covers |
|---|---|
| [setup.md](setup.md) | Install, `.env` settings, database scripts in order, first admin, deploying to your server |
| [architecture.md](architecture.md) | Components, request flow, module map, how the spiders and collector plug in |
| [database.md](database.md) | Music and account tables, the database roles, every SQL file |
| [security.md](security.md) | Threat model, each control and where it lives, known gaps |
| [hardening.md](hardening.md) | Running the server safely: service sandbox, file permissions, database roles, network |
| [api.md](api.md) | Every page and API route with its access level |
| [spiders.md](spiders.md) | Each maintenance check, each fix type, the approval flow, report files |
| [collector.md](collector.md) | Sources, license rules, genres and the wanted list, downloads, undo |
| [frontend.md](frontend.md) | Pages, theme, CSP constraints, `apiFetch`, adding a page |
| [operations.md](operations.md) | Start/stop, logs, backups, account recovery, troubleshooting |
| [updates.md](updates.md) | Signed server updates: how they work, what they protect against, how the developer releases one |
| [testing.md](testing.md) | Every test suite and what it covers, fixtures, adding tests |
| [threat-model.md](threat-model.md) | What the apps and the server defend against, per feature (certificate pinning, join requests, song offers, per-server controls on desktop and Android), and the gaps still open |
| [performance.md](performance.md) | Library-size and load measurements, and what was done about them |

Keep these current: when behaviour changes, update the matching doc in the same change.
