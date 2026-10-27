"""Checks that the files in YOUR repo are the exact versions that were tested together.

Run it from the ROOT of your repo (the folder that contains `streamlit_app/`):

    python check_install.py

It compares every file with the tested version (line endings are ignored) and reports which are old or missing.
If anything is old or missing, the report lists exactly which files to upload into which folder. A failing test is almost always one file
from a set that was not replaced (a new test against an old page, or the reverse); this finds it in seconds.
"""
import hashlib
import os
import sys

EXPECTED = {
    ".github/workflows/auto_send.yml": "7584f63dbabb1ca5f56c9323f206f991289bc564d4be62aa2fc1bfc3fed18047",
    ".github/workflows/label_replies_from_words.yml": "d4acf5d6abdba83599a86a0945bbc4d95ec6c49a2083746ffafcd304dd27860a",
    ".github/workflows/send_batch.yml": "ca7f34e6f23a21030bd13aa1b5e52e59183ef40ca715f86c315fd6f727d654ef",
    ".github/workflows/send_test_email.yml": "767572a564b7d9b13eb72146076605d44d43190502cc38a857a79ec6d0d8ed84",
    ".github/workflows/set_response_status.yml": "895c5c3587bed5a90595fcd75f0cfb3efe68ba4fc3fc97b64c2ceb926441defe",
    ".streamlit/config.toml": "8b79d77aa2549b1d5f4fb329d5d915d1b3608de1d6302c73b0adce76dc3c22fe",
    "apps_script/Code.gs": "363617876e65f8329b933e7d3cfc024cf32e502a0c87a7615457f1f73f97f3be",
    "apps_script/test_unsubscribe.js": "90802b1557d924356eb5cf0dadfb3ef6ba45da9ae7974fdcb3a785380a0de9d9",
    "docs/config.js": "a7a4afbe3cbb92e56dac5b48ef315ddc939b49c779e16ee96e0bb34bd2d5fd70",
    "docs/index.html": "7eeff80630c33138bf1f7da657893966c2e4c75cdcb8760690982e70b2f173ea",
    "docs/unsubscribe.html": "7eeff80630c33138bf1f7da657893966c2e4c75cdcb8760690982e70b2f173ea",
    "docs/unsubscribe.js": "f596d19540d59e00bd75919a3705f17368443db10baf36d703ceb59e8a16e92b",
    "html_campaign_logic.py": "c8993d1f3a2cf66cdb479595c583328d6001fc6b2b12efdf50ea6b865435bef5",
    "outreach.py": "4067cf0c047c10e198fa7a3f7d7b02e2a233c46dfe289c40fbe324d4f8b3606e",
    "response_status.py": "2e7ef196adfab78cf3b277cb062ec9f621cf2fda60f83af8aee65ad94026bd64",
    "streamlit_app/accounts_logic.py": "f7c55364ccb83fbc3170e030fbfc2eb7fc21cb19748cd3e93028625af7db6d17",
    "streamlit_app/app.py": "94b723f762c8ef223f9ab28372b6c2b2e6d25bd9e8d268e70efd618521a2cab7",
    "streamlit_app/auth.py": "257c2b210b27e289e4c4b407384fefb32ad719026d290ea9d9cde58aceacd142",
    "streamlit_app/campaign_analytics_logic.py": "754f020cf146d43a46bf23cca94e93789c14c07fbc8f49e1f3b7e69786722c00",
    "streamlit_app/campaign_builder.py": "d746812e5f0f96a14310340116522b315856b91327a846cff839a427ed7d102e",
    "streamlit_app/campaign_header_logic.py": "1455e246447a187c970fea31889e80e258ce3e6d2bd46b2c39888ceb11094424",
    "streamlit_app/campaign_html.py": "1c186413415c76367ffb79a5dd87a61ea87b9c1d5842c7c49855dc119cc11b7b",
    "streamlit_app/campaign_status_logic.py": "f0fded3b7fb810b0e1de4f682cb85953f8140c01a8a1fcd3f85dc514ef03f65c",
    "streamlit_app/campaigns_hub_html.py": "8506869fb3b9c96fdc7b86b06479aff2ca43fb3fdb76f8a310a0178f324fc420",
    "streamlit_app/campaigns_hub_logic.py": "bfa7447029ad8056573c5a365bfd88e463c8a569425d18c420f9fdb27187dd93",
    "streamlit_app/config.py": "91fc236d9ecbcf6f0c9bc2c20bf518c2a2e87f4b245c8aeb86a5b7d9b6d95f29",
    "streamlit_app/conversation_logic.py": "8a349f4725006ae5e5eedba4593d8de261c377f88de07abac845f7d342f4c073",
    "streamlit_app/data_import_logic.py": "a34cca3b8130dc2151859ae8e5acb104bcfb026413b2210121389879de4f3e7c",
    "streamlit_app/email_account_slots_logic.py": "4f2fbd2e56a8c538fbdfa066dc4dc9ddb1fd84b87ef5a832b4c3bd6a96e65164",
    "streamlit_app/email_accounts_html.py": "706a5ae5590750e1681b46b7778bef53ea63e0d9086b85ab9c86917b46ecdb99",
    "streamlit_app/email_accounts_view_logic.py": "baa32d5f8c535bc29dc48aa2e0e59922eb36712883707fafefbbf9ef9f863d6b",
    "streamlit_app/github_client.py": "b6dc1d923164f12c64041e0df2ea6da687ccc4e3962f84c3479cab8b19380ec6",
    "streamlit_app/html_editor_html.py": "3ab8bf7bf409aad76246fd3ff0dd71f6517e22e84503ffa7a44a5b68fefd466c",
    "streamlit_app/html_editor_logic.py": "5b3a8635d66a8c0e5de858bc9f5743b171ae3c93063a7e977585a855827deab1",
    "streamlit_app/html_editor_view.py": "a76b01d231ee31b916c15cb9191637231c5a38a219da625ac1a97012b9a95e1e",
    "streamlit_app/html_sequences_logic.py": "bab8ef48b720b454d2ab08e4febfd818220711a5df79b663948eca72edbc0c19",
    "streamlit_app/html_structure_view.py": "c2d90d6383d4053b6e78e5ab60baa690bdf0b4c5193300696c2acabcc2a5b826",
    "streamlit_app/inbox_html.py": "a9e8cc064c2ee893812deecc99ee887ae640f824bc6dbe733e1d7caa4ea5021d",
    "streamlit_app/inbox_keywords.py": "c4d9978131d1ab1ef5282dff1ad62a36db9531d500e4ae5fcf348a969faf19ad",
    "streamlit_app/inbox_logic.py": "deb3882a9e697f0c198a1c7c0753e274635a854587b85e43ae21f29fb4ffd5d1",
    "streamlit_app/inbox_senders.py": "52dfe223ce54ce53f591156deab6a666c708805d5be2c57331acd14ddeecbce9",
    "streamlit_app/inbox_view.py": "b36aca1fea1be5eb1a0d928d308f5c5f4898ebc0b3964f7d8bc5881a2cb8c999",
    "streamlit_app/launch_logic.py": "6402fea1b63a9c1d81f8274df3270da5c373e8b4a8fc360064642602c6f649e1",
    "streamlit_app/leads_actions.py": "32f4a9151144f63fb2c0c348f9eaabaa7bb0ef8c382fa72cd7a37d4522c5f404",
    "streamlit_app/leads_html.py": "01e455da4c99b7bd48550082df9665cdefd9bf8ec0a2237cf6d75aa3175b4c78",
    "streamlit_app/leads_logic.py": "fc49f165b5429f4a68736646c083e64bd0545912966583392dbdb0ed993d01b2",
    "streamlit_app/leads_view.py": "a05ea42b51578175fe34c3485318f711c1c65ee2952cab166a7b5e95908a6bf4",
    "streamlit_app/page_state.py": "11a24a73225667f238a37c0b534438cab74154aef2f9f6abc88a25efbc601f41",
    "streamlit_app/pages/campaigns.py": "9d76adb3d2b13840ea18745bd09aa2dd9a573746f3648eb9073882b093c2ec9c",
    "streamlit_app/pages/controls.py": "4f34cefd534b5339f252ee3ad63d2fab4fa4ba0a7c04e9ad6a907066de0ae56e",
    "streamlit_app/pages/email_accounts.py": "6c53967aec5e7427a7a1cc77a8b6218d015b41cacb89b172b2d7b67f8e790e29",
    "streamlit_app/pages/new_campaign.py": "51a78cc525c889add4087a78f26610dedba0b11874bf43ed904647c1a59a3311",
    "streamlit_app/pages/performance.py": "51e6acf388ae28b65e4142428174b15a28d6a068ee46a185ea5575320c018778",
    "streamlit_app/pages/responses.py": "3024c4cd1310d52d7753c6c81d4e2085637318d590f29c49a9d25b6abc4520c3",
    "streamlit_app/parallel_logic.py": "ed057a1f7dec53401b758d96097ad38c0426371d41d2d9acc6d275c72df72f5d",
    "streamlit_app/performance_figures.py": "6fe5eb8ca5ad9bdbcc0011eed54190b4f67c5abac3dbb43a0cb6b0b991610030",
    "streamlit_app/performance_html.py": "4955c6a4b7b9d233743256f078ab41617c5e88b627a5cb55fefd884827f5d536",
    "streamlit_app/performance_logic.py": "43adc1600f578545180b25dfd080c411162e70b360a1361b342485ee5d91916a",
    "streamlit_app/performance_view.py": "84fa8d36a124cf254bd020b91a98cac9dafdf2b5fcb2d0478144dbefcedd91f8",
    "streamlit_app/preview_logic.py": "b4c34d19ed8dfda8e26afb97c770189af20dbf320581f7da250e7b7d1223528d",
    "streamlit_app/replies_logic.py": "29ea69e1b6a4b4e354c5ae34069e16dc89d6cbb10d66bb32289304b86629d3eb",
    "streamlit_app/responses_hub_logic.py": "fe4e0091ca5ffc54ddb426312a96601f0ff7b66110958ec93efe1fcdbae5a187",
    "streamlit_app/responses_page_html.py": "08e0a807d4bee469b0cf3b7af38223b279c5d8f38b9c4455033f6ebed4c4ec8a",
    "streamlit_app/responses_reply_logic.py": "8b26e6140dac41b71726dc910f3c2a1d194d5a095da310d5905b8ea341d73e6f",
    "streamlit_app/schedule_html.py": "6553914d6879833d19348cf8fd5cac9380b1840e04c73e31ceb496c508ae2bc4",
    "streamlit_app/schedule_logic.py": "f33e853e3264debfebcdfe6b8fefdac395a6211f536749572d5daf7eb7552d74",
    "streamlit_app/schedule_view.py": "4980c6c31cf42e4ee1dc9e5404fbca05481b490428352ec0b9ba9d39153966ae",
    "streamlit_app/schedule_view_logic.py": "90685df02d7def838ea74cdc7ac4e8bd54f5b5beff36148954b4bba59e00ce18",
    "streamlit_app/send_logic.py": "c9db86a73fbedbe7779e0fa42b59cd094fc684d33c2537c90d84b39ed137fda1",
    "streamlit_app/sequences_html.py": "56b9186e7b4d4770ef55d4b7ffa4cb0f4e67e8614a2e1f08347f73f3e7b00181",
    "streamlit_app/sequences_logic.py": "f4ee777b37bca74bc2b9df198af733fae971794875aa88761f3636ac3e26698a",
    "streamlit_app/sequences_view.py": "775600c43de66c735042d943890b0ecce2af243c1843047a99ae6f6df4262319",
    "streamlit_app/sequences_view_logic.py": "2f4efd31e6997aa226c9606e970e07fda2118b6e5d1426d3bf6a787439864e56",
    "streamlit_app/settings_html.py": "08fb74186e141c645334d3b1ef75e11902f9469e85daffe372e1edd38d95f6a9",
    "streamlit_app/settings_logic.py": "3bb28311300e7815d948d6d3a05c568249af6636cf03cbdbebc145dda8ef79c9",
    "streamlit_app/settings_view.py": "6cc2607e2015a5b18aecd50c2113250e9793bd7231b5fde71290fb6f89e761fd",
    "streamlit_app/settings_view_logic.py": "759735b507968868d09e5620de9b428020667315fc9abe4ff9b7331905fbe38a",
    "streamlit_app/sheets_readonly.py": "64e9cd579b6e0c69f49d664ebaabe0260c63dbc7227314b73a1bd62f1b2894df",
    "streamlit_app/shell_data.py": "37c0d7413ce5d40cfafbe1cafac9dd6fd119f6e61baaccf0ccacf6fc68fd9620",
    "streamlit_app/shell_html.py": "7603ff440dde85cf0db46d27dbf632091e31011523a383f1add3f198edcae912",
    "streamlit_app/shell_logic.py": "34c9b6dfe1083e967877bf5580005895b65d4fafe9b5f46fa636db2e2aa82bca",
    "streamlit_app/shell_view.py": "d97d22f2f262e6142fd0319ea02f6aacbd4eac55b236920ddd7c1fc926d8c271",
    "streamlit_app/tests/conftest.py": "7f71171c94a87ed0e214c45d9a2250827afb427223ddd844da0da58cee1837d0",
    "streamlit_app/tests/test_accounts_logic.py": "60c18376564cf915df54b0c9f49dbeed2195f17e266276faafec2be3052cd4e3",
    "streamlit_app/tests/test_auth.py": "3b58668ea6f673adcb9bda4c0cf6f2926d18bdfa0e0329adcbba12c75f98e42a",
    "streamlit_app/tests/test_campaign_analytics_logic.py": "e13b15a0da888122e4c19df54e286052861bded7a1f81b36ac36611f30df4904",
    "streamlit_app/tests/test_campaign_builder.py": "a972dbe4eb0a94c3c11f05f749e38ec09f2b34add40cb406c9b30ca3acbd6c23",
    "streamlit_app/tests/test_campaign_header_logic.py": "fd1459e223c3585b6b5ab2805826df6536cf974c203bc5be6de2e89397471edd",
    "streamlit_app/tests/test_campaign_header_page.py": "e15a737b6a0f23dad9ffad1b28f7f4370b1842d986e5bd4b9b1afb9945d0154c",
    "streamlit_app/tests/test_campaign_html.py": "7875a2514b1dfb93cbd1846f7d3f7aa116bcb0683050b1115d2e2df1f500160a",
    "streamlit_app/tests/test_campaign_status_logic.py": "c5c4dad9987bff0cde169c4e8343b2fa0edef39042709226ce05021c58bc39c2",
    "streamlit_app/tests/test_campaigns_hub_html.py": "1bab5cae6175fe3f4ab2de3bef1258a290e3013f6e6f23604311d6baa4b4f3cb",
    "streamlit_app/tests/test_campaigns_hub_logic.py": "dc2db697def4b88675947dcf17b3e0f7ce2c1a638cb7fbe82e29c3a67aa4b64f",
    "streamlit_app/tests/test_campaigns_hub_page.py": "a6b55f8faff873f43419d496c66a74bddab3c93e6c796392d6fbc7230dd8484f",
    "streamlit_app/tests/test_completed_logic.py": "7cf4f3db1b5dbd0c9e8868d2606d3e63f60256b52d67b84f4031cc26a8942412",
    "streamlit_app/tests/test_conversation_logic.py": "6ffe59d5398e782aa0b158a511ecb8344bd77cc625b437d52f311980e593e04a",
    "streamlit_app/tests/test_data_import_logic.py": "7298dd1e56510e70617031123f5f2cde1ef74c0fada5c1b02f3fdc2c3136e2a2",
    "streamlit_app/tests/test_email_account_slots_logic.py": "5d0b8f49f9508a77fffa0fe81aae82046ab039a5b0025ae0d9b1428ca750e8d6",
    "streamlit_app/tests/test_email_accounts_html.py": "64998da909b25bfefc8536cc4e536e0410739b5270a4ab502c5fcb1834013639",
    "streamlit_app/tests/test_email_accounts_page.py": "efd4e3115776454e5edf65547fa43765b6e10557d8d808ffe178794810372d7e",
    "streamlit_app/tests/test_email_accounts_view_logic.py": "35a1fafdb71e961767a5f8707e78bec2d15c27dc2cc9e469552f03b886b5e84f",
    "streamlit_app/tests/test_github_client.py": "6353049e0e6d51c476bad7cc824ba473c80d35c15eb65ec2e0e91c75d5221cf5",
    "streamlit_app/tests/test_github_read_cache_pages.py": "c6c2f9da098e5b3afaf3e95cdfcd081ed053ba70c263b69b27c2e16d3ccd2002",
    "streamlit_app/tests/test_html_editor_logic.py": "5aee2f1552a3397b2ade16a52a985ba26806616572a7f1a69a61a07bde97acf4",
    "streamlit_app/tests/test_html_editor_page.py": "31988b189499ae4e618e0d5f92259401492e0287be944ff01af236ad3d0ec32d",
    "streamlit_app/tests/test_html_launch_page.py": "0dbc5e73f9fb012e0edb83ddf24aac5bc704f24707b4594665418a74a3949f1e",
    "streamlit_app/tests/test_html_new_campaign_page.py": "7c737b590b40d6adf2e39580eb8920af0a8cf57dada5de5ef9ac2daf8003a7c7",
    "streamlit_app/tests/test_html_sequences_logic.py": "acc1f19fd0c45e2d91d68017d0dd966f1ea4e0a3bbe541610520e92d7be3ccf1",
    "streamlit_app/tests/test_html_sequences_page.py": "35e48ab617ef3f220df61cf6f203558f1c3fd28037ad6b3d7d315a5bad67a6bc",
    "streamlit_app/tests/test_html_settings_page.py": "d596461fdbeabca39b6d8705f46a76ab6e0d56c717da0e70947cfb0f14be87e8",
    "streamlit_app/tests/test_inbox_html.py": "36612be7f50fb81e980040a57bf4920b096d80787a1f71cb355832267eadaa68",
    "streamlit_app/tests/test_inbox_keywords.py": "b5051ef7d67f914e99c7aef266a6933bc7ca993ed0b7f324816c4e4730c67cbc",
    "streamlit_app/tests/test_inbox_logic.py": "dfaaf4eb5b863627496bd099b410eea1c2a711ad2f581be87b488e92ba39d2bf",
    "streamlit_app/tests/test_inbox_page.py": "d2427874922718e65338427625f56f17475a6347fc19b24ce46d5a07229ca4b5",
    "streamlit_app/tests/test_inbox_senders.py": "06b0f84a97635e3703be9f521cbcfe72726883e4fcc4de129a1889ed5d06387d",
    "streamlit_app/tests/test_launch_logic.py": "a02affbb7bcb1cdbb3f42a16a128bd0e9fb45d3f21ed4eadd23c72b4b9ecb7bf",
    "streamlit_app/tests/test_leads_html.py": "f4fe3bdb78b882ce83790c93a50d855bad8749751f2b43c4a04b3ddc61439009",
    "streamlit_app/tests/test_leads_logic.py": "a4ac8fb24b814e35aaf9c822f5141194e2943832c83f34780a7696186faba764",
    "streamlit_app/tests/test_leads_page.py": "e02717e6da0d5ae259b178d37b23f5e0b786eb2b9f404ad83294a5341a7d4f0e",
    "streamlit_app/tests/test_live_reads.py": "b8551c3aaed77067bbc6737406d62a70df2a3862d5db9d7148f7fcf4a87ed4e2",
    "streamlit_app/tests/test_live_template_fallback.py": "3543c39f0f837ff8dfe748d9c98da8b8316a680f9e1494f39d3f529d3e4096d3",
    "streamlit_app/tests/test_pacing_page.py": "105778196b394592bf05a3f8c00a580a0432dfbee14fd9a8c190ccd3e9b4f82e",
    "streamlit_app/tests/test_page_state.py": "b7a20ab124efd745994791cf7da0300865a78734c7d076ed584b9acc5edb0ca9",
    "streamlit_app/tests/test_pages_smoke.py": "4e6f940dd6460e876e507f9dd670a39a15404c60402b87129ff590fe7038b83c",
    "streamlit_app/tests/test_parallel_logic.py": "a405200bb0f41b86d6980de9cb9d863510b56c516b9456feb47014835d5d98ab",
    "streamlit_app/tests/test_performance_figures.py": "e2fab5fe9f3f48aeecaf7e3af3daa945333b1d8671370e7b1a8b51490c530e28",
    "streamlit_app/tests/test_performance_html.py": "ed4523d300708f759010c13190b9e43ab643dceee1e86165f5a802921a0ba7b7",
    "streamlit_app/tests/test_performance_logic.py": "81fd1e31d2d57da0a4c6636027d254f607a005635bd2a2301ac48f8a0032f953",
    "streamlit_app/tests/test_performance_page.py": "e1f2fbe8604008a07fa6dd8f4fda71a9ce33756fd1fd7e658a9ab2d9b7d1776a",
    "streamlit_app/tests/test_preview_logic.py": "8d55f7ceb102bd2d635f3307becaf112d6333da5fd2c942cfaf9996717d8527c",
    "streamlit_app/tests/test_replies_logic.py": "37fd3feba359c864eac69a4a260394b8c209e3d0fb9b6f853f9c838120a94c0e",
    "streamlit_app/tests/test_responses_hub_logic.py": "4b08aec8fec8f16e5da999d3c4c2e1766c93336f947ee036536a846814e5081b",
    "streamlit_app/tests/test_responses_page_html.py": "789eb266c79240c5ecd8623909d30615988dadcfb57300878e6a9c9b1163103d",
    "streamlit_app/tests/test_responses_reply_logic.py": "c60429ff87c3847ad2593f13f1eabfbc9e192e4e834a334e2bfe0d0c57f2039d",
    "streamlit_app/tests/test_schedule_html.py": "cf1c1954253175ba832e8ea548f1fb98b77b2a69c8a4d64c040dd541c4df72a8",
    "streamlit_app/tests/test_schedule_logic.py": "09c24045bd9bdddbe9fec4980968c73e2abce28e46946a8d48611bc7c07f4ce3",
    "streamlit_app/tests/test_schedule_page.py": "403600e0fb9fbd5716be3015d1be8467705116558811709b62d439856e96f05f",
    "streamlit_app/tests/test_schedule_view_logic.py": "ca5164df57ddf4a9b965cb453f3869cbdaacdcc5cada607aa7b700ac7714fd14",
    "streamlit_app/tests/test_send_logic.py": "efa7feb134371b123b7f51b709bc277822e894fb4710e30143c6a7371fe2c4aa",
    "streamlit_app/tests/test_sequences_html.py": "5e54b0b543b84a2808533afa83db0b7179ea1e64cfad078edd139e5bea70adab",
    "streamlit_app/tests/test_sequences_logic.py": "b7b45e00286ba3316f64eb17f9cca6d897c85232fc91923cf6e8ae6ea0555458",
    "streamlit_app/tests/test_sequences_page.py": "0f3725db5a56813890aa64312a0c44d94d93aef9bb00a102ed2066a1011cec89",
    "streamlit_app/tests/test_sequences_view_logic.py": "e86e8d5e2bd18510999014dda443bd532c04a3842c79dd9deccab522537dc4a5",
    "streamlit_app/tests/test_settings_html.py": "b5ef03ae38f11fb8b6e84dd2dbdd438dabe84344450ebb664cc98aa3d63a8526",
    "streamlit_app/tests/test_settings_logic.py": "0c9ad8e8cf00da0dc6fa9560fc6a1c8003a1ab1e057478244a929e66f9617c6f",
    "streamlit_app/tests/test_settings_page.py": "8e2de9f890fa75c26f6f21db6c375fbd446049b4c0634fe0492491c553932b79",
    "streamlit_app/tests/test_settings_view_logic.py": "d1a9c11b9c28d80d0699aa90b0663c1eec4cfa7825254613767d8f65cec2ad21",
    "streamlit_app/tests/test_sheet_speed_pages.py": "ce5c735927a511c68dd4d6ef9da3bdade5594949096c9f53065311e12bdc4213",
    "streamlit_app/tests/test_sheets_readonly.py": "860aa13274740b1ad74c80d7b602738ee53f783f84271430811dbb8d52d80f51",
    "streamlit_app/tests/test_shell_html.py": "dad3c038dd9e880d7ce71ad33617769a14d882913049252b669af4de35777758",
    "streamlit_app/tests/test_shell_logic.py": "85bdfee61ff53059f2d2c83dc497139c0c74f921e2de25d527f806289cd7253a",
    "streamlit_app/tests/test_shell_page.py": "b7b5cd4beba49731a0a6cee6f34fdbb11a7df023823878c43ef758c28eaa1aa3",
    "streamlit_app/tests/test_work_queue_logic.py": "0bc4375d9a7bf0050122f5329750787863dfc0ff6934395fe9be333f657be769",
    "streamlit_app/tools/generate_password_hash.py": "fcdd90aacb6cf86aa755f623c9d0da0608e8ddbd55135c3cf36255a2c57f6354",
    "streamlit_app/work_queue_logic.py": "653bbcc5e35003f4ef201033ffbfea48b403709c0b4eb285b84a30a098b645f8",
    "tests/test_apps_script_sync.py": "baf4d6a4cc33b0a6046adf6819bf69b576a2143be152f574f363bf155fed2b42",
    "tests/test_get_campaign_from_listing.py": "4b0ce6bd04fbd25f14a35141e27ba0e68064a56574b56459762343598e0178cd",
    "tests/test_html_campaign_logic.py": "33cf6277e84bc07d5fc0d8b8d5d8e47ce9805849921d9badd88d2f0def768c0c",
    "tests/test_html_campaigns_engine.py": "444df83020246b2e0893e82f8d718e44188828486576fe1146fe4a1043c0540d",
    "tests/test_response_status.py": "131a1e040c34be625a968fbf6485e0d1a4e4b535dbe1513f093e7ce7e4dd4aaf",
    "tests/test_stage_wait_overrides.py": "b784bda4227086834a5dd811387b9c02419ead37f91af743416debf17ac811b4",
    "tests/test_static_page.py": "aa78cff9a97f01e213eebfe011167031d55570f28deb9de630ac352ab04a681d",
    "tests/test_unsubscribe_engine.py": "93cf98a608d75f48f3f4c0c023e55a07a20c632f6868b4902ee28c2164d321af",
    "tests/test_unsubscribe_logic.py": "d9de02c05415d33f685e6ea8cba9ee25192812243f4d75582d924a43f4309cf5",
    "tests/test_unsubscribe_workflows.py": "35f5d4098f42adfed7182526d7cb6bd83598b43cd768769e5494f33128ab416a",
    "unsubscribe_logic.py": "85462c8c31330c4d2150a36578d4c2fb1a0e16e9b4aa22679e2015c36c06ee60",
}


# A file that is allowed to live in more than one place. The theme file works from either folder, so it is not "missing" when it is in the other one.
ALSO_ACCEPTED = {".streamlit/config.toml": ["streamlit_app/.streamlit/config.toml"]}


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read().replace(b"\r\n", b"\n")).hexdigest()


# Files this version no longer has: the Today page became a part of Home. A copy left behind would sit unused in the repo, so the checker asks for it to be deleted.
SHOULD_BE_DELETED = ["streamlit_app/pages/today.py", "streamlit_app/tests/test_today_page.py"]


def is_test_file(path):
    return path.startswith("tests/") or "/tests/" in path


def main():
    if not os.path.isdir("streamlit_app"):
        print("Run this from the root of your repo: the folder that contains the streamlit_app folder.")
        return 2
    stale, missing = [], []
    for path, expected in sorted(EXPECTED.items()):
        places = [path] + ALSO_ACCEPTED.get(path, [])
        found = [place for place in places if os.path.exists(place)]
        if not found:
            missing.append(path)
        elif not any(digest(place) == expected for place in found):
            stale.append(path)
    leftovers = [path for path in SHOULD_BE_DELETED if os.path.exists(path)]
    problems = sorted(stale + missing)
    app_problems = [p for p in problems if not is_test_file(p)]
    test_problems = [p for p in problems if is_test_file(p)]
    app_total = sum(1 for p in EXPECTED if not is_test_file(p))
    test_total = len(EXPECTED) - app_total

    print(f"THE APP:   {app_total - len(app_problems)} of {app_total} app files are correct." + ("  The app itself is fully up to date." if not app_problems else ""))
    print(f"THE TESTS: {test_total - len(test_problems)} of {test_total} test files are correct." + ("" if not test_problems else "  (Tests only check the code. The app runs fine without them, but upload them so they stay in step.)"))

    def show(title, paths):
        if not paths:
            return
        print(f"\n{title}")
        folders = {}
        for path in paths:
            folders.setdefault(os.path.dirname(path) or "(the main folder of the repo)", []).append(path)
        for folder, files in sorted(folders.items()):
            print(f"  Upload these {len(files)} file(s) into the folder  {folder}/  (Add file > Upload files):")
            for path in files:
                print(f"      {os.path.basename(path)}   [{'missing' if path in missing else 'old version, replace it'}]")

    show("APP FILES TO UPLOAD (the app is not complete until these are in):", app_problems)
    show("TEST FILES TO UPLOAD:", test_problems)
    for path in leftovers:
        print(f"\nDELETE   {path}   (no longer part of the app: Today is now a section of Home)")
    print(f"\n{len(EXPECTED) - len(problems)} of {len(EXPECTED)} files are exactly the tested version.")
    if problems:
        print(f"{len(stale)} old, {len(missing)} missing. Upload the files listed above, wait a minute, and run this again.")
        return 1
    if leftovers:
        print(f"{len(leftovers)} file(s) above should be deleted, then run this again.")
        return 1
    print("Everything matches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
