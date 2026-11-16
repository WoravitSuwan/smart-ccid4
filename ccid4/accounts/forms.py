from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import User


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="ชื่อผู้ใช้",
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True, "class": "input"}),
    )
    password = forms.CharField(
        label="รหัสผ่าน", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "class": "input"})
    )
    error_messages = {
        "invalid_login": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง",
        "inactive": "บัญชีนี้ถูกปิดใช้งาน",
    }

    def clean(self):
        username = self.cleaned_data.get("username")
        if username:
            u = User.objects.filter(username=username).only("account_expires_at").first()
            if u and u.is_expired:
                raise forms.ValidationError("บัญชีหมดอายุ ติดต่อผู้ดูแลระบบเพื่อต่ออายุ", code="expired")
        return super().clean()


class TokenForm(forms.Form):
    token = forms.CharField(
        label="รหัส 6 หลักจากแอปยืนยันตัวตน",
        min_length=6,
        max_length=6,
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "autofocus": True, "class": "input"}
        ),
    )


from django.forms import inlineformset_factory  # noqa: E402

from .models import ArrestTeamMember, Commander, CourtOrProsecutor, OfficerProfile, Unit  # noqa: E402


class StyledMixin:
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput | forms.ClearableFileInput):
                f.widget.attrs.setdefault("class", "input")


class PersonalForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["rank", "first_name", "last_name", "position", "phone"]
        labels = {"first_name": "ชื่อ", "last_name": "นามสกุล"}


class OfficerProfileForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = OfficerProfile
        fields = ["birth_date", "signature"]
        widgets = {"birth_date": forms.DateInput(attrs={"type": "date"})}


class UnitForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["name", "address", "doc_prefix", "division", "bureau", "phone"]


class _RowForm(StyledMixin, forms.ModelForm):
    pass


CommanderFormSet = inlineformset_factory(
    User, Commander, form=_RowForm, fields=["rank", "name", "position", "is_default"], extra=1, can_delete=True
)
ArrestTeamFormSet = inlineformset_factory(
    User, ArrestTeamMember, form=_RowForm, fields=["rank", "name", "position", "phone"], extra=1, can_delete=True
)
CourtFormSet = inlineformset_factory(
    User, CourtOrProsecutor, form=_RowForm, fields=["kind", "name", "court_type"], extra=1, can_delete=True
)
