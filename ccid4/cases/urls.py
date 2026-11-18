from django.urls import path

from . import letter_views, views

app_name = "cases"
urlpatterns = [
    path("", views.case_list, name="list"),
    path("new/", views.case_create, name="create"),
    path("<int:pk>/", views.case_detail, name="detail"),
    path("<int:pk>/delete/", views.case_delete, name="delete"),
    path("<int:pk>/transfer/", views.transfer, name="transfer"),
    path("<int:pk>/card/ocr/", views.card_ocr, name="card_ocr"),
    path("<int:pk>/card/audit/", views.card_audit, name="card_audit"),
    path("<int:pk>/parties/<int:party_id>/reveal/", views.reveal_citizen_id, name="reveal_id"),
    path("<int:pk>/timeline/", views.case_timeline, name="timeline"),
    path("<int:pk>/letters/new/", letter_views.letter_new, name="letter_new"),
    path("<int:pk>/letters/<int:letter_id>/", letter_views.letter_edit, name="letter_edit"),
    path("<int:pk>/letters/<int:letter_id>/reply-file/", letter_views.letter_reply_file, name="letter_reply_file"),
]
