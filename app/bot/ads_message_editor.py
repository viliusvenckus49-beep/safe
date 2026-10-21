"""Bottom-of-chat AUTO ADS input/receipt controller, using existing content services."""

import secrets
from copy import deepcopy

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyParameters

from app.ads_content import media_from_message, parse_buttons, text_from_message
from app.ads_customers import CustomerAdsService
from app.bot.ads import (
    callback_request,
    campaign_panel,
    cb,
    customer_cb,
    failure,
    private,
    send_preview,
)
from app.bot.keyboards import keyboard
from app.bot.states import AdMessageFlow
from app.bot.validation import actor_id
from app.errors import DomainError
from app.i18n import t

ACTIONS = {"qe", "qb", "qp", "qr", "qf", "qk"}
FIELDS = {"TEXT", "MEDIA", "BUTTONS", "ADD", "EDIT"}
log = structlog.get_logger()


def control(action, cid, value="", *, customer=False):
    return (customer_cb if customer else cb)(action, cid, value)


def brand(text):
    return "🛡 <b>CRIMSON SAFECHECK™</b>\n\n" + text


async def retire(message, ids):
    """Only our recorded bot messages in this actor's private dialog are eligible."""
    bot = message.bot
    if bot is None:
        raise DomainError("forbidden")
    for mid in dict.fromkeys(ids):
        if not isinstance(mid, int) or mid <= 0:
            continue
        try:
            await bot.delete_message(message.chat.id, mid)
        except (TelegramAPIError, TimeoutError, OSError) as error:
            # A too-old/missing message must never leave an executable old control.
            try:
                await bot.edit_message_reply_markup(
                    chat_id=message.chat.id, message_id=mid, reply_markup=None
                )
            except (TelegramAPIError, TimeoutError, OSError):
                pass
            log.info("ads_editor_cleanup_unavailable", error=type(error).__name__)


async def leave_editor(message, state):
    data = await state.get_data()
    ids = list(data.get("q_previews", []))
    panel = data.get("q_panel")
    if panel and panel != message.message_id:
        ids.append(panel)
    await retire(message, ids)
    await state.update_data(q_panel=0, q_token="", q_kind="", q_previews=[])


async def replace(message, state, text, markup, *, extra=(), reply=None):
    data = await state.get_data()
    await retire(message, [data.get("q_panel", 0), *extra])
    # Persist invalidation before an uncertain send, so old callbacks cannot act.
    await state.update_data(q_panel=0)
    result = await message.answer(
        text,
        reply_markup=markup,
        parse_mode="HTML",
        reply_parameters=ReplyParameters(message_id=reply, allow_sending_without_reply=True)
        if reply
        else None,
    )
    await state.update_data(q_panel=result.message_id)
    return result


def nav(data, *, collecting=False):
    cid, token, customer = data["q_cid"], data["q_token"], data["q_customer"]
    rows = []
    if collecting:
        rows.append([(t("ads.done"), control("qf", cid, token, customer=customer))])
    elif data.get("q_kind") == "INPUT" and data.get("q_field") in {"TEXT", "MEDIA", "BUTTONS"}:
        rows.append([(t("ads.clear"), control("qr", cid, token, customer=customer))])
    rows.append([(t("ads.back"), control("qb", cid, token, customer=customer))])
    return keyboard(rows)


async def checked(message, state, service, actor, *, customer):
    ads = await private(service, message, actor, customer=customer)
    data = await state.get_data()
    if isinstance(ads, CustomerAdsService) and data.get("q_grant"):
        ads.expected_grant = int(data["q_grant"])
    return ads, data


async def show_main(message, state, ads, actor, cid, *, extra=()):
    data = await state.get_data()
    await retire(message, [data.get("q_panel", 0), *data.get("q_previews", []), *extra])
    await state.set_state(None)
    await state.update_data(q_panel=0, q_token="", q_kind="", q_previews=[])
    panel = await campaign_panel(message, ads, actor, cid, edit=False)
    await state.update_data(
        q_panel=panel.message_id,
        q_cid=cid,
        q_customer=isinstance(ads, CustomerAdsService),
        q_kind="MENU",
    )


async def receipt(message, state, ads, actor, draft, *, reply=None):
    version = await ads.save_received(actor, draft.id)
    await state.set_state(None)
    await state.update_data(q_kind="SET", q_version=version.revision)
    data = await state.get_data()
    await replace(message, state, brand(t("ads.message_set")), nav(data), reply=reply)


async def begin(message, state, ads, actor, cid, value):
    parts = value.split(".")
    if len(parts) not in {2, 3} or parts[0] not in FIELDS:
        raise DomainError("ads_stale")
    field, expected = parts[0], int(parts[1])
    campaign, _ = await ads.current(actor, cid)
    if campaign.version != expected:
        raise DomainError("ads_conflict")
    index = int(parts[2]) if len(parts) == 3 else -1
    if field == "EDIT" and index < 0:
        raise DomainError("ads_stale")
    data = await state.get_data()
    if (
        data.get("q_kind") in {"INPUT", "SET", "PREVIEW"}
        and data.get("q_panel") != message.message_id
    ):
        raise DomainError("ads_stale")
    grant = (
        await ads.grants.check(actor, cid, edit=True)
        if isinstance(ads, CustomerAdsService)
        else None
    )
    await ads.session.commit()
    await retire(message, data.get("q_previews", []))
    await state.set_state(AdMessageFlow.input)
    await state.update_data(
        q_cid=cid,
        q_version=expected,
        q_field=field,
        q_index=index,
        q_customer=isinstance(ads, CustomerAdsService),
        q_grant=grant.id if grant else 0,
        q_token=secrets.token_hex(4),
        q_kind="INPUT",
        q_draft="",
        q_album="",
        q_previews=[],
    )
    data = await state.get_data()
    prompt = (
        t("ads.media_prompt")
        if field == "MEDIA"
        else t("ads.buttons_prompt")
        if field in {"BUTTONS", "ADD", "EDIT"}
        else t("ads.text_prompt")
    )
    await replace(
        message,
        state,
        brand(t("ads.send_now") + "\n\n" + prompt),
        nav(data),
        extra=[message.message_id],
    )


async def input_payload(ads, actor, data, message):
    field = data["q_field"]
    if field == "TEXT":
        return "TEXT", text_from_message(message)
    if field == "MEDIA":
        return "MEDIA", [media_from_message(message)]
    if message.text is None:
        raise DomainError("ads_buttons")
    parsed = parse_buttons(
        message.text, [e.model_dump(mode="json", exclude_none=True) for e in message.entities or []]
    )
    if field == "BUTTONS":
        return "BUTTONS", parsed
    if len(parsed) != 1 or len(parsed[0]) != 1 or parsed[0][0]["kind"] != "url":
        raise DomainError("ads_buttons")
    campaign, version = await ads.current(actor, data["q_cid"])
    if campaign.version != data["q_version"]:
        raise DomainError("ads_conflict")
    buttons = deepcopy(version.content["buttons"])
    if field == "ADD":
        buttons.append(parsed[0])
    else:
        locations = [(r, c) for r, row in enumerate(buttons) for c in range(len(row))]
        if not 0 <= data["q_index"] < len(locations):
            raise DomainError("ads_stale")
        r, c = locations[data["q_index"]]
        if buttons[r][c]["kind"] != "url":
            raise DomainError("ads_stale")
        buttons[r][c] = parsed[0][0]
    return "BUTTONS", buttons


def register_message_editor(router: Router):
    from app.bot.callbacks import AdsAction, CustomerAd

    @router.message(AdMessageFlow.input, ~F.text.startswith("/"))
    async def receive(message: Message, state: FSMContext, service):
        try:
            actor = actor_id(message)
            data = await state.get_data()
            ads, data = await checked(
                message, state, service, actor, customer=data.get("q_customer") is True
            )
            if data.get("q_kind") != "INPUT":
                raise DomainError("ads_stale")
            if data["q_field"] == "MEDIA" and message.media_group_id:
                if data.get("q_album") and data["q_album"] != message.media_group_id:
                    raise DomainError("ads_media")
                if not data.get("q_draft"):
                    draft = await ads.stage(
                        actor,
                        data["q_cid"],
                        data["q_version"],
                        "MEDIA",
                        None,
                        f"ads:album:{message.chat.id}:{message.message_id}",
                    )
                    await state.update_data(q_draft=draft.id, q_album=message.media_group_id)
                else:
                    draft = await ads.draft(actor, data["q_draft"])
                draft = await ads.append_media(
                    actor, draft.id, media_from_message(message), message.message_id
                )
                await replace(
                    message,
                    state,
                    brand(t("ads.collecting", count=len(draft.content["media"]))),
                    nav(await state.get_data(), collecting=True),
                )
                return
            if data.get("q_draft"):
                raise DomainError("ads_media")
            field, value = await input_payload(ads, actor, data, message)
            draft = await ads.stage(
                actor,
                data["q_cid"],
                data["q_version"],
                field,
                value,
                f"ads:received:{message.chat.id}:{message.message_id}",
            )
            await receipt(message, state, ads, actor, draft, reply=message.message_id)
        except (
            DomainError,
            TelegramAPIError,
            ValueError,
            KeyError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            data = await state.get_data()
            if data.get("q_kind") == "SET":
                log.warning("ads_editor_receipt_unconfirmed", error=type(error).__name__)
                return
            if not data.get("q_token"):
                await message.answer(failure(error), parse_mode="HTML")
                return
            await replace(
                message,
                state,
                brand(failure(error)),
                nav(data, collecting=bool(data.get("q_draft"))),
            )

    @router.callback_query(AdsAction.filter(F.action.in_(ACTIONS)))
    @router.callback_query(CustomerAd.filter(F.action.in_(ACTIONS)))
    async def callback(
        callback: CallbackQuery, callback_data: AdsAction | CustomerAd, state: FSMContext, service
    ):
        acknowledged = False
        try:
            if not isinstance(callback.message, Message):
                raise DomainError("forbidden")
            message, actor = callback.message, callback.from_user.id
            customer = isinstance(callback_data, CustomerAd)
            ads, data = await checked(message, state, service, actor, customer=customer)
            name, cid, value = callback_data.action, callback_data.campaign, callback_data.value
            await ads.get(actor, cid)
            if (
                data.get("q_kind") in {"INPUT", "SET", "PREVIEW"}
                and data.get("q_panel")
                and data["q_panel"] != message.message_id
            ):
                raise DomainError("ads_stale")
            if name in {"qb", "qr", "qf"} and (
                not data.get("q_token")
                or value != data["q_token"]
                or data.get("q_cid") != cid
                or data.get("q_customer") != customer
                or data.get("q_panel") != message.message_id
            ):
                raise DomainError("ads_stale")
            await callback.answer()
            acknowledged = True
            if name == "qe":
                await begin(message, state, ads, actor, cid, value)
            elif name == "qb":
                if data.get("q_draft"):
                    await ads.cancel(actor, data["q_draft"])
                await show_main(message, state, ads, actor, cid)
            elif name == "qr":
                if data.get("q_kind") != "INPUT" or data["q_field"] not in {
                    "TEXT",
                    "MEDIA",
                    "BUTTONS",
                }:
                    raise DomainError("ads_stale")
                if data.get("q_draft"):
                    await ads.cancel(actor, data["q_draft"])
                payload = {"text": "", "entities": []} if data["q_field"] == "TEXT" else []
                draft = await ads.stage(
                    actor,
                    cid,
                    data["q_version"],
                    data["q_field"],
                    payload,
                    callback_request(callback, "received-clear"),
                )
                await state.update_data(q_draft="")
                await receipt(message, state, ads, actor, draft)
            elif name == "qf":
                draft = await ads.finish_media(actor, data["q_draft"])
                await state.update_data(q_draft="")
                await receipt(message, state, ads, actor, draft)
            elif name == "qk":
                operation, expected, position = value.split(".")
                campaign, version = await ads.current(actor, cid)
                if campaign.version != int(expected):
                    raise DomainError("ads_conflict")
                if isinstance(ads, CustomerAdsService):
                    ads.expected_grant = (await ads.grants.check(actor, cid, edit=True)).id
                buttons = deepcopy(version.content["buttons"])
                locations = [(r, c) for r, row in enumerate(buttons) for c in range(len(row))]
                index = int(position)
                if operation not in {"REMOVE", "ORDER"} or not 0 <= index < len(locations):
                    raise DomainError("ads_stale")
                r, c = locations[index]
                if buttons[r][c]["kind"] != "url":
                    raise DomainError("ads_stale")
                if operation == "REMOVE":
                    buttons[r].pop(c)
                    buttons = [row for row in buttons if row]
                else:
                    r2, c2 = locations[(index - 1) % len(locations)]
                    buttons[r][c], buttons[r2][c2] = buttons[r2][c2], buttons[r][c]
                draft = await ads.stage(
                    actor,
                    cid,
                    int(expected),
                    "BUTTONS",
                    buttons,
                    callback_request(callback, "received-buttons"),
                )
                await state.update_data(
                    q_panel=message.message_id,
                    q_cid=cid,
                    q_customer=customer,
                    q_token=secrets.token_hex(4),
                    q_draft="",
                )
                await receipt(message, state, ads, actor, draft)
            elif name == "qp":
                if value not in {"TEXT", "MEDIA", "BUTTONS", "FULL"}:
                    raise DomainError("ads_stale")
                _, version = await ads.current(actor, cid)
                await ads.session.commit()
                await retire(
                    message,
                    [data.get("q_panel", 0), message.message_id, *data.get("q_previews", [])],
                )
                await state.set_state(None)
                await state.update_data(
                    q_panel=0,
                    q_cid=cid,
                    q_customer=customer,
                    q_token=secrets.token_hex(4),
                    q_kind="PREVIEW",
                    q_previews=[],
                )
                sent = await send_preview(
                    message, ads, actor, cid, version.content, "v" + str(version.id), value
                )
                await state.update_data(q_previews=[item.message_id for item in sent])
                data = await state.get_data()
                await replace(message, state, brand(t("ads.preview")), nav(data))
        except (
            DomainError,
            TelegramAPIError,
            ValueError,
            KeyError,
            IndexError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            if not acknowledged:
                await callback.answer(failure(error), show_alert=True)
            elif isinstance(callback.message, Message):
                # API delivery failure is not a save failure. Keep any committed
                # revision and show one bounded controller, never an ad re-send.
                data = await state.get_data()
                if data.get("q_kind") == "SET" and isinstance(
                    error, (TelegramAPIError, TimeoutError, OSError)
                ):
                    log.warning("ads_editor_receipt_unconfirmed", error=type(error).__name__)
                    return
                if data.get("q_token") and data.get("q_cid") == callback_data.campaign:
                    text = (
                        t("ads.preview_failed") if callback_data.action == "qp" else failure(error)
                    )
                    await replace(callback.message, state, brand(text), nav(data))
                else:
                    await callback.answer(failure(error), show_alert=True)
