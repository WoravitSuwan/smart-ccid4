from django.urls import path

from . import views

app_name = "cyber"
urlpatterns = [
    path("network/", views.network_page, name="network"),
    path("network/data/", views.network_data, name="network_data"),
    path("network/export.xlsx", views.network_export, name="network_export"),
    path("merge/", views.merge_list, name="merge_list"),
    path("merge/<int:pk>/decide/", views.merge_decide, name="merge_decide"),
    path("case/<int:pk>/", views.case_page, name="case"),
    path("case/<int:pk>/flow.json", views.flow_json, name="flow"),
    path("case/<int:pk>/transfers/add/", views.transfer_add, name="transfer_add"),
    path("case/<int:pk>/transfers/<int:tid>/delete/", views.transfer_delete, name="transfer_delete"),
    path("case/<int:pk>/transfers/<int:tid>/evidence/", views.transfer_evidence, name="transfer_evidence"),
    path("case/<int:pk>/slip/", views.slip_read, name="slip"),
    path("case/<int:pk>/import/", views.import_upload, name="import"),
    path("case/<int:pk>/import/map/", views.import_map, name="import_map"),
    path("case/<int:pk>/accounts/<int:aid>/", views.account_update, name="account_update"),
    path("case/<int:pk>/accounts/<int:aid>/reveal/", views.account_reveal, name="account_reveal"),
    path("case/<int:pk>/letters/", views.letters, name="letters"),
    path("case/<int:pk>/online/add/", views.online_add, name="online_add"),
    path("case/<int:pk>/online/<int:oid>/delete/", views.online_delete, name="online_delete"),
    path("case/<int:pk>/crypto/wallet/", views.wallet_add, name="wallet_add"),
    path("case/<int:pk>/crypto/wallet/<int:wid>/delete/", views.wallet_delete, name="wallet_delete"),
    path("case/<int:pk>/crypto/tx/", views.tx_add, name="tx_add"),
    path("case/<int:pk>/crypto/tx/<int:tid>/delete/", views.tx_delete, name="tx_delete"),
    path("case/<int:pk>/crypto/import/", views.tx_import, name="tx_import"),
    path("case/<int:pk>/crypto/export.json", views.crypto_export, name="crypto_export"),
    path("case/<int:pk>/merge/", views.merge_propose, name="merge_propose"),
]
