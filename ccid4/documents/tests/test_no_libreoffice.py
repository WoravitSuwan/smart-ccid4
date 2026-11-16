import pytest
from django.urls import reverse

from documents.tests.test_documents import case, full_profile  # noqa: F401 - fixtures


@pytest.mark.django_db
def test_pdf_request_without_libreoffice_still_gives_word(client_inv, case, full_profile, settings):  # noqa: F811
    settings.SOFFICE_BIN = "soffice-not-installed"
    html = client_inv.get(reverse("documents:page", args=[case.pk])).content.decode()
    assert "ออกได้เฉพาะ Word" in html
    client_inv.post(reverse("documents:issue", args=[case.pk]), {"doc": ["daily_record"], "format": "pdf"})
    job = case.document_jobs.get()
    d = job.documents.get()
    assert job.status == "done" and d.docx and not d.pdf and "LibreOffice" in job.error
