from django import forms

from accounts.forms import StyledMixin

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
