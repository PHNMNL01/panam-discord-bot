import panam_web_app
from io import BytesIO

from openpyxl import Workbook


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

    missing_file_response = client.post("/api/files/process", data={})
    assert missing_file_response.status_code == 400, missing_file_response.status_code
    assert "error" in missing_file_response.get_json()

    unsupported_response = client.post(
        "/api/files/process",
        data={
            "file": (BytesIO(b"zip"), "archive.zip"),
            "instruction": "shrn to",
            "output_format": "auto",
        },
        content_type="multipart/form-data",
    )
    assert unsupported_response.status_code == 400, unsupported_response.status_code
    assert "error" in unsupported_response.get_json()

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["Jmeno", "Oddeleni", "Email"])
    worksheet.append(["Ana", "IT", "ana@example.com"])
    worksheet.append(["Bela", "HR", "bela@example.com"])
    workbook_buffer = BytesIO()
    workbook.save(workbook_buffer)
    workbook.close()
    workbook_buffer.seek(0)

    file_response = client.post(
        "/api/files/process",
        data={
            "file": (workbook_buffer, "people.xlsx"),
            "instruction": "vyber sloupce Oddeleni",
            "output_format": "auto",
        },
        content_type="multipart/form-data",
    )
    assert file_response.status_code == 200, file_response.status_code
    file_data = file_response.get_json()
    assert file_data["kind"] == "file", file_data
    assert file_data["output_filename"] == "people_by_Panam.xlsx", file_data
    assert file_data["download_url"].startswith("/api/files/download/"), file_data

    download_response = client.get(file_data["download_url"])
    assert download_response.status_code == 200, download_response.status_code
    assert download_response.data

    print("panam_web_flask_smoke_test ok")


if __name__ == "__main__":
    main()
