from app import create_app
from app.services.network_watchdog import FailoverWatchdog


app = create_app()


if __name__ == "__main__":
    watchdog = FailoverWatchdog(app.config)
    watchdog.run_forever()
