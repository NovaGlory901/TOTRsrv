"""Updates from the command line (the same things the admin API does):

python -m updates status     what is installed, what the last check found, what the update service
last said python -m updates check      ask GitHub for the newest signed release python -m updates
install    download and verify it, and file the request (the update service, run by systemd, then
installs it)
"""

import json
import sys

import dotenv

import paths

dotenv.load_dotenv(paths.env_file())
from . import fetch, signing  # noqa: E402  (after .env, which holds UPDATE_REPO)


def main(argv) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    try:
        if cmd == "status":
            ok, why = fetch.configured()
            print(
                json.dumps(
                    {
                        "configured": ok,
                        "problem": why,
                        "fetching": fetch.app_status(),
                        "service": fetch.service_status(),
                    },
                    indent=1,
                )
            )
        elif cmd == "check":
            print(json.dumps(fetch.check(), indent=1))
        elif cmd == "install":
            print(json.dumps(fetch.stage("command line"), indent=1))
            print(
                "Filed. The update service installs it now (watch: journalctl -u totr-update -f)."
            )
        else:
            print(__doc__)
            return 2
    except signing.UpdateError as e:
        print(e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
