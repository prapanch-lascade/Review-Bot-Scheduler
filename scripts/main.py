import logging
import os
import sys

from providers.appstore import run_appstore
from providers.playstore import run_playstore


LOG = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if len(sys.argv) < 2:
        raise Exception("Provider missing")

    provider = sys.argv[1]
    app_slug = os.environ.get("APP_SLUG", "").strip()
    LOG.info(
        "Review sync starting: app=%s provider=%s",
        app_slug or "(APP_SLUG unset -> legacy single-app state names)",
        provider,
    )

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
