from django.urls import path

from . import views

app_name = "intake"
urlpatterns = [
    path("", views.index, name="index"),
    path("qr/<str:kind>.png", views.qr_png, name="qr_png"),
    path("poster/<str:kind>/", views.poster, name="poster"),
    path("regenerate/", views.regenerate, name="regenerate"),
    path("staff/<str:kind>/", views.staff_form, name="staff_form"),
    path("submissions/<int:sub_id>/retry/", views.retry, name="retry"),
    path("files/<int:file_id>/", views.intake_file, name="file"),
    path("import/", views.import_upload, name="import_upload"),
    path("import/<int:pk>/", views.import_review, name="import_review"),
    path("import/<int:pk>/file/", views.import_file, name="import_file"),
]
