from django.urls import path

from . import views

app_name = "deadlines"
urlpatterns = [
    path("<int:pk>/tasks/add/", views.task_add, name="task_add"),
    path("<int:pk>/tasks/<int:task_id>/toggle/", views.task_toggle, name="task_toggle"),
    path("<int:pk>/tasks/<int:task_id>/delete/", views.task_delete, name="task_delete"),
    path("<int:pk>/suspects/<int:party_id>/detentions/add/", views.detention_add, name="detention_add"),
    path("<int:pk>/detentions/<int:det_id>/delete/", views.detention_delete, name="detention_delete"),
]
