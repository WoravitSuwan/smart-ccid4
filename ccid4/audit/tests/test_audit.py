import pytest
from django.db import connection, transaction
from django.db.utils import DatabaseError

from audit.models import AuditLog
from audit.services import log, verify_chain


@pytest.mark.django_db
def test_chain_verifies_and_links(investigator):
    a = log(investigator, "view", "case", 1)
    b = log(investigator, "export", "case", 1, "ส่งออก")
    assert b.prev_hash == a.hash
    assert verify_chain() == (True, None)


@pytest.mark.django_db
def test_database_refuses_update_and_delete(investigator):
    e = log(investigator, "view", "case", 1)
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditLog.objects.filter(pk=e.pk).update(detail="แก้")
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditLog.objects.filter(pk=e.pk).delete()


@pytest.mark.django_db
def test_tampering_is_detected(investigator):
    e = log(investigator, "view", "case", 1)
    log(investigator, "view", "case", 2)
    with connection.cursor() as cur:  # simulate someone bypassing the trigger as DB owner
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cur.execute("ALTER TABLE audit_auditlog DISABLE TRIGGER audit_log_no_update")
        cur.execute("UPDATE audit_auditlog SET detail='x' WHERE id=%s", [e.pk])
        cur.execute("ALTER TABLE audit_auditlog ENABLE TRIGGER audit_log_no_update")
    assert verify_chain() == (False, e.pk)


@pytest.mark.django_db
def test_audit_page_requires_permission(client_inv, client, make_user):
    assert client_inv.get("/audit/").status_code == 403
    sup = make_user(role="supervisor")
    client.force_login(sup)
    assert client.get("/audit/").status_code == 200
