"""Checks that the files in YOUR repo are the exact versions that were tested together.

Run it from the ROOT of your repo (the folder that contains `streamlit_app/`):

    python check_install.py

Every line says ok, STALE (the file is an older or different version) or MISSING. Line endings are ignored.
If anything is STALE or MISSING, paste the output back and the right files will be sent. A failing test is almost always one file
from a set that was not replaced (a new test against an old page, or the reverse); this finds it in seconds.
"""
import hashlib
import os
import sys

EXPECTED = {
    ".github/workflows/label_replies_from_words.yml": "d4acf5d6abdba83599a86a0945bbc4d95ec6c49a2083746ffafcd304dd27860a",
    ".github/workflows/set_response_status.yml": "895c5c3587bed5a90595fcd75f0cfb3efe68ba4fc3fc97b64c2ceb926441defe",
    ".streamlit/config.toml": "8b79d77aa2549b1d5f4fb329d5d915d1b3608de1d6302c73b0adce76dc3c22fe",
    "outreach.py": "d95a71e970f7b826a48fc1895f4eea4260f4ea88b396c0229b9df8d8d704b599",
    "response_status.py": "2e7ef196adfab78cf3b277cb062ec9f621cf2fda60f83af8aee65ad94026bd64",
    "streamlit_app/accounts_logic.py": "f7c55364ccb83fbc3170e030fbfc2eb7fc21cb19748cd3e93028625af7db6d17",
    "streamlit_app/app.py": "ea053926b45c466eb5354790e6926dcd742c105757b917eb13fdb6069a596bb1",
    "streamlit_app/auth.py": "257c2b210b27e289e4c4b407384fefb32ad719026d290ea9d9cde58aceacd142",
    "streamlit_app/campaign_analytics_logic.py": "754f020cf146d43a46bf23cca94e93789c14c07fbc8f49e1f3b7e69786722c00",
    "streamlit_app/campaign_builder.py": "0fbe9ce75bc3fc64a500486b4e951e44d84e63642abaf736f709e498bc5f38fa",
    "streamlit_app/campaign_header_logic.py": "d8a1e7f8de418d58ce4e41aaf61069330aa8b4a9f0c90e89dc56ccf14e4e8478",
    "streamlit_app/campaign_html.py": "b935f16bf14e886e798a8ad524f6ebc094ec969c0d5d68a83cd00eea18056e9c",
    "streamlit_app/campaign_status_logic.py": "f0fded3b7fb810b0e1de4f682cb85953f8140c01a8a1fcd3f85dc514ef03f65c",
    "streamlit_app/campaigns_hub_logic.py": "f7c681434676b130a1c1d47474661fcacef10ac99bea22412fc24fe353a0694c",
    "streamlit_app/config.py": "2930e290cc483fa10956f7ea69dada03fd18d7e48a190b81987d5aba74c33b78",
    "streamlit_app/conversation_logic.py": "8a349f4725006ae5e5eedba4593d8de261c377f88de07abac845f7d342f4c073",
    "streamlit_app/data_import_logic.py": "965cc71866c9456ef27d749a891d8c6400909bc0b1af7494050a7c69d869f1e3",
    "streamlit_app/email_account_slots_logic.py": "4f2fbd2e56a8c538fbdfa066dc4dc9ddb1fd84b87ef5a832b4c3bd6a96e65164",
    "streamlit_app/github_client.py": "b6dc1d923164f12c64041e0df2ea6da687ccc4e3962f84c3479cab8b19380ec6",
    "streamlit_app/inbox_html.py": "cb592269779276920877a78d8cb025b97ebe5ec1d3a5eb09169c2e3733a90f47",
    "streamlit_app/inbox_keywords.py": "c4d9978131d1ab1ef5282dff1ad62a36db9531d500e4ae5fcf348a969faf19ad",
    "streamlit_app/inbox_logic.py": "deb3882a9e697f0c198a1c7c0753e274635a854587b85e43ae21f29fb4ffd5d1",
    "streamlit_app/inbox_senders.py": "52dfe223ce54ce53f591156deab6a666c708805d5be2c57331acd14ddeecbce9",
    "streamlit_app/inbox_view.py": "9346e649ca2c342f208c7c24d27f8a957a854c6fa0954b5b31cf2a34924778d1",
    "streamlit_app/launch_logic.py": "6402fea1b63a9c1d81f8274df3270da5c373e8b4a8fc360064642602c6f649e1",
    "streamlit_app/leads_actions.py": "32f4a9151144f63fb2c0c348f9eaabaa7bb0ef8c382fa72cd7a37d4522c5f404",
    "streamlit_app/leads_html.py": "4d2b29d3d820012059198ae200a572a610362f9ed915dec4915e76c10effbd3f",
    "streamlit_app/leads_logic.py": "e2a45c64f3856952cc36e0c2f6e9285fcf488b6793219cb68327c63ea9b6ee4e",
    "streamlit_app/leads_view.py": "a05ea42b51578175fe34c3485318f711c1c65ee2952cab166a7b5e95908a6bf4",
    "streamlit_app/page_state.py": "11a24a73225667f238a37c0b534438cab74154aef2f9f6abc88a25efbc601f41",
    "streamlit_app/pages/campaigns.py": "10321568829af1b5b0c47ea9794afb437aaa08935d9d894826fa5667924a4b31",
    "streamlit_app/pages/controls.py": "4f34cefd534b5339f252ee3ad63d2fab4fa4ba0a7c04e9ad6a907066de0ae56e",
    "streamlit_app/pages/email_accounts.py": "2ef1190d4b53b8a6400807b843bd6a3fdaa95a092ae63dbaeb887e2cac518ea7",
    "streamlit_app/pages/new_campaign.py": "51a78cc525c889add4087a78f26610dedba0b11874bf43ed904647c1a59a3311",
    "streamlit_app/pages/performance.py": "05ffcac81f0403cb529df0b6ef64877a9aa1be3467f77eea6805af9010d699ec",
    "streamlit_app/pages/responses.py": "56eb79761c0006ea7bf43ce4e69c3ba017f4651db3a4b9374f4dff8fe93e8c4d",
    "streamlit_app/pages/today.py": "9b763d0a3d33a11b42b1a8d5e5cb2ccbbc9377c4f3aef4564734b599186c8e7c",
    "streamlit_app/parallel_logic.py": "ed057a1f7dec53401b758d96097ad38c0426371d41d2d9acc6d275c72df72f5d",
    "streamlit_app/performance_figures.py": "95c0f02b9759c7ddc6f82dc05677956f9da1f29fb7ea00c81ef6f12b5be42bc4",
    "streamlit_app/performance_html.py": "d3d44bd3283dd65a948b6b1ace32a34d21cbc35894324396c67b6284fc6ded3c",
    "streamlit_app/performance_logic.py": "a51f5c5a5c13f6760508466bff1d8740a006c84e7db556771a513af58827be18",
    "streamlit_app/performance_view.py": "9ee9c3f413d5063ab5f087084dc27ef8e724b6231bd681e14af7e581d7f9d732",
    "streamlit_app/preview_logic.py": "7309b595cf2712d6df33dc1c5777474cd07a5fe0599184ed52be8bee2768f632",
    "streamlit_app/replies_logic.py": "29ea69e1b6a4b4e354c5ae34069e16dc89d6cbb10d66bb32289304b86629d3eb",
    "streamlit_app/responses_hub_logic.py": "fe4e0091ca5ffc54ddb426312a96601f0ff7b66110958ec93efe1fcdbae5a187",
    "streamlit_app/responses_reply_logic.py": "8b26e6140dac41b71726dc910f3c2a1d194d5a095da310d5905b8ea341d73e6f",
    "streamlit_app/schedule_html.py": "6553914d6879833d19348cf8fd5cac9380b1840e04c73e31ceb496c508ae2bc4",
    "streamlit_app/schedule_logic.py": "f33e853e3264debfebcdfe6b8fefdac395a6211f536749572d5daf7eb7552d74",
    "streamlit_app/schedule_view.py": "4980c6c31cf42e4ee1dc9e5404fbca05481b490428352ec0b9ba9d39153966ae",
    "streamlit_app/schedule_view_logic.py": "90685df02d7def838ea74cdc7ac4e8bd54f5b5beff36148954b4bba59e00ce18",
    "streamlit_app/send_logic.py": "c9db86a73fbedbe7779e0fa42b59cd094fc684d33c2537c90d84b39ed137fda1",
    "streamlit_app/sequences_html.py": "b3163c8fbc6b1abdce9ba96bcebefbdc8f104b91d260420a9e6ecc05e9ef402e",
    "streamlit_app/sequences_logic.py": "f4ee777b37bca74bc2b9df198af733fae971794875aa88761f3636ac3e26698a",
    "streamlit_app/sequences_view.py": "fa8a92e61a94cab43affc3e1627ff9736632480ad269fb096b4322803fb44d07",
    "streamlit_app/sequences_view_logic.py": "2f4efd31e6997aa226c9606e970e07fda2118b6e5d1426d3bf6a787439864e56",
    "streamlit_app/settings_html.py": "0002c8080c0d1d487fbcac211a0c13d11f3e056dad2faf67c8b734cc425713c8",
    "streamlit_app/settings_logic.py": "309e5a71169d7224a5868be2e5905dd9a7cdcd098c45f554bde396e55907716c",
    "streamlit_app/settings_view.py": "9f12e2dbd50b194a5a72bbea80f352661c71858e793ab2600d2ab53935afeeac",
    "streamlit_app/settings_view_logic.py": "759735b507968868d09e5620de9b428020667315fc9abe4ff9b7331905fbe38a",
    "streamlit_app/sheets_readonly.py": "64e9cd579b6e0c69f49d664ebaabe0260c63dbc7227314b73a1bd62f1b2894df",
    "streamlit_app/tests/conftest.py": "7f71171c94a87ed0e214c45d9a2250827afb427223ddd844da0da58cee1837d0",
    "streamlit_app/tests/test_accounts_logic.py": "60c18376564cf915df54b0c9f49dbeed2195f17e266276faafec2be3052cd4e3",
    "streamlit_app/tests/test_auth.py": "3b58668ea6f673adcb9bda4c0cf6f2926d18bdfa0e0329adcbba12c75f98e42a",
    "streamlit_app/tests/test_campaign_analytics_logic.py": "e13b15a0da888122e4c19df54e286052861bded7a1f81b36ac36611f30df4904",
    "streamlit_app/tests/test_campaign_builder.py": "e1ab6a492af59a6b3ec21dc2db55f98cdc7289cd8e35e0697299958112b9dbf8",
    "streamlit_app/tests/test_campaign_header_logic.py": "fd1459e223c3585b6b5ab2805826df6536cf974c203bc5be6de2e89397471edd",
    "streamlit_app/tests/test_campaign_header_page.py": "d5d35bec7ef9724cfaa9d290039d8ea1f4295288baeff246f230cbe88541d23e",
    "streamlit_app/tests/test_campaign_html.py": "a23d640f482a14558b01fd5fc29060e8fd3f063966fbafa52afd9613614a8f20",
    "streamlit_app/tests/test_campaign_status_logic.py": "c5c4dad9987bff0cde169c4e8343b2fa0edef39042709226ce05021c58bc39c2",
    "streamlit_app/tests/test_campaigns_hub_logic.py": "e3b654764af95c028fe5c49f9fab42d4aa0f492928ea7f94ef48f18733f0b71f",
    "streamlit_app/tests/test_conversation_logic.py": "6ffe59d5398e782aa0b158a511ecb8344bd77cc625b437d52f311980e593e04a",
    "streamlit_app/tests/test_data_import_logic.py": "37d54c02252d06036eb775e918724d08b3d700d521e9864c89be1a8b11c2ab99",
    "streamlit_app/tests/test_email_account_slots_logic.py": "5d0b8f49f9508a77fffa0fe81aae82046ab039a5b0025ae0d9b1428ca750e8d6",
    "streamlit_app/tests/test_github_client.py": "6353049e0e6d51c476bad7cc824ba473c80d35c15eb65ec2e0e91c75d5221cf5",
    "streamlit_app/tests/test_github_read_cache_pages.py": "c6c2f9da098e5b3afaf3e95cdfcd081ed053ba70c263b69b27c2e16d3ccd2002",
    "streamlit_app/tests/test_inbox_html.py": "89f12da1548496663cdd4800b9e495a8739a860157409d04d750619055c05cf8",
    "streamlit_app/tests/test_inbox_keywords.py": "b5051ef7d67f914e99c7aef266a6933bc7ca993ed0b7f324816c4e4730c67cbc",
    "streamlit_app/tests/test_inbox_logic.py": "dfaaf4eb5b863627496bd099b410eea1c2a711ad2f581be87b488e92ba39d2bf",
    "streamlit_app/tests/test_inbox_page.py": "50798c39507ffd99e6f54180b9b6a69b02222925fc07858c0be8dc354a3ef7c1",
    "streamlit_app/tests/test_inbox_senders.py": "06b0f84a97635e3703be9f521cbcfe72726883e4fcc4de129a1889ed5d06387d",
    "streamlit_app/tests/test_launch_logic.py": "a02affbb7bcb1cdbb3f42a16a128bd0e9fb45d3f21ed4eadd23c72b4b9ecb7bf",
    "streamlit_app/tests/test_leads_html.py": "f4fe3bdb78b882ce83790c93a50d855bad8749751f2b43c4a04b3ddc61439009",
    "streamlit_app/tests/test_leads_logic.py": "0bd921f947bd31bffc29f6330fbb2db9cc10bb8542364f38383ba893dea9187d",
    "streamlit_app/tests/test_leads_page.py": "14527b7ed005d31394c8e79023a7f746b4274329695894ce678efdead0ba0a36",
    "streamlit_app/tests/test_live_reads.py": "b8551c3aaed77067bbc6737406d62a70df2a3862d5db9d7148f7fcf4a87ed4e2",
    "streamlit_app/tests/test_page_state.py": "b7a20ab124efd745994791cf7da0300865a78734c7d076ed584b9acc5edb0ca9",
    "streamlit_app/tests/test_pages_smoke.py": "6fea4542e852994769ddb98be302965afad083c09e5584af507a500a51c9c5b6",
    "streamlit_app/tests/test_parallel_logic.py": "a405200bb0f41b86d6980de9cb9d863510b56c516b9456feb47014835d5d98ab",
    "streamlit_app/tests/test_performance_figures.py": "e1360d36645a2a06e41bebb1e92d20cc47abe9580a5dd2836018fb6992cad74a",
    "streamlit_app/tests/test_performance_html.py": "af6b178e8be1272b723a1a4cbfe28f8dd70f44235e2840d2a9fa07ac3787d9d2",
    "streamlit_app/tests/test_performance_logic.py": "0a6ab851a715ee70221fff8481796f0a114110f555c21894411652cb74c17077",
    "streamlit_app/tests/test_performance_page.py": "aa4e6e16a0c6cfe3960253474d11dcf4d2b72f6bb9c1e199ab2305f56b5b4641",
    "streamlit_app/tests/test_preview_logic.py": "8d55f7ceb102bd2d635f3307becaf112d6333da5fd2c942cfaf9996717d8527c",
    "streamlit_app/tests/test_replies_logic.py": "37fd3feba359c864eac69a4a260394b8c209e3d0fb9b6f853f9c838120a94c0e",
    "streamlit_app/tests/test_responses_hub_logic.py": "4b08aec8fec8f16e5da999d3c4c2e1766c93336f947ee036536a846814e5081b",
    "streamlit_app/tests/test_responses_reply_logic.py": "c60429ff87c3847ad2593f13f1eabfbc9e192e4e834a334e2bfe0d0c57f2039d",
    "streamlit_app/tests/test_schedule_html.py": "cf1c1954253175ba832e8ea548f1fb98b77b2a69c8a4d64c040dd541c4df72a8",
    "streamlit_app/tests/test_schedule_logic.py": "09c24045bd9bdddbe9fec4980968c73e2abce28e46946a8d48611bc7c07f4ce3",
    "streamlit_app/tests/test_schedule_page.py": "403600e0fb9fbd5716be3015d1be8467705116558811709b62d439856e96f05f",
    "streamlit_app/tests/test_schedule_view_logic.py": "ca5164df57ddf4a9b965cb453f3869cbdaacdcc5cada607aa7b700ac7714fd14",
    "streamlit_app/tests/test_send_logic.py": "efa7feb134371b123b7f51b709bc277822e894fb4710e30143c6a7371fe2c4aa",
    "streamlit_app/tests/test_sequences_html.py": "5e54b0b543b84a2808533afa83db0b7179ea1e64cfad078edd139e5bea70adab",
    "streamlit_app/tests/test_sequences_logic.py": "b7b45e00286ba3316f64eb17f9cca6d897c85232fc91923cf6e8ae6ea0555458",
    "streamlit_app/tests/test_sequences_page.py": "c767bab30a47a03d6caae719b850f2d1782d43cb29774e547932a2018ed8e305",
    "streamlit_app/tests/test_sequences_view_logic.py": "e86e8d5e2bd18510999014dda443bd532c04a3842c79dd9deccab522537dc4a5",
    "streamlit_app/tests/test_settings_html.py": "bd6799b7b63ed20cdae0e2cda11a62dd4f28b4d211abf0f01d22a0ea416bd3a1",
    "streamlit_app/tests/test_settings_logic.py": "4383bff70691246dbd827597fe095001808c8c974eb26d1f069625a6c41183ab",
    "streamlit_app/tests/test_settings_page.py": "80cb6ce396c7a29c8b6618674f02aa3808bdceeda5a1291b6d038767d38b624a",
    "streamlit_app/tests/test_settings_view_logic.py": "d1a9c11b9c28d80d0699aa90b0663c1eec4cfa7825254613767d8f65cec2ad21",
    "streamlit_app/tests/test_sheet_speed_pages.py": "ce5c735927a511c68dd4d6ef9da3bdade5594949096c9f53065311e12bdc4213",
    "streamlit_app/tests/test_sheets_readonly.py": "860aa13274740b1ad74c80d7b602738ee53f783f84271430811dbb8d52d80f51",
    "streamlit_app/tests/test_today_page.py": "2282140b5be9e101201dd37e4852edac068a33d6c1f7291ebaf7e43c5307ffea",
    "streamlit_app/tests/test_work_queue_logic.py": "b7b17d849b88876bd67fc357a917d4856b0ecee32d799c820497025c2ff2066a",
    "streamlit_app/tools/generate_password_hash.py": "fcdd90aacb6cf86aa755f623c9d0da0608e8ddbd55135c3cf36255a2c57f6354",
    "streamlit_app/work_queue_logic.py": "e45fef528edccbb8bdaf08c29326fc98404b10f07de92f575bd7d3b2d19643fb",
    "tests/test_response_status.py": "131a1e040c34be625a968fbf6485e0d1a4e4b535dbe1513f093e7ce7e4dd4aaf",
    "tests/test_stage_wait_overrides.py": "b784bda4227086834a5dd811387b9c02419ead37f91af743416debf17ac811b4",
}


# A file that is allowed to live in more than one place. The theme file works from either folder, so it is not "missing" when it is in the other one.
ALSO_ACCEPTED = {".streamlit/config.toml": ["streamlit_app/.streamlit/config.toml"]}


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read().replace(b"\r\n", b"\n")).hexdigest()


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
            print(f"MISSING  {path}" + (f"   (also looked in: {', '.join(places[1:])})" if len(places) > 1 else ""))
        elif not any(digest(place) == expected for place in found):
            stale.append(path)
            print(f"STALE    {found[0]}")
    ok = len(EXPECTED) - len(stale) - len(missing)
    print(f"\n{ok} of {len(EXPECTED)} files are exactly the tested version.")
    if stale or missing:
        print(f"{len(stale)} stale, {len(missing)} missing. Paste this output back and the right files will be sent.")
        return 1
    print("Everything matches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
