"""Form fields shared by all apps."""

from django import forms


class MinuteDateTimeField(forms.DateTimeField):
    """Datetime edited to the minute (datetime-local). Seconds in stored or imported values do not count as a change."""

    def has_changed(self, initial, data):
        if hasattr(initial, "replace"):
            initial = initial.replace(second=0, microsecond=0)
        return super().has_changed(initial, data)


class TrimmedCharField(forms.CharField):
    """Leading/trailing spaces in stored text do not count as a change."""

    def has_changed(self, initial, data):
        return (initial or "").strip() != (self.to_python(data) or "").strip()
