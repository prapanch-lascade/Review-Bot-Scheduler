import sys

from providers.appstore import run_appstore
from providers.playstore import run_playstore


def main():

    if len(sys.argv) < 2:
        raise Exception("Provider missing")

    provider = sys.argv[1]

    if provider == "appstore":
        run_appstore()
        return

    if provider == "playstore":
        run_playstore()
        return

    raise Exception(
        f"Unknown provider : {provider}"
    )


if __name__ == "__main__":
    main()
