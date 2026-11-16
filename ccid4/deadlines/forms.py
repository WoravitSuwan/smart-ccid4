from django import forms

from accounts.forms import StyledMixin


class TaskForm(StyledMixin, forms.Form):
    title = forms.CharField(label="รายการ", max_length=300)
    reason = forms.CharField(label="เหตุผล/คำสั่งที่อ้างอิง", max_length=300, required=False)
    done = forms.BooleanField(label="ทำแล้ว (ลงบันทึกพนักงานสอบสวน)", required=False)


class DetentionForm(StyledMixin, forms.Form):
    start_date = forms.DateField(label="ตั้งแต่วันที่", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    days = forms.IntegerField(label="จำนวนวัน (นับวันแรก)", min_value=1, max_value=12)
    court = forms.CharField(label="ศาล", max_length=200, required=False)
    order_no = forms.CharField(label="เลขคำร้อง/คำสั่ง", max_length=60, required=False)
    note = forms.CharField(label="หมายเหตุ", max_length=300, required=False)
