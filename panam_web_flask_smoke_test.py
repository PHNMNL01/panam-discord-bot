import panam_web_app


def main() -> None:
    assert panam_web_app.app is not None

    client = panam_web_app.app.test_client()
    response = client.get("/")

    assert response.status_code == 200, response.status_code
    assert b"Panam Web" in response.data
    print("panam_web_flask_smoke_test ok")


if __name__ == "__main__":
    main()
