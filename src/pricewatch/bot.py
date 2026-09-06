from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
    render_cancelled,
    render_confirmation,
    render_correction_prompt,
    render_plan_error,
    render_price_history,
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

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
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

    async def recent_public_prices(
        self,
        product_id: int,
        *,
        since: datetime,
    ) -> list[tuple[Decimal, datetime]]: ...


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

    async def _show(
        self,
        chat_id: int,
        view: TelegramView,
        *,
        message_id: int | None,
    ) -> None:
        if message_id is None:
            await self._send(chat_id, view)
            return
        await self._telegram.edit_message_text(
            chat_id,
            message_id,
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
        raw_message_id = message.get("message_id")
        if not isinstance(telegram_user_id, int) or not isinstance(chat_id, int):
            return
        message_id = raw_message_id if isinstance(raw_message_id, int) else None

        user_id = await self._repository.ensure_user(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
        )
        try:
            await self._dispatch_callback(
                user_id,
                chat_id,
                data,
                message_id=message_id,
            )
        finally:
            await self._telegram.answer_callback_query(callback_id)

    async def _dispatch_callback(
        self,
        user_id: int,
        chat_id: int,
        data: str,
        *,
        message_id: int | None,
    ) -> None:
        if data == "home":
            await self._show(chat_id, render_start(), message_id=message_id)
            return
        if data == "add":
            await self._show(chat_id, render_add_prompt(), message_id=message_id)
            return
        if data == "my":
            products = await self._repository.list_user_products(user_id)
            await self._show(
                chat_id,
                render_product_list(products),
                message_id=message_id,
            )
            return
        if data.startswith("my_page:"):
            _, _, raw_page = data.partition(":")
            try:
                page = int(raw_page)
            except ValueError:
                return
            products = await self._repository.list_user_products(user_id)
            await self._show(
                chat_id,
                render_product_list(products, page=max(0, page)),
                message_id=message_id,
            )
            return
        if data.startswith("history_page:"):
            parts = data.split(":")
            if len(parts) != 3:
                return
            try:
                subscription_id = int(parts[1])
                page = max(0, int(parts[2]))
            except ValueError:
                return
            await self._handle_subscription_action(
                user_id,
                chat_id,
                "history",
                subscription_id,
                message_id=message_id,
                history_page=page,
            )
            return

        action, separator, raw_id = data.partition(":")
        if not separator or not raw_id:
            return
        if action in {"confirm", "correct", "cancel"}:
            await self._handle_confirmation_action(
                user_id,
                chat_id,
                action,
                raw_id,
                message_id=message_id,
            )
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
                message_id=message_id,
            )

    async def _handle_confirmation_action(
        self,
        user_id: int,
        chat_id: int,
        action: str,
        confirmation_id: str,
        *,
        message_id: int | None,
    ) -> None:
        pending = await self._repository.get_pending_confirmation(confirmation_id)
        if pending is None or pending[0] != user_id:
            await self._show(
                chat_id,
                TelegramView(
                    text="Это подтверждение уже недоступно.",
                    reply_markup={
                        "inline_keyboard": [
                            [{"text": "🏠 На главную", "callback_data": "home"}]
                        ]
                    },
                ),
                message_id=message_id,
            )
            return

        if action == "correct":
            await self._repository.get_pending_confirmation(confirmation_id, consume=True)
            await self._show(
                chat_id,
                render_correction_prompt(),
                message_id=message_id,
            )
            return
        if action == "cancel":
            await self._repository.get_pending_confirmation(confirmation_id, consume=True)
            await self._show(chat_id, render_cancelled(), message_id=message_id)
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
        await self._show(
            chat_id,
            render_tracking_card(summary),
            message_id=message_id,
        )

    async def _handle_subscription_action(
        self,
        user_id: int,
        chat_id: int,
        action: str,
        subscription_id: int,
        *,
        message_id: int | None,
        history_page: int = 0,
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
            await self._show(
                chat_id,
                TelegramView(
                    text="Товар не найден в твоих подписках.",
                    reply_markup={
                        "inline_keyboard": [
                            [{"text": "⬅️ Назад", "callback_data": "my"}]
                        ]
                    },
                ),
                message_id=message_id,
            )
            return

        if action == "pause":
            await self._repository.pause_subscription(subscription_id)
        elif action == "resume":
            await self._repository.resume_subscription(subscription_id)
        elif action == "history":
            loader = getattr(self._repository, "recent_public_prices", None)
            if loader is None:
                await self._show(
                    chat_id,
                    TelegramView(
                        text="История показывает проверенные цены за последние 7 дней.",
                        reply_markup={
                            "inline_keyboard": [
                                [
                                    {
                                        "text": "⬅️ Назад",
                                        "callback_data": f"product:{subscription_id}",
                                    }
                                ]
                            ]
                        },
                    ),
                    message_id=message_id,
                )
                return
            prices = await loader(
                current.product.id,
                since=datetime.now(UTC) - timedelta(days=7),
            )
            await self._show(
                chat_id,
                render_price_history(current, prices, page=history_page),
                message_id=message_id,
            )
            return
        elif action == "product":
            await self._show(
                chat_id,
                render_tracking_card(current),
                message_id=message_id,
            )
            return

        refreshed = await self._repository.list_user_products(user_id)
        updated = next(
            item for item in refreshed if item.subscription.id == subscription_id
        )
        await self._show(
            chat_id,
            render_tracking_card(updated),
            message_id=message_id,
        )
