"""Mandatory comment flow shared by REP commands and result buttons."""

from uuid import uuid4

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import ForceReply, Message, ReplyParameters

from app import presentation as p
from app.bot import keyboards as kb
from app.bot.screens import clear_flow, delete_panel, flow_screen
from app.bot.states import RepVoteFlow
from app.errors import DomainError
from app.i18n import t
from app.services import Service


async def begin(
    message: Message, state: FSMContext, service: Service, actor: int, target: str, value: int
):
    user = await service.resolve(target)
    if user.telegram_id == actor:
        raise DomainError("self_rep")
    await clear_flow(state)
    await state.set_state(RepVoteFlow.comment)
    await state.update_data(rep_target=f"u:{user.id}", rep_value=value, rep_nonce=uuid4().hex)
    giver = await service.resolve(str(actor))
    text = p.rep_comment_prompt(user, giver, value, group=message.chat.type != "private")
    if message.chat.type == "private":
        await flow_screen(message, state, text, reply_markup=kb.navigation())
    else:
        previous = (await state.get_data()).get("screen_message_id")
        prompt = await message.answer(text, reply_markup=ForceReply(selective=True))
        if previous and previous != prompt.message_id:
            await delete_panel(message, previous)
        await state.update_data(
            rep_prompt_id=prompt.message_id, screen_message_id=prompt.message_id
        )


def register_vote_handlers(router: Router):
    @router.message(RepVoteFlow.comment)
    async def comment_input(message: Message, state: FSMContext, service: Service):
        if not message.from_user:
            return
        draft = await state.get_data()
        if message.chat.type != "private" and (
            not message.reply_to_message
            or message.reply_to_message.message_id != draft.get("rep_prompt_id")
        ):
            return
        try:
            comment = service.validate_rep_comment(message.text)
        except DomainError:
            if message.chat.type == "private":
                await flow_screen(
                    message, state, t("error.rep_comment"), reply_markup=kb.navigation()
                )
            else:
                prompt = await message.answer(
                    t("error.rep_comment"),
                    reply_markup=ForceReply(selective=True),
                    reply_parameters=ReplyParameters(message_id=message.message_id),
                )
                if draft.get("rep_prompt_id"):
                    await delete_panel(message, draft["rep_prompt_id"])
                await state.update_data(
                    rep_prompt_id=prompt.message_id, screen_message_id=prompt.message_id
                )
            return
        if (
            not draft.get("rep_target")
            or not draft.get("rep_nonce")
            or draft.get("rep_value") not in (-1, 1)
        ):
            raise DomainError("stale_callback")
        try:
            data = await service.vote(
                message.from_user.id,
                draft["rep_target"],
                draft["rep_value"],
                message.chat.id,
                "rep-draft:" + draft["rep_nonce"],
                comment=comment,
            )
        except DomainError as error:
            await clear_flow(state)
            await flow_screen(
                message,
                state,
                p.error(error.code),
                reply_markup=kb.back() if message.chat.type == "private" else None,
            )
            return
        await clear_flow(state)
        await flow_screen(
            message,
            state,
            p.reputation_pending(data),
            reply_markup=kb.back() if message.chat.type == "private" else None,
        )
