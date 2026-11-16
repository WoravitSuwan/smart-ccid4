from django.urls import path

from . import views

app_name = "documents"
urlpatterns = [
    path("<int:pk>/documents/", views.documents_page, name="page"),
    path("<int:pk>/documents/issue/", views.issue, name="issue"),
    path("<int:pk>/documents/jobs/<int:job_id>/", views.job_status, name="job"),
    path("<int:pk>/documents/jobs/<int:job_id>/bundle/", views.download_bundle, name="bundle"),
    path("<int:pk>/documents/<int:doc_id>/<str:fmt>/", views.download, name="download"),
]
