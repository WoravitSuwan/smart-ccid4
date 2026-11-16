from django.urls import path

from . import views

app_name = "dika"
urlpatterns = [
    path("<int:pk>/dika/", views.search, name="search"),
    path("<int:pk>/dika/<int:ruling_id>/pin/", views.pin, name="pin"),
]
