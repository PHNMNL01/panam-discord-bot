import os

import panam_test_runner


def main() -> None:
    tests = panam_test_runner.get_available_tests()
    names = {test["name"] for test in tests}

    assert isinstance(tests, list), tests
    assert "router" in names, names

    original_env = {
        key: os.environ.get(key)
        for key in (
            "WERKZEUG_RUN_MAIN",
            "WERKZEUG_SERVER_FD",
        )
    }

    try:
        os.environ["WERKZEUG_RUN_MAIN"] = "true"
        os.environ["WERKZEUG_SERVER_FD"] = "123"
        env = panam_test_runner.build_test_env()
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert "WERKZEUG_RUN_MAIN" not in env, env
    assert "WERKZEUG_SERVER_FD" not in env, env
    assert env["PYTHONUNBUFFERED"] == "1", env
    assert env["PYTHONIOENCODING"] == "utf-8", env

    try:
        panam_test_runner.run_smoke_test("unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown smoke test did not raise ValueError")

    router_test = next(test for test in tests if test["name"] == "router")
    assert "exists" in router_test, router_test

    print("panam_test_runner_smoke_test ok")


if __name__ == "__main__":
    main()
