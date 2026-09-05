from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pricewatch.runtime_models import UserProductSummary
from pricewatch.search_plan import SearchPlan


@dataclass(frozen=True, slots=True)
class TelegramView:
    text: str
    reply_markup: dict[str, Any] | None = None


def _inline_keyboard(*rows: list[dict[str, str]]) -> dict[str, Any]:
    return {"inline_keyboard": list(rows)}


def _format_rub(value: Decimal | str) -> str:
    number = Decimal(str(value))
    integral = int(number.quantize(Decimal("1")))
    return f"{integral:,}".replace(",", " ") + " ₽"


def _marketplace_name(value: str | None) -> str:
    names = {"ozon": "Ozon", "wildberries": "Wildberries"}
    if value is None:
        return ""
    return names.get(value.casefold(), value)


def _human_attribute_value(key: str, value: str) -> str:
    normalized = value.strip()
    unit_names = {"gb": "ГБ", "tb": "ТБ", "mb": "МБ"}
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(gb|tb|mb)", normalized, re.IGNORECASE)
    if match:
        return f"{match.group(1)} {unit_names[match.group(2).casefold()]}"
    if key.casefold() in {"brand", "model", "edition", "generation"}:
        return normalized.title()
    return normalized


def render_start() -> TelegramView:
    return TelegramView(
        text=(
            "👋 PriceWatch\n\n"
            "Отправь название товара, который хочешь купить.\n\n"
            "Например:\nXiaomi Pad 7 8/256\n\n"
            "Я буду проверять Ozon и Wildberries\n"
            "и напишу, когда цена станет самой низкой\n"
            "за последние 7 дней."
        ),
        reply_markup=_inline_keyboard(
            [{"text": "➕ Добавить товар", "callback_data": "add"}],
            [{"text": "📦 Мои товары", "callback_data": "my"}],
        ),
    )


def render_confirmation(plan: SearchPlan, *, confirmation_id: str) -> TelegramView:
    attributes = []
    display_keys = {
        "brand": "Бренд",
        "model": "Модель",
        "ram": "RAM",
        "storage": "Память",
        "capacity": "Объём",
        "size": "Размер",
        "generation": "Поколение",
        "edition": "Версия",
    }
    for key, value in plan.identity_attributes.items():
        label = display_keys.get(key.casefold(), key)
        attributes.append(f"• {label}: {_human_attribute_value(key, value)}")
    details = "\n".join(attributes)
    if details:
        details = f"\n{details}\n"

    return TelegramView(
        text=(
            "🔎 Я понял товар так:\n\n"
            f"{plan.canonical_name}\n"
            f"{details}\n"
            "Буду искать именно эту версию,\n"
            "не смешивая её с похожими моделями,\n"
            "другими объёмами и аксессуарами."
        ),
        reply_markup=_inline_keyboard(
            [{"text": "✅ Всё верно", "callback_data": f"confirm:{confirmation_id}"}],
            [{"text": "✏️ Изменить", "callback_data": f"correct:{confirmation_id}"}],
            [{"text": "❌ Отмена", "callback_data": f"cancel:{confirmation_id}"}],
        ),
    )


def render_tracking_card(summary: UserProductSummary) -> TelegramView:
    subscription = summary.subscription
    if summary.public_price is None:
        price_block = "Ищу актуальные предложения…"
    else:
        price_block = (
            "Сейчас лучшая цена:\n"
            f"{_format_rub(summary.public_price)} • {_marketplace_name(summary.marketplace)}"
        )
        if summary.seven_day_min_price is not None:
            price_block += (
                "\n\nМинимум за 7 дней:\n"
                f"{_format_rub(summary.seven_day_min_price)}"
            )

    action = (
        {"text": "▶️ Возобновить", "callback_data": f"resume:{subscription.id}"}
        if subscription.status == "paused"
        else {"text": "⏸ Остановить", "callback_data": f"pause:{subscription.id}"}
    )
    rows: list[list[dict[str, str]]] = []
    if summary.listing_url and summary.public_price:
        rows.append(
            [
                {
                    "text": "🛒 Открыть товар",
                    "url": summary.listing_url,
                }
            ]
        )
    rows.append([{"text": "📊 История", "callback_data": f"history:{subscription.id}"}])
    rows.append([action])

    return TelegramView(
        text=(
            "✅ Отслеживание включено\n\n"
            f"{summary.product.canonical_name}\n\n"
            f"{price_block}\n\n"
            "Проверка: примерно каждые 4 минуты"
        ),
        reply_markup={"inline_keyboard": rows},
    )


def render_product_list(
    products: tuple[UserProductSummary, ...],
    *,
    page: int = 0,
    page_size: int = 8,
) -> TelegramView:
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if not products:
        return TelegramView(
            text="📦 Пока ничего не отслеживается.",
            reply_markup=_inline_keyboard(
                [{"text": "➕ Добавить товар", "callback_data": "add"}]
            ),
        )

    last_page = (len(products) - 1) // page_size
    current_page = min(max(page, 0), last_page)
    start = current_page * page_size
    visible = products[start : start + page_size]

    lines = [f"📦 Отслеживается: {len(products)}", ""]
    buttons: list[list[dict[str, str]]] = []
    for offset, item in enumerate(visible, start=start + 1):
        state = "⏸" if item.subscription.status == "paused" else ""
        lines.append(f"{offset}. {item.product.canonical_name} {state}".rstrip())
        if item.public_price is None:
            lines.append("   ищу актуальную цену")
        else:
            lines.append(
                f"   {_format_rub(item.public_price)} · {_marketplace_name(item.marketplace)}"
            )
            if (
                item.seven_day_min_price is not None
                and Decimal(item.public_price) == Decimal(item.seven_day_min_price)
            ):
                lines.append("   минимум за 7 дней")
        lines.append("")
        buttons.append(
            [
                {
                    "text": f"{offset}. {item.product.canonical_name[:38]}",
                    "callback_data": f"product:{item.subscription.id}",
                }
            ]
        )

    nav: list[dict[str, str]] = []
    if current_page > 0:
        nav.append({"text": "⬅️ Назад", "callback_data": f"my_page:{current_page - 1}"})
    if current_page < last_page:
        nav.append({"text": "Дальше ➡️", "callback_data": f"my_page:{current_page + 1}"})
    if nav:
        buttons.append(nav)
    buttons.append([{"text": "➕ Добавить товар", "callback_data": "add"}])
    return TelegramView(text="\n".join(lines).rstrip(), reply_markup={"inline_keyboard": buttons})


def render_new_low(payload: Mapping[str, Any]) -> TelegramView:
    price = _format_rub(str(payload["public_price"]))
    previous = _format_rub(str(payload["previous_min"]))
    delta = _format_rub(str(payload["delta"]))
    percent = str(payload["delta_percent"]).replace(".", ",")
    marketplace = _marketplace_name(str(payload["marketplace"]))
    url = str(payload["url"])

    extra_price = ""
    conditional = payload.get("conditional_prices")
    if isinstance(conditional, Mapping) and conditional:
        card_price = conditional.get("ozon_card")
        if card_price is not None:
            extra_price = f"\nПо Ozon Карте: {_format_rub(str(card_price))}\n"

    return TelegramView(
        text=(
            "🔥 НОВАЯ МИНИМАЛЬНАЯ ЦЕНА\n\n"
            f"{payload['product_name']}\n\n"
            f"{price} • {marketplace}\n"
            f"{extra_price}\n"
            f"Было минимум: {previous}\n"
            f"Снижение: {delta} · {percent}%\n\n"
            "Цена проверена на карточке товара только что."
        ),
        reply_markup=_inline_keyboard(
            [{"text": "🛒 Открыть товар", "url": url}],
        ),
    )


def render_add_prompt() -> TelegramView:
    return TelegramView(
        text=(
            "Напиши точное название товара одним сообщением.\n\n"
            "Например: Xiaomi Pad 7 8/256"
        )
    )


def render_plan_error() -> TelegramView:
    return TelegramView(
        text=(
            "Не смог надёжно разобрать товар. Проверь название и характеристики и отправь ещё раз."
        )
    )
