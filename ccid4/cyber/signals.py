"""Keep the cross-case identifier index (B4) current whenever something that holds an identifier changes."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from parties.models import CaseParty, Person

from .models import BankAccount, CryptoTx, CryptoWallet, OnlineAccount


def _reindex(case_ids):
    from cases.models import Case

    from .index import reindex_case

    for case in Case.objects.filter(pk__in=set(case_ids)).select_related("owner"):
        reindex_case(case)


@receiver(post_save, sender=Person)
def person_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _reindex(instance.roles.values_list("case_id", flat=True))


@receiver([post_save, post_delete], sender=CaseParty)
@receiver([post_save, post_delete], sender=BankAccount)
@receiver([post_save, post_delete], sender=OnlineAccount)
@receiver([post_save, post_delete], sender=CryptoWallet)
@receiver([post_save, post_delete], sender=CryptoTx)
def case_item_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        _reindex([instance.case_id])
