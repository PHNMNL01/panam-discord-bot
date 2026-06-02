import panam_log_reader


def main() -> None:
    logs = panam_log_reader.get_available_logs()
    names = {log["name"] for log in logs}

    assert isinstance(logs, list), logs
    assert names == {"main", "bot_process", "web_process", "dock_process"}, names

    main_log = panam_log_reader.read_log_tail("main")
    assert main_log["name"] == "main", main_log
    assert "content" in main_log, main_log
    assert "exists" in main_log, main_log

    try:
        panam_log_reader.read_log_tail("../README.md")
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown log did not raise ValueError")

    print("panam_log_reader_smoke_test ok")


if __name__ == "__main__":
    main()
