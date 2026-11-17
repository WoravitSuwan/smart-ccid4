from django.urls import path

from . import views

app_name = "summons"
urlpatterns = [
    path("", views.index, name="index"),
    path("import/", views.import_page, name="import"),
    path("import/<int:pk>/", views.import_review, name="import_review"),
    path("import/<int:pk>/issues.xlsx", views.import_issues_xlsx, name="import_issues"),
    path("register/", views.register, name="register"),
    path("register.xlsx", views.register_xlsx, name="register_xlsx"),
    path("register/<int:pk>/", views.register_detail, name="detail"),
    path("register/<int:pk>/reveal/", views.reveal_id, name="reveal_id"),
    path("issue/", views.issue, name="issue"),
    path("issue/preview/", views.issue_preview, name="issue_preview"),
    path("batches/<int:pk>/", views.batch_page, name="batch"),
    path("summons/<int:pk>/result/", views.set_result, name="result"),
    path("files/<str:kind>/<int:pk>/", views.download, name="download"),
    path("settings/", views.settings_page, name="settings"),
    path("templates/<int:pk>/activate/", views.activate_template, name="activate_template"),
]
