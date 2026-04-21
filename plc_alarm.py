from app import create_app
from app.services.plc_alarm import PlcAlarmWorker, ensure_plc_alarm_config_file


app = create_app()


if __name__ == "__main__":
    ensure_plc_alarm_config_file(app.config)
    worker = PlcAlarmWorker(app.config)
    worker.run_forever()