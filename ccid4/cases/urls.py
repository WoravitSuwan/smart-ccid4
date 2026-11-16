from django.urls import path

from . import views

app_name = "cases"
urlpatterns = [
    path("", views.case_list, name="list"),
    path("new/", views.case_create, name="create"),
    path("<int:pk>/", views.case_detail, name="detail"),
    path("<int:pk>/delete/", views.case_delete, name="delete"),
]
