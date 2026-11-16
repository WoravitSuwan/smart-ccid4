import factory

from accounts.models import User


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user{n}")
    first_name = "ทดสอบ"
    last_name = factory.Sequence(lambda n: f"ผู้ใช้{n}")
    role = "investigator"
    password = factory.PostGenerationMethodCall("set_password", "pass-word-123")

    @factory.post_generation
    def _save(obj, create, extracted, **kw):
        if create:
            obj.save()
