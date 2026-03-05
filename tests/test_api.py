"""Tests for FastAPI endpoints — upload, status, download, glossary, history."""

import uuid
from pathlib import Path

import httpx

from app.models import TranslationHistory, TranslationJob
from app.models.job import JobStatus


async def test_upload_returns_job_id(
    client: httpx.AsyncClient,
    test_user,
    txt_file,
) -> None:
    """POST multipart with a TXT file yields 202 and job_id UUID in response."""
    with open(txt_file, "rb") as f:
        response = await client.post(
            "/api/translations/upload",
            data={
                "target_lang": "ru",
                "user_id": str(test_user.id),
            },
            files={"file": ("sample.txt", f, "text/plain")},
        )
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    uuid.UUID(data["job_id"])
    assert data["status"] == "pending"


async def test_upload_creates_user_if_not_exists(
    client: httpx.AsyncClient,
    txt_file,
) -> None:
    """Upload with new user_id creates anonymous user and returns 202."""
    new_user_id = uuid.uuid4()
    with open(txt_file, "rb") as f:
        response = await client.post(
            "/api/translations/upload",
            data={
                "target_lang": "ru",
                "user_id": str(new_user_id),
            },
            files={"file": ("sample.txt", f, "text/plain")},
        )
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "pending"


async def test_upload_invalid_extension_returns_400(
    client: httpx.AsyncClient,
    test_user,
    tmp_path,
) -> None:
    """POST with .docx file yields 400."""
    docx_path = tmp_path / "doc.docx"
    docx_path.write_bytes(b"fake docx content")
    with open(docx_path, "rb") as f:
        response = await client.post(
            "/api/translations/upload",
            data={
                "target_lang": "ru",
                "user_id": str(test_user.id),
            },
            files={"file": ("doc.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
    assert response.status_code == 400


async def test_upload_oversized_returns_400(
    client: httpx.AsyncClient,
    test_user,
    tmp_path,
) -> None:
    """File content > max_file_size_mb yields 400."""
    from app.config import get_settings

    settings = get_settings()
    oversized = tmp_path / "big.txt"
    oversized.write_bytes(b"x" * (settings.max_file_size_mb * 1024 * 1024 + 1))
    with open(oversized, "rb") as f:
        response = await client.post(
            "/api/translations/upload",
            data={
                "target_lang": "ru",
                "user_id": str(test_user.id),
            },
            files={"file": ("big.txt", f, "text/plain")},
        )
    assert response.status_code == 400


async def test_status_pending_job(
    client: httpx.AsyncClient,
    test_user,
    txt_file,
) -> None:
    """Newly created job has status == 'pending'."""
    with open(txt_file, "rb") as f:
        upload_resp = await client.post(
            "/api/translations/upload",
            data={
                "target_lang": "ru",
                "user_id": str(test_user.id),
            },
            files={"file": ("sample.txt", f, "text/plain")},
        )
    job_id = upload_resp.json()["job_id"]

    status_resp = await client.get(f"/api/translations/{job_id}/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "pending"


async def test_download_pending_job_returns_404(
    client: httpx.AsyncClient,
    test_user,
    txt_file,
) -> None:
    """GET download on a pending job yields 404."""
    with open(txt_file, "rb") as f:
        upload_resp = await client.post(
            "/api/translations/upload",
            data={
                "target_lang": "ru",
                "user_id": str(test_user.id),
            },
            files={"file": ("sample.txt", f, "text/plain")},
        )
    job_id = upload_resp.json()["job_id"]

    download_resp = await client.get(f"/api/translations/{job_id}/download")
    assert download_resp.status_code == 404


async def test_cancel_pending_job(
    client: httpx.AsyncClient,
    test_user,
    txt_file,
) -> None:
    """DELETE yields {'status': 'cancelled'}."""
    with open(txt_file, "rb") as f:
        upload_resp = await client.post(
            "/api/translations/upload",
            data={
                "target_lang": "ru",
                "user_id": str(test_user.id),
            },
            files={"file": ("sample.txt", f, "text/plain")},
        )
    job_id = upload_resp.json()["job_id"]

    cancel_resp = await client.delete(f"/api/translations/{job_id}")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"


async def test_glossary_create_and_list(
    client: httpx.AsyncClient,
    test_user,
) -> None:
    """POST creates term, GET returns it in list."""
    create_resp = await client.post(
        "/api/glossary",
        json={
            "user_id": str(test_user.id),
            "source_term": "API",
            "target_term": "API",
        },
    )
    assert create_resp.status_code == 201
    entry = create_resp.json()
    assert entry["source_term"] == "API"
    assert entry["target_term"] == "API"

    list_resp = await client.get("/api/glossary", params={"user_id": str(test_user.id)})
    assert list_resp.status_code == 200
    entries = list_resp.json()
    assert len(entries) >= 1
    assert any(e["source_term"] == "API" for e in entries)


async def test_glossary_duplicate_returns_409(
    client: httpx.AsyncClient,
    test_user,
) -> None:
    """Second POST with same (user_id, source_term) yields 409."""
    payload = {
        "user_id": str(test_user.id),
        "source_term": "Duplicate",
        "target_term": "First",
    }
    await client.post("/api/glossary", json=payload)
    second_resp = await client.post(
        "/api/glossary",
        json={"user_id": payload["user_id"], "source_term": payload["source_term"], "target_term": "Second"},
    )
    assert second_resp.status_code == 409


async def test_glossary_update(
    client: httpx.AsyncClient,
    test_user,
) -> None:
    """PUT changes target_term."""
    create_resp = await client.post(
        "/api/glossary",
        json={
            "user_id": str(test_user.id),
            "source_term": "UpdateMe",
            "target_term": "Old",
        },
    )
    entry_id = create_resp.json()["id"]

    update_resp = await client.put(
        f"/api/glossary/{entry_id}",
        json={"source_term": "UpdateMe", "target_term": "New"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["target_term"] == "New"


async def test_glossary_delete(
    client: httpx.AsyncClient,
    test_user,
) -> None:
    """DELETE removes entry."""
    create_resp = await client.post(
        "/api/glossary",
        json={
            "user_id": str(test_user.id),
            "source_term": "DeleteMe",
            "target_term": "X",
        },
    )
    entry_id = create_resp.json()["id"]

    delete_resp = await client.delete(f"/api/glossary/{entry_id}")
    assert delete_resp.status_code == 204

    list_resp = await client.get("/api/glossary", params={"user_id": str(test_user.id)})
    entries = list_resp.json()
    assert not any(e["id"] == entry_id for e in entries)


async def test_history_list_empty(
    client: httpx.AsyncClient,
    test_user,
) -> None:
    """Fresh user yields empty list."""
    resp = await client.get("/api/history", params={"user_id": str(test_user.id)})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_history_delete(
    client: httpx.AsyncClient,
    db_session,
    test_user,
) -> None:
    """Creates TranslationHistory row, DELETE removes it."""
    job = TranslationJob(
        id=uuid.uuid4(),
        user_id=test_user.id,
        status=JobStatus.DONE,
        target_lang="ru",
        input_path="/tmp/in.txt",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    history = TranslationHistory(
        job_id=job.id,
        user_id=test_user.id,
        filename="in.txt",
        source_lang="en",
        target_lang="ru",
    )
    db_session.add(history)
    await db_session.commit()
    await db_session.refresh(history)

    delete_resp = await client.delete(f"/api/history/{history.id}")
    assert delete_resp.status_code == 204

    list_resp = await client.get("/api/history", params={"user_id": str(test_user.id)})
    assert not any(h["id"] == str(history.id) for h in list_resp.json())
