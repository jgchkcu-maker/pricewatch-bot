from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import uuid4

from pricewatch.runtime_models import (
    SubscriptionRecord,
    TrackedProductRecord,
    UserProductSummary,
)
from pricewatch.search_plan import SearchPlan
from pricewatch.telegram_views import (
    TelegramView,
    render_add_prompt,
    render_confirmation,
    render_plan_error,
    render_product_list,
    render_start,
    render_tracking_card,
)


class SearchPlanProvider(Protocol):
    async def create_plan(self, text: str) -> SearchPlan: ...


class TelegramSender(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
    ) -> None: ...


class BotRepository(Protocol):
    async def ensure_user(self, *, telegram_user_id: int, chat_id: int) -> int: ...

    async def save_pending_confirmation(
        self,
        *,
        confirmation_id: str,
        user_id: int,
        raw_input: str,
        plan: SearchPlan,
        ttl_minutes: int = 15,
    ) -> None: ...

    async def get_pending_confirmation(
        self,
        confirmation_id: str,
        *,
        consume: bool = False,
    ) -> tuple[int, str, SearchPlan] | None: ...

    async def upsert_tracked_product(self, plan: SearchPlan) -> TrackedProductRecord: ...

    async def subscribe(self, *, user_id: int, product_id: int) -> SubscriptionRecord: ...

    async def list_user_products(self, user_id: int) -> tuple[UserProductSummary, ...]: ...

    async def pause_subscription(self, subscription_id: int) -> None: ...

    async def resume_subscription(self, subscription_id: int) -> None: ...


class TelegramBotApp:
    def __init__(
        self,
        *,
        repository: BotRepository,
        plan_provider: SearchPlanProvider,
        telegram: TelegramSender,
    ) -> None:
        self._repository = repository
        self._plan_provider = plan_provider
        self._telegram = telegram

    async def _send(self, chat_id: int, view: TelegramView) -> None:
        await self._telegram.send_message(
            chat_id,
            view.text,
            reply_markup=view.reply_markup,
        )

    async def handle_update(self, update: Mapping[str, Any]) -> None:
        message = update.get("message")
        if isinstance(message, Mapping):
            await self._handle_message(message)
            return
        callback = update.get("callback_query")
        if isinstance(callback, Mapping):
            await self._handle_callback(callback)

    async def _handle_message(self, message: Mapping[str, Any]) -> None:
        sender = message.get("from")
        chat = message.get("chat")
        text = message.get("text")
        if not isinstance(sender, Mapping) or not isinstance(chat, Mapping):
            return
        if not isinstance(text, str):
            return
        telegram_user_id = sender.get("id")
        chat_id = chat.get("id")
        if not isinstance(telegram_user_id, int) or not isinstance(chat_id, int):
            return

        user_id = await self._repository.ensure_user(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
        )
        normalized = text.strip()
        if normalized == "/start":
            await self._send(chat_id, render_start())
            return
        if normalized in {"/my", "/products"}:
            products = await self._repository.list_user_products(user_id)
            await self._send(chat_id, render_product_list(products))
            return
        if normalized.startswith("/"):
            await self._send(chat_id, render_start())
            return
        if not normalized:
            return

        try:
            plan = await self._plan_provider.create_plan(normalized)
        except Exception:
            await self._send(chat_id, render_plan_error())
            return
        confirmation_id = uuid4().hex[:16]
        await self._repository.save_pending_confirmation(
            confirmation_id=confirmation_id,
            user_id=user_id,
            raw_input=normalized,
            plan=plan,
        )
        await self._send(
            chat_id,
            render_confirmation(plan, confirmation_id=confirmation_id),
        )

    async def _handle_callback(self, callback: Mapping[str, Any]) -> None:
        callback_id = callback.get("id")
        sender = callback.get("from")
        message = callback.get("message")
        data = callback.get("data")
        if not isinstance(callback_id, str) or not isinstance(data, str):
            return
        if not isinstance(sender, Mapping) or not isinstance(message, Mapping):
            return
        chat = message.get("chat")
        if not isinstance(chat, Mapping):
            return
        telegram_user_id = sender.get("id")
        chat_id = chat.get("id")
        if not isinstance(telegram_user_id, int) or not isinstance(chat_id, int):
            return

        user_id = await self._repository.ensure_user(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
        )
        try:
            await self._dispatch_callback(user_id, chat_id, data)
        finally:
            await self._telegram.answer_callback_query(callback_id)

    async def _dispatch_callback(self, user_id: int, chat_id: int, data: str) -> None:
        if data == "add":
            await self._send(chat_id, render_add_prompt())
            return
        if data == "my":
            products = await self._repository.list_user_products(user_id)
            await self._send(chat_id, render_product_list(products))
            return
        if data.startswith("my_page:"):
            _, _, raw_page = data.partition(":")
            try:
                page = int(raw_page)
            except ValueError:
                return
            products = await self._repository.list_user_products(user_id)
            await self._send(chat_id, render_product_list(products, page=max(0, page)))
            return

        action, separator, raw_id = data.partition(":")
        if not separator or not raw_id:
            return
        if action in {"confirm", "correct", "cancel"}:
            await self._handle_confirmation_action(user_id, chat_id, action, raw_id)
            return
        if action in {"pause", "resume", "product", "history"}:
            try:
                subscription_id = int(raw_id)
            except ValueError:
                return
            await self._handle_subscription_action(
                user_id,
                chat_id,
                action,
                subscription_id,
            )

    async def _handle_confirmation_action(
        self,
        user_id: int,
        chat_id: int,
        action: str,
        confirmation_id: str,
    ) -> None:
        pending = await self._repository.get_pending_confirmation(confirmation_id)
        if pending is None or pending[0] != user_id:
            await self._telegram.send_message(chat_id, "Это подтверждение уже недоступно.")
            return

        if action == "correct":
            await self._repository.get_pending_confirmation(confirmation_id, consume=True)
            await self._telegram.send_message(
                chat_id,
                "Отправь исправленное название товара одним сообщением.",
            )
            return
        if action == "cancel":
            await self._repository.get_pending_confirmation(confirmation_id, consume=True)
            await self._telegram.send_message(chat_id, "Добавление товара отменено.")
            return

        consumed = await self._repository.get_pending_confirmation(
            confirmation_id,
            consume=True,
        )
        if consumed is None:
            return
        plan = consumed[2]
        product = await self._repository.upsert_tracked_product(plan)
        subscription = await self._repository.subscribe(
            user_id=user_id,
            product_id=product.id,
        )
        summary = UserProductSummary(subscription=subscription, product=product)
        await self._send(chat_id, render_tracking_card(summary))

    async def _handle_subscription_action(
        self,
        user_id: int,
        chat_id: int,
        action: str,
        subscription_id: int,
    ) -> None:
        products = await self._repository.list_user_products(user_id)
        current = next(
            (
                item
                for item in products
                if item.subscription.id == subscription_id
                and item.subscription.user_id == user_id
            ),
            None,
        )
        if current is None:
            await self._telegram.send_message(chat_id, "Товар не найден в твоих подписках.")
            return

        if action == "pause":
            await self._repository.pause_subscription(subscription_id)
        elif action == "resume":
            await self._repository.resume_subscription(subscription_id)
        elif action == "history":
            await self._telegram.send_message(
                chat_id,
                "История показывает проверенные изменения цены за последние 7 дней.",
            )
            return
        elif action == "product":
            await self._send(chat_id, render_tracking_card(current))
            return

        refreshed = await self._repository.list_user_products(user_id)
        updated = next(
            item for item in refreshed if item.subscription.id == subscription_id
        )
        await self._send(chat_id, render_tracking_card(updated))
