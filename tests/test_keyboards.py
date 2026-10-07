from app.bot import keyboards
from app.bot.callbacks import Action, ReportStep


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_home_has_all_product_routes_and_admin_visibility():
    normal = {
        Action.unpack(value).name
        for value in callbacks(keyboards.home())
        if value.startswith("sc|")
    }
    assert {"lookup", "rep", "report", "top", "scams", "profile", "info"} <= normal
    assert "admin" not in normal
    assert "admin" not in {
        Action.unpack(value).name
        for value in callbacks(keyboards.home(True))
        if value.startswith("sc|")
    }


def test_report_navigation_and_nonce_are_consistent():
    for stage in ("target", "reason", "evidence", "preview"):
        values = callbacks(keyboards.report("nonce123", stage))
        assert keyboards.action("close") in values
        for value in values:
            if value.startswith("draft:"):
                assert ReportStep.unpack(value).nonce == "nonce123"
        if stage == "preview":
            assert "submit" in [
                ReportStep.unpack(v).action for v in values if v.startswith("draft:")
            ]


def test_pagination_clamps_first_and_last():
    first = callbacks(keyboards.pages(0, 11))
    last = callbacks(keyboards.pages(2, 11))
    assert Action.unpack(first[0]).value == "0"
    assert Action.unpack(first[2]).value == "1"
    assert Action.unpack(last[2]).value == "2"
    assert keyboards.action("home") in last


def test_top_links_use_username_or_numeric_telegram_identity():
    from types import SimpleNamespace

    rows = [
        {"user": SimpleNamespace(username="name_one", telegram_id=42, display_name="One")},
        {"user": SimpleNamespace(username=None, telegram_id=43, display_name="Two")},
    ]
    buttons = [button for row in keyboards.leaderboard(rows).inline_keyboard for button in row]
    assert buttons[0].url == "https://t.me/name_one"
    assert buttons[1].url == "tg://user?id=43"
    assert buttons[-1].callback_data == keyboards.action("home")
