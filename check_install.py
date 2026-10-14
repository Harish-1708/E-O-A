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
    "streamlit_app/inbox_html.py": "610c328034f72219eb1644e80158dea7e4fee9837fc16013d9e378b2a7c6c470",
    "streamlit_app/inbox_keywords.py": "c4d9978131d1ab1ef5282dff1ad62a36db9531d500e4ae5fcf348a969faf19ad",
    "streamlit_app/inbox_logic.py": "deb3882a9e697f0c198a1c7c0753e274635a854587b85e43ae21f29fb4ffd5d1",
    "streamlit_app/inbox_view.py": "2ef98dc925ac93650b2ede275660d51c4458fceebe8aa2d90d1ccc4a3e349c5d",
    "streamlit_app/launch_logic.py": "6402fea1b63a9c1d81f8274df3270da5c373e8b4a8fc360064642602c6f649e1",
    "streamlit_app/leads_actions.py": "32f4a9151144f63fb2c0c348f9eaabaa7bb0ef8c382fa72cd7a37d4522c5f404",
    "streamlit_app/leads_html.py": "4d2b29d3d820012059198ae200a572a610362f9ed915dec4915e76c10effbd3f",
    "streamlit_app/leads_logic.py": "e2a45c64f3856952cc36e0c2f6e9285fcf488b6793219cb68327c63ea9b6ee4e",
    "streamlit_app/leads_view.py": "a05ea42b51578175fe34c3485318f711c1c65ee2952cab166a7b5e95908a6bf4",
    "streamlit_app/page_state.py": "11a24a73225667f238a37c0b534438cab74154aef2f9f6abc88a25efbc601f41",
    "streamlit_app/pages/campaigns.py": "d0739874f85a432f01c2fd4e4cc82c0189e25fe5e0dff637454c2ab11602668e",
    "streamlit_app/pages/controls.py": "4f34cefd534b5339f252ee3ad63d2fab4fa4ba0a7c04e9ad6a907066de0ae56e",
    "streamlit_app/pages/email_accounts.py": "2ef1190d4b53b8a6400807b843bd6a3fdaa95a092ae63dbaeb887e2cac518ea7",
    "streamlit_app/pages/new_campaign.py": "51a78cc525c889add4087a78f26610dedba0b11874bf43ed904647c1a59a3311",
    "streamlit_app/pages/performance.py": "05ffcac81f0403cb529df0b6ef64877a9aa1be3467f77eea6805af9010d699ec",
    "streamlit_app/pages/responses.py": "06d84668878e4bab9cf865ab5e1fea45a0fbb44cc200d0097ce9a0c077fd1921",
    "streamlit_app/pages/today.py": "9b763d0a3d33a11b42b1a8d5e5cb2ccbbc9377c4f3aef4564734b599186c8e7c",
    "streamlit_app/parallel_logic.py": "ed057a1f7dec53401b758d96097ad38c0426371d41d2d9acc6d275c72df72f5d",
    "streamlit_app/performance_figures.py": "95c0f02b9759c7ddc6f82dc05677956f9da1f29fb7ea00c81ef6f12b5be42bc4",
    "streamlit_app/performance_html.py": "d3d44bd3283dd65a948b6b1ace32a34d21cbc35894324396c67b6284fc6ded3c",
    "streamlit_app/performance_logic.py": "a51f5c5a5c13f6760508466bff1d8740a006c84e7db556771a513af58827be18",
    "streamlit_app/performance_view.py": "9ee9c3f413d5063ab5f087084dc27ef8e724b6231bd681e14af7e581d7f9d732",
    "streamlit_app/preview_logic.py": "7309b595cf2712d6df33dc1c5777474cd07a5fe0599184ed52be8bee2768f632",
    "streamlit_app/replies_logic.py": "29ea69e1b6a4b4e354c5ae34069e16dc89d6cbb10d66bb32289304b86629d3eb",
    "streamlit_app/responses_hub_logic.py": "fe4e0091ca5ffc54ddb426312a96601f0ff7b66110958ec93efe1fcdbae5a187",
    "streamlit_app/responses_reply_logic.py": "b89d28ca7240f1283e5fee56178f39fc7356fa0249656486453db5f2815fb1d2",
    "streamlit_app/schedule_html.py": "bb183640e52333425ab699c69dd0f8a832c09fb53407425deebe31c667837513",
    "streamlit_app/schedule_logic.py": "6084a960fa86feaa93f3f281994d5edc0ff49e1d906f52e1b158b3e0a295a778",
    "streamlit_app/schedule_view.py": "a0b85a26aee85734a06c1f915864026182cd49bbcfc97e78b8c09935bdf7a549",
    "streamlit_app/schedule_view_logic.py": "769b1d513cf030cdfabd61ec380ef4b227681e458f896020d1c78fa404263a28",
    "streamlit_app/send_logic.py": "c9db86a73fbedbe7779e0fa42b59cd094fc684d33c2537c90d84b39ed137fda1",
    "streamlit_app/sequences_html.py": "a5ef1f1f0eab448299d90743ae054cf0cb35ddcd71516c6f7bda463b197e267b",
    "streamlit_app/sequences_logic.py": "f4ee777b37bca74bc2b9df198af733fae971794875aa88761f3636ac3e26698a",
    "streamlit_app/sequences_view.py": "9d9a2b67ef5184a8e4e93b7a4e63d1498c5c0d9bad0489a45d9b7771b8dc8740",
    "streamlit_app/sequences_view_logic.py": "fd39a9eaf112bdde5055819a8d1981efb7a141fa7a553c1eed3aa408bebc8844",
    "streamlit_app/settings_logic.py": "86dbc5ecb5315c6b3c32fcf819913b4b399a77ad40f27a019cd6ef0f1fc4bad0",
    "streamlit_app/sheets_readonly.py": "64e9cd579b6e0c69f49d664ebaabe0260c63dbc7227314b73a1bd62f1b2894df",
    "streamlit_app/tests/conftest.py": "7f71171c94a87ed0e214c45d9a2250827afb427223ddd844da0da58cee1837d0",
    "streamlit_app/tests/test_accounts_logic.py": "60c18376564cf915df54b0c9f49dbeed2195f17e266276faafec2be3052cd4e3",
    "streamlit_app/tests/test_auth.py": "3b58668ea6f673adcb9bda4c0cf6f2926d18bdfa0e0329adcbba12c75f98e42a",
    "streamlit_app/tests/test_campaign_analytics_logic.py": "e13b15a0da888122e4c19df54e286052861bded7a1f81b36ac36611f30df4904",
    "streamlit_app/tests/test_campaign_builder.py": "7a452e52a4163b4a4ae869bb3794b0404fb100b5f9d719f0530795ef7501710d",
    "streamlit_app/tests/test_campaign_header_logic.py": "fd1459e223c3585b6b5ab2805826df6536cf974c203bc5be6de2e89397471edd",
    "streamlit_app/tests/test_campaign_header_page.py": "7730b03e843b75dccf47ce1b7703b85c9dcf14d35dd4e909f68b64c75fb7fc0d",
    "streamlit_app/tests/test_campaign_html.py": "a23d640f482a14558b01fd5fc29060e8fd3f063966fbafa52afd9613614a8f20",
    "streamlit_app/tests/test_campaign_status_logic.py": "c5c4dad9987bff0cde169c4e8343b2fa0edef39042709226ce05021c58bc39c2",
    "streamlit_app/tests/test_campaigns_hub_logic.py": "e3b654764af95c028fe5c49f9fab42d4aa0f492928ea7f94ef48f18733f0b71f",
    "streamlit_app/tests/test_conversation_logic.py": "6ffe59d5398e782aa0b158a511ecb8344bd77cc625b437d52f311980e593e04a",
    "streamlit_app/tests/test_data_import_logic.py": "37d54c02252d06036eb775e918724d08b3d700d521e9864c89be1a8b11c2ab99",
    "streamlit_app/tests/test_email_account_slots_logic.py": "5d0b8f49f9508a77fffa0fe81aae82046ab039a5b0025ae0d9b1428ca750e8d6",
    "streamlit_app/tests/test_github_client.py": "6353049e0e6d51c476bad7cc824ba473c80d35c15eb65ec2e0e91c75d5221cf5",
    "streamlit_app/tests/test_github_read_cache_pages.py": "5deffd289556e4b3b0d6982ef76bf6b5af79895f35ce70b422fefa5aa0e427ec",
    "streamlit_app/tests/test_inbox_html.py": "c7cc68756d4df3c3b221f02c277cf6aeeacf3b798c9a5d51cd66b3acdcfbf569",
    "streamlit_app/tests/test_inbox_keywords.py": "b5051ef7d67f914e99c7aef266a6933bc7ca993ed0b7f324816c4e4730c67cbc",
    "streamlit_app/tests/test_inbox_logic.py": "dfaaf4eb5b863627496bd099b410eea1c2a711ad2f581be87b488e92ba39d2bf",
    "streamlit_app/tests/test_inbox_page.py": "1816c96fbade5ae40ff39252264b4eb538702e8fd569c8df9d3fecf6f6b0ad48",
    "streamlit_app/tests/test_launch_logic.py": "a02affbb7bcb1cdbb3f42a16a128bd0e9fb45d3f21ed4eadd23c72b4b9ecb7bf",
    "streamlit_app/tests/test_leads_html.py": "f4fe3bdb78b882ce83790c93a50d855bad8749751f2b43c4a04b3ddc61439009",
    "streamlit_app/tests/test_leads_logic.py": "0bd921f947bd31bffc29f6330fbb2db9cc10bb8542364f38383ba893dea9187d",
    "streamlit_app/tests/test_leads_page.py": "14527b7ed005d31394c8e79023a7f746b4274329695894ce678efdead0ba0a36",
    "streamlit_app/tests/test_live_reads.py": "b8551c3aaed77067bbc6737406d62a70df2a3862d5db9d7148f7fcf4a87ed4e2",
    "streamlit_app/tests/test_page_state.py": "b7a20ab124efd745994791cf7da0300865a78734c7d076ed584b9acc5edb0ca9",
    "streamlit_app/tests/test_pages_smoke.py": "22409720b280f38174112a50222e19e2aaeb85ff6f12c298ed1355780d5a2562",
    "streamlit_app/tests/test_parallel_logic.py": "a405200bb0f41b86d6980de9cb9d863510b56c516b9456feb47014835d5d98ab",
    "streamlit_app/tests/test_performance_figures.py": "e1360d36645a2a06e41bebb1e92d20cc47abe9580a5dd2836018fb6992cad74a",
    "streamlit_app/tests/test_performance_html.py": "af6b178e8be1272b723a1a4cbfe28f8dd70f44235e2840d2a9fa07ac3787d9d2",
    "streamlit_app/tests/test_performance_logic.py": "0a6ab851a715ee70221fff8481796f0a114110f555c21894411652cb74c17077",
    "streamlit_app/tests/test_performance_page.py": "aa4e6e16a0c6cfe3960253474d11dcf4d2b72f6bb9c1e199ab2305f56b5b4641",
    "streamlit_app/tests/test_preview_logic.py": "8d55f7ceb102bd2d635f3307becaf112d6333da5fd2c942cfaf9996717d8527c",
    "streamlit_app/tests/test_replies_logic.py": "37fd3feba359c864eac69a4a260394b8c209e3d0fb9b6f853f9c838120a94c0e",
    "streamlit_app/tests/test_responses_hub_logic.py": "4b08aec8fec8f16e5da999d3c4c2e1766c93336f947ee036536a846814e5081b",
    "streamlit_app/tests/test_responses_reply_logic.py": "41ebc280ca992c2011899af80e2e1e07f7bdcebcbb52392e893dbc0f3c8f3e2a",
    "streamlit_app/tests/test_schedule_html.py": "157729a4f95ac9c979ad7e825e85edfa125561e2d833eee3427c1b6a643eed7d",
    "streamlit_app/tests/test_schedule_logic.py": "7af34508e725d6f1259a02d134687c15ec3a29f5275929d2a8515ace2110cdaf",
    "streamlit_app/tests/test_schedule_page.py": "d14668eaadf963efcbaaabb98e07a1a74d355baa399524224f26b09449a79086",
    "streamlit_app/tests/test_schedule_view_logic.py": "cf57ba8abc76e6b9a9391720f5ea7389564d8c8bc123a330b640921b3c7fb63b",
    "streamlit_app/tests/test_send_logic.py": "efa7feb134371b123b7f51b709bc277822e894fb4710e30143c6a7371fe2c4aa",
    "streamlit_app/tests/test_sequences_html.py": "a48a402c7013a893a80d110fe68d9d165fd009457caff5475f9a719326fefd93",
    "streamlit_app/tests/test_sequences_logic.py": "b7b45e00286ba3316f64eb17f9cca6d897c85232fc91923cf6e8ae6ea0555458",
    "streamlit_app/tests/test_sequences_page.py": "1a57a01662426531db27f600535a17fdb663ffc76b835243034b4e5a7f2c0e35",
    "streamlit_app/tests/test_sequences_view_logic.py": "faca0acea4a45877a4947200031341026db8c8d566d07f8fa50a15131373afd0",
    "streamlit_app/tests/test_settings_logic.py": "05b590aa21808efd07e0f5a6c3ebff0ec86a0b5d914b633f55a67372d90059e2",
    "streamlit_app/tests/test_sheet_speed_pages.py": "e488c2db2826d62297c3dab0b02b333c1433e063f6e0c0679ab1b0043bbd2236",
    "streamlit_app/tests/test_sheets_readonly.py": "860aa13274740b1ad74c80d7b602738ee53f783f84271430811dbb8d52d80f51",
    "streamlit_app/tests/test_today_page.py": "2282140b5be9e101201dd37e4852edac068a33d6c1f7291ebaf7e43c5307ffea",
    "streamlit_app/tests/test_work_queue_logic.py": "b7b17d849b88876bd67fc357a917d4856b0ecee32d799c820497025c2ff2066a",
    "streamlit_app/tools/generate_password_hash.py": "fcdd90aacb6cf86aa755f623c9d0da0608e8ddbd55135c3cf36255a2c57f6354",
    "streamlit_app/work_queue_logic.py": "e45fef528edccbb8bdaf08c29326fc98404b10f07de92f575bd7d3b2d19643fb",
    "tests/test_response_status.py": "131a1e040c34be625a968fbf6485e0d1a4e4b535dbe1513f093e7ce7e4dd4aaf",
}


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read().replace(b"\r\n", b"\n")).hexdigest()


def main():
    if not os.path.isdir("streamlit_app"):
        print("Run this from the root of your repo: the folder that contains the streamlit_app folder.")
        return 2
    stale, missing = [], []
    for path, expected in sorted(EXPECTED.items()):
        if not os.path.exists(path):
            missing.append(path)
            print(f"MISSING  {path}")
        elif digest(path) != expected:
            stale.append(path)
            print(f"STALE    {path}")
    ok = len(EXPECTED) - len(stale) - len(missing)
    print(f"\n{ok} of {len(EXPECTED)} files are exactly the tested version.")
    if stale or missing:
        print(f"{len(stale)} stale, {len(missing)} missing. Paste this output back and the right files will be sent.")
        return 1
    print("Everything matches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
