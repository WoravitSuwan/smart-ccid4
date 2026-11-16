from django import forms

from accounts.forms import StyledMixin
from core.forms import MinuteDateTimeField, TrimmedCharField

from .models import Case


class CaseCreateForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Case
        fields = ["case_type", "title", "receipt_no", "case_no", "offense", "reported_at", "circumstances"]
        widgets = {
            "reported_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "circumstances": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {"title": "เรื่องโดยย่อ"}


DT = {"type": "datetime-local"}
DT_FIELDS = ["dr_complaint_at", "dr_caseno_at", "incident_from", "incident_to", "reported_at"]


class CaseDetailForm(StyledMixin, forms.ModelForm):
    """แท็บข้อมูลคดี (A4.2) — กรอกเอง; AI เสนอค่าในเฟส 5."""

    GROUPS = {
        "ข้อหาและเขตอำนาจ": [
            "offense",
            "law_sections",
            "penalty_text",
            "max_penalty_months",
            "max_fine",
            "court_type",
            "jurisdiction",
        ],
        "บันทึกประจำวัน รับคำร้องทุกข์": ["dr_complaint_item", "dr_complaint_no", "dr_complaint_at"],
        "บันทึกประจำวัน ตัดเลขคดีอาญา": ["case_no", "dr_caseno_item", "dr_caseno_at"],
        "เหตุการณ์": ["incident_from", "incident_to", "location", "location_lat", "location_lng", "reported_at", "damage_amount"],
    }

    class Meta:
        model = Case
        fields = [
            "case_type", "status", "receipt_no", "title",
            "offense", "law_sections", "penalty_text", "max_penalty_months", "max_fine", "court_type", "jurisdiction",
            "dr_complaint_item", "dr_complaint_no", "dr_complaint_at", "case_no", "dr_caseno_item", "dr_caseno_at",
            "incident_from", "incident_to", "location", "location_lat", "location_lng", "reported_at", "damage_amount",
            "circumstances",
        ]  # fmt: skip
        widgets = {f: forms.DateTimeInput(attrs=DT, format="%Y-%m-%dT%H:%M") for f in DT_FIELDS} | {
            "circumstances": forms.Textarea(attrs={"rows": 8, "class": "input doc-text"})
        }
        labels = {"title": "เรื่องโดยย่อ"}
        field_classes = {f: MinuteDateTimeField for f in DT_FIELDS} | {
            "circumstances": TrimmedCharField,
            "title": TrimmedCharField,
        }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            f.show_hidden_initial = False  # compare with the stored value, not a hidden copy (reported_at has a callable default)

    def groups(self):
        return [(name, [self[f] for f in names]) for name, names in self.GROUPS.items()]

    @property
    def head_fields(self):
        return [self[f] for f in ["case_type", "status", "receipt_no", "title"]]

    def clean(self):
        data = super().clean()
        a, b = data.get("incident_from"), data.get("incident_to")
        if a and b and b < a:
            self.add_error("incident_to", "เวลาสิ้นสุดต้องไม่ก่อนเวลาเริ่ม")
        return data
