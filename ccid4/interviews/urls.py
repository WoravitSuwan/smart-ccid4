from django.urls import path

from . import views

app_name = "interviews"
urlpatterns = [
    path("", views.index, name="index"),
    path("settings/", views.settings_page, name="settings"),
    path("quick/", views.quick_start, name="quick_start"),
    path("new/<int:case_pk>/<int:party_pk>/", views.start, name="start"),
    path("<int:pk>/record/", views.record, name="record"),
    path("<int:pk>/chunk/", views.chunk, name="chunk"),
    path("<int:pk>/status/", views.status, name="status"),
    path("<int:pk>/live/", views.live_text, name="live"),
    path("<int:pk>/marker/", views.marker, name="marker"),
    path("<int:pk>/close/", views.close, name="close"),
    path("<int:pk>/import/", views.import_audio, name="import_audio"),
    path("<int:pk>/process/", views.process_page, name="process"),
    path("<int:pk>/reprocess/", views.reprocess, name="reprocess"),
    path("<int:pk>/transcript/", views.import_transcript, name="import_transcript"),
    path("<int:pk>/audio/", views.audio, name="audio"),
    path("<int:pk>/review/", views.review, name="review"),
    path("<int:pk>/segments/<int:seg_id>/", views.edit_segment, name="edit_segment"),
    path("<int:pk>/segments/<int:seg_id>/speaker/", views.segment_speaker, name="segment_speaker"),
    path("<int:pk>/speakers/", views.speakers, name="speakers"),
    path("<int:pk>/narrative/", views.narrative, name="narrative"),
    path("<int:pk>/format/", views.statement_format, name="statement_format"),
    path("<int:pk>/summary.docx", views.summary_download, name="summary"),
    path("<int:pk>/transcript.docx", views.transcript_download, name="transcript_docx"),
    path("<int:pk>/redraft/", views.redraft, name="redraft"),
    path("<int:pk>/pairs/<int:pair_id>/", views.save_pair, name="save_pair"),
    path("<int:pk>/confirm/", views.confirm, name="confirm"),
    path("<int:pk>/documents/<int:doc_id>/<str:kind>/", views.download, name="download"),
]
