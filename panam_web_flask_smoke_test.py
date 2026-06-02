import panam_web_app


def main() -> None:
    assert panam_web_app.app is not None

    client = panam_web_app.app.test_client()
    health_response = client.get("/health")
    clear_response = client.post("/api/clear")
    response = client.get("/")

    assert health_response.status_code == 200, health_response.status_code
    assert health_response.get_json() == {"status": "ok", "service": "panam-web"}
    assert clear_response.status_code == 200, clear_response.status_code
    assert clear_response.get_json()["status"] == "ok"
    assert response.status_code == 200, response.status_code
    assert b"Panam Web" in response.data
    print("panam_web_flask_smoke_test ok")


if __name__ == "__main__":
    main()
