from django.urls import path

from . import views

app_name = "evidence"
urlpatterns = [
    path("case/<int:pk>/", views.case_page, name="case"),
    path("case/<int:pk>/upload/", views.upload, name="upload"),
    path("case/<int:pk>/files/<int:fid>/", views.file_panel, name="file"),
    path("case/<int:pk>/files/<int:fid>/view/", views.file_view, name="view"),
    path("case/<int:pk>/files/<int:fid>/download/", views.file_download, name="download"),
    path("case/<int:pk>/files/<int:fid>/verify/", views.file_verify, name="verify"),
    path("case/<int:pk>/files/<int:fid>/withdraw/", views.file_withdraw, name="withdraw"),
    path("case/<int:pk>/exhibits/add/", views.exhibit_add, name="exhibit_add"),
    path("case/<int:pk>/exhibits/<int:xid>/", views.exhibit_update, name="exhibit_update"),
    path("case/<int:pk>/exhibits/<int:xid>/handover/", views.exhibit_handover, name="exhibit_handover"),
]
