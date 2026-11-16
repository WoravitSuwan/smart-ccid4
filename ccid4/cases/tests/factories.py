import factory

from accounts.tests.factories import UserFactory
from cases.models import Case
from parties.models import CaseParty, Person


class CaseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Case

    owner = factory.SubFactory(UserFactory)
    case_type = "online"
    status = "investigating"
    case_no = factory.Sequence(lambda n: f"{n + 1}/2569")
    offense = "ฉ้อโกงประชาชน"
    max_penalty_months = 60


class PersonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Person

    title = "นาย"
    first_name = factory.Sequence(lambda n: f"สมมติ{n}")
    last_name = "ตัวอย่าง"
    citizen_id = "3000000000456"


class PartyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CaseParty

    case = factory.SubFactory(CaseFactory)
    person = factory.SubFactory(PersonFactory)
    role = "accuser"
