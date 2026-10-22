"""Terminal CLI entry point: ``python -m interfaces.cli`` (see start_cli.sh)."""

from core.settings.env import load_dotenv

# Some settings read os.environ at import time; load the repository .env first.
load_dotenv()

from interfaces.cli.main import command_loop  # noqa: E402

# multiprocessing's forkserver imports the main module as __mp_main__.
if __name__ == "__main__":
    command_loop()
