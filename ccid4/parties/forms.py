from django import forms
from django.forms import formset_factory, modelformset_factory

from accounts.forms import StyledMixin
from core import thai
from core.forms import MinuteDateTimeField, TrimmedCharField

from .models import PropertyItem

PERSON_FIELDS = [
    "title",
    "first_name",
    "last_name",
    "citizen_id",
    "passport_no",
    "birth_date",
    "gender",
    "nationality",
    "religion",
    "occupation",
    "education",
    "phone",
    "house_no",
    "moo",
    "building",
    "soi",
    "road",
    "tambon",
    "amphoe",
    "province",
    "postcode",
    "contact_name",
    "contact_relation",
    "contact_phone",
]
SUSPECT_FIELDS = ["marks", "arrested_at", "arrived_at", "custody_place", "bail_status"]


class PartyForm(StyledMixin, forms.Form):
    """One person in a case (accuser / suspect / witness) with their statement. Citizen ID stays masked unless revealed."""

    party_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    title = forms.CharField(label="คำนำหน้า", required=False, max_length=40)
    first_name = forms.CharField(label="ชื่อ", required=False, max_length=120)
    last_name = forms.CharField(label="นามสกุล", required=False, max_length=120)
    citizen_id = forms.CharField(label="เลขประจำตัวประชาชน", required=False, max_length=20)
    passport_no = forms.CharField(label="เลขหนังสือเดินทาง", required=False, max_length=40)
    birth_date = forms.DateField(label="วันเกิด", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    gender = forms.CharField(label="เพศ", required=False, max_length=20)
    nationality = forms.CharField(label="สัญชาติ", required=False, max_length=60)
    religion = forms.CharField(label="ศาสนา", required=False, max_length=60)
    occupation = forms.CharField(label="อาชีพ", required=False, max_length=120)
    education = forms.CharField(label="การศึกษา", required=False, max_length=120)
    phone = forms.CharField(label="โทรศัพท์", required=False, max_length=40)
    house_no = forms.CharField(label="บ้านเลขที่", required=False, max_length=40)
    moo = forms.CharField(label="หมู่", required=False, max_length=20)
    building = forms.CharField(label="อาคาร/หมู่บ้าน", required=False, max_length=120)
    soi = forms.CharField(label="ซอย", required=False, max_length=120)
    road = forms.CharField(label="ถนน", required=False, max_length=120)
    tambon = forms.CharField(label="ตำบล/แขวง", required=False, max_length=120)
    amphoe = forms.CharField(label="อำเภอ/เขต", required=False, max_length=120)
    province = forms.CharField(label="จังหวัด", required=False, max_length=120)
    postcode = forms.CharField(label="รหัสไปรษณีย์", required=False, max_length=10)
    contact_name = forms.CharField(label="บุคคลที่ติดต่อได้", required=False, max_length=200)
    contact_relation = forms.CharField(label="ความเกี่ยวข้อง", required=False, max_length=60)
    contact_phone = forms.CharField(label="โทรศัพท์ผู้ติดต่อ", required=False, max_length=40)
    marks = forms.CharField(label="ตำหนิรูปพรรณ", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    arrested_at = MinuteDateTimeField(
        label="วันเวลาถูกจับ/มอบตัว",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    arrived_at = MinuteDateTimeField(
        label="วันเวลาถึงที่ทำการ พงส.",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    custody_place = forms.CharField(label="สถานที่ควบคุม", required=False, max_length=200)
    bail_status = forms.CharField(label="การประกันตัว", required=False, max_length=200)
    statement = TrimmedCharField(
        label="คำให้การ", required=False, widget=forms.Textarea(attrs={"rows": 8, "class": "input doc-text"})
    )

    def __init__(self, *a, real_citizen_ids=None, **kw):
        super().__init__(*a, **kw)
        self.real_citizen_ids = real_citizen_ids or {}

    def clean_citizen_id(self):
        value = thai.to_arabic_digits(self.cleaned_data.get("citizen_id", "")).strip()
        pid = self.cleaned_data.get("party_id") or self.initial.get("party_id")
        real = self.real_citizen_ids.get(pid, "")
        if real and value == thai.mask_citizen_id(real):
            return real  # left masked: keep the stored number
        digits = value.replace("-", "").replace(" ", "")
        if digits and (not digits.isdigit() or len(digits) != 13):
            raise forms.ValidationError("เลขประจำตัวประชาชนต้องเป็นตัวเลข 13 หลัก")
        return digits

    def clean(self):
        data = super().clean()
        filled = any(v not in (None, "") for k, v in data.items() if k not in ("party_id", "DELETE"))
        if filled and not data.get("DELETE") and not data.get("first_name"):
            self.add_error("first_name", "กรอกชื่อ")
        data["is_blank"] = not filled
        return data

    def section(self, names):
        return [self[n] for n in names]

    @property
    def personal_fields(self):
        return self.section(["title", "first_name", "last_name", "citizen_id", "passport_no", "birth_date", "gender", "phone"])

    @property
    def address_fields(self):
        return self.section(["house_no", "moo", "building", "soi", "road", "tambon", "amphoe", "province", "postcode"])

    @property
    def extra_fields(self):
        return self.section(
            ["nationality", "religion", "occupation", "education", "contact_name", "contact_relation", "contact_phone"]
        )

    @property
    def suspect_fields(self):
        return self.section(["arrested_at", "arrived_at", "custody_place", "bail_status", "marks"])

    @property
    def display_name(self):
        v = lambda n: (self[n].value() or "")  # noqa: E731
        name = f"{v('title')}{v('first_name')} {v('last_name')}".strip()
        return name or "ยังไม่ระบุชื่อ"


class BasePartyFormSet(forms.BaseFormSet):
    def __init__(self, *a, real_citizen_ids=None, **kw):
        self.real_citizen_ids = real_citizen_ids or {}
        super().__init__(*a, **kw)

    def get_form_kwargs(self, index):
        return {"real_citizen_ids": self.real_citizen_ids}


PartyFormSet = formset_factory(PartyForm, formset=BasePartyFormSet, extra=0, can_delete=True)


class PropertyForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = PropertyItem
        fields = ["name", "quantity", "value", "status", "note"]


PropertyFormSet = modelformset_factory(PropertyItem, form=PropertyForm, extra=0, can_delete=True)
