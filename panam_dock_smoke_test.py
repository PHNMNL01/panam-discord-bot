import panam_dock_app
import panam_process_manager


def assert_web_child_env_is_sanitized() -> None:
    original_env = {
        key: panam_process_manager.os.environ.get(key)
        for key in (
            "WERKZEUG_RUN_MAIN",
            "WERKZEUG_SERVER_FD",
            "FLASK_RUN_FROM_CLI",
            "WERKZEUG_DEBUG_PIN",
        )
    }

    try:
        panam_process_manager.os.environ["WERKZEUG_RUN_MAIN"] = "true"
        panam_process_manager.os.environ["WERKZEUG_SERVER_FD"] = "123"
        panam_process_manager.os.environ["FLASK_RUN_FROM_CLI"] = "true"
        panam_process_manager.os.environ["WERKZEUG_DEBUG_PIN"] = "111-222-333"

        env = panam_process_manager.build_child_process_env("web")
    finally:
        for key, value in original_env.items():
            if value is None:
                panam_process_manager.os.environ.pop(key, None)
            else:
                panam_process_manager.os.environ[key] = value

    assert "WERKZEUG_RUN_MAIN" not in env, env
    assert "WERKZEUG_SERVER_FD" not in env, env
    assert "FLASK_RUN_FROM_CLI" not in env, env
    assert "WERKZEUG_DEBUG_PIN" not in env, env
    assert env["PANAM_WEB_DEBUG"] == "0", env
    assert env["PANAM_WEB_USE_RELOADER"] == "0", env
    assert env["PANAM_WEB_PORT"] == "5050", env
    assert env["PANAM_WEB_HOST"] == "127.0.0.1", env
    assert env["PANAM_STARTED_BY_DOCK"] == "1", env
    assert env["PYTHONUNBUFFERED"] == "1", env
    assert env["PYTHONIOENCODING"] == "utf-8", env


def main() -> None:
    statuses = panam_process_manager.get_all_service_statuses()
    service_names = {status.name for status in statuses}

    assert service_names == {"bot", "web"}, service_names
    assert all(status.pid_file for status in statuses)

    try:
        panam_process_manager.get_service_status("unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown service did not raise ValueError")

    assert_web_child_env_is_sanitized()

    assert panam_dock_app.app is not None

    client = panam_dock_app.app.test_client()
    health_response = client.get("/health")
    status_response = client.get("/api/status")

    assert health_response.status_code == 200, health_response.status_code
    assert health_response.get_json() == {"status": "ok", "service": "panam-dock"}
    assert status_response.status_code == 200, status_response.status_code

    status_json = status_response.get_json()
    assert status_json["status"] == "ok", status_json
    api_service_names = {service["name"] for service in status_json["services"]}
    assert api_service_names == {"bot", "web"}, api_service_names
    assert all("state" in service for service in status_json["services"]), status_json
    assert all("status_detail" in service for service in status_json["services"]), status_json

    print("panam_dock_smoke_test ok")


if __name__ == "__main__":
    main()
