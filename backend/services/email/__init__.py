"""Email delivery — provider-agnostic (plan §4 EmailProvider).

Dev logs to the console (no SMTP creds needed); prod sends via Resend. Routers
import only ``get_email_provider`` / the ``send_*`` helpers, never an SDK.
"""
from __future__ import annotations

from functools import lru_cache

from core.config import settings
from services.email.base import EmailMessage, EmailProvider

__all__ = [
    "EmailMessage",
    "EmailProvider",
    "get_email_provider",
    "send_verification_email",
    "send_password_reset_email",
    "send_welcome_email",
    "send_receipt_email",
    "send_reminder_email",
    "send_digest_email",
    "send_inheritance_email",
]


@lru_cache
def get_email_provider() -> EmailProvider:
    provider = settings.email_provider
    if provider == "resend":
        from services.email.resend_provider import ResendProvider

        return ResendProvider()
    # "console" (default) and any not-yet-implemented provider fall back to log.
    from services.email.console_provider import ConsoleProvider

    return ConsoleProvider()


def _link(path: str, token: str) -> str:
    base = settings.public_url.rstrip("/")
    return f"{base}{path}?token={token}"


async def send_verification_email(*, to: str, token: str) -> None:
    link = _link("/verify-email", token)
    await get_email_provider().send(
        EmailMessage(
            to=to,
            subject="КАПИТАЛЬ — подтвердите адрес почты",
            text=(
                "Подтвердите адрес электронной почты, чтобы активировать аккаунт:\n\n"
                f"{link}\n\nСсылка действует ограниченное время. Если вы не "
                "регистрировались — просто проигнорируйте это письмо."
            ),
            html=(
                "<p>Подтвердите адрес электронной почты, чтобы активировать "
                f'аккаунт:</p><p><a href="{link}">Подтвердить почту</a></p>'
                "<p style='color:#888;font-size:12px'>Ссылка действует ограниченное "
                "время. Если вы не регистрировались — проигнорируйте это письмо.</p>"
            ),
        )
    )


async def send_welcome_email(*, to: str, name: str | None = None) -> None:
    """Activation/welcome email — sent once the address is verified."""
    app_url = settings.public_url.rstrip("/")
    greeting = f"Привет, {name}!" if name else "Привет!"
    await get_email_provider().send(
        EmailMessage(
            to=to,
            subject="Добро пожаловать в КАПИТАЛЬ 🎉",
            text=(
                f"{greeting}\n\nВаш аккаунт активирован. КАПИТАЛЬ — приватный "
                "трекер капитала с AI-советником: добавьте активы текстом, голосом "
                "или фото — мы посчитаем стоимость в любой валюте и подскажем "
                f"персональные инсайты.\n\nНачните с первого ввода: {app_url}/input\n\n"
                "Это не инвестиционная рекомендация — только аналитика ваших данных."
            ),
            html=(
                f"<p>{greeting}</p><p>Ваш аккаунт активирован. <b>КАПИТАЛЬ</b> — "
                "приватный трекер капитала с AI-советником: добавьте активы текстом, "
                "голосом или фото — мы посчитаем стоимость в любой валюте и подскажем "
                f'персональные инсайты.</p><p><a href="{app_url}/input">Сделать первый '
                "ввод</a></p><p style='color:#888;font-size:12px'>Это не инвестиционная "
                "рекомендация — только аналитика ваших данных.</p>"
            ),
        )
    )


async def send_receipt_email(
    *,
    to: str,
    plan: str,
    amount: str | None,
    currency: str | None,
    expires_at: str | None = None,
) -> None:
    """Payment receipt — sent after a successful (active) payment webhook."""
    app_url = settings.public_url.rstrip("/")
    amount_line = ""
    if amount:
        cur = (currency or "USD").upper()
        amount_line = f"Сумма: {amount} {cur}\n"
    until_line = f"Доступ активен до: {expires_at}\n" if expires_at else ""
    plan_title = plan.capitalize()
    await get_email_provider().send(
        EmailMessage(
            to=to,
            subject=f"КАПИТАЛЬ — оплата получена ({plan_title})",
            text=(
                f"Спасибо за оплату! Тариф {plan_title} активирован.\n\n"
                f"{amount_line}{until_line}\n"
                f"Управление подпиской: {app_url}/settings\n\n"
                "Это письмо — подтверждение платежа, сохраните его для отчётности."
            ),
            html=(
                f"<p>Спасибо за оплату! Тариф <b>{plan_title}</b> активирован.</p>"
                f"<p>{('Сумма: ' + amount + ' ' + (currency or 'USD').upper() + '<br>') if amount else ''}"
                f"{('Доступ активен до: ' + expires_at) if expires_at else ''}</p>"
                f'<p><a href="{app_url}/settings">Управление подпиской</a></p>'
                "<p style='color:#888;font-size:12px'>Это письмо — подтверждение "
                "платежа, сохраните его для отчётности.</p>"
            ),
        )
    )


async def send_reminder_email(*, to: str, name: str | None, days: int, lang: str = "ru") -> None:
    """Nudge to refresh the portfolio when the last snapshot is stale."""
    en = lang == "en"
    app_url = settings.public_url.rstrip("/")
    greeting = (f"Hi, {name}!" if name else "Hi!") if en else (f"Привет, {name}!" if name else "Привет!")
    subject = "KAPITAL — time to update your portfolio" if en else "КАПИТАЛЬ — пора обновить портфель"
    if en:
        text = (
            f"{greeting}\n\nYou haven't updated your wealth for {days} days. A fresh "
            f"snapshot keeps the trend chart and analytics accurate.\n\n"
            f"Update in a minute: {app_url}/input\n"
        )
        html = (
            f"<p>{greeting}</p><p>You haven't updated your wealth for <b>{days} days.</b> "
            "A fresh snapshot keeps the trend chart and analytics accurate.</p>"
            f'<p><a href="{app_url}/input">Update in a minute</a></p>'
        )
    else:
        text = (
            f"{greeting}\n\nВы не обновляли капитал уже {days} дн. Свежий "
            f"снимок делает график динамики и аналитику точнее.\n\n"
            f"Обновить за минуту: {app_url}/input\n"
        )
        html = (
            f"<p>{greeting}</p><p>Вы не обновляли капитал уже <b>{days} дн.</b> "
            "Свежий снимок делает график динамики и аналитику точнее.</p>"
            f'<p><a href="{app_url}/input">Обновить за минуту</a></p>'
        )
    await get_email_provider().send(EmailMessage(to=to, subject=subject, text=text, html=html))


async def send_digest_email(
    *,
    to: str,
    name: str | None,
    current_usd: str,
    change_usd: str,
    change_pct: str | None,
    lang: str = "ru",
) -> None:
    """Weekly net-worth digest."""
    en = lang == "en"
    app_url = settings.public_url.rstrip("/")
    greeting = (f"Hi, {name}!" if name else "Hi!") if en else (f"Привет, {name}!" if name else "Привет!")
    sign = "+" if not change_usd.startswith("-") else ""
    pct = f" ({sign}{change_pct}%)" if change_pct else ""
    subject = "KAPITAL — your week in review" if en else "КАПИТАЛЬ — итоги недели"
    if en:
        text = (
            f"{greeting}\n\nYour net worth: ${current_usd}\n"
            f"Change this week: {sign}${change_usd}{pct}\n\n"
            f"More: {app_url}/\n\n"
            "This is analysis of your data, not investment advice."
        )
        html = (
            f"<p>{greeting}</p><p>Your net worth: <b>${current_usd}</b><br>"
            f"Change this week: {sign}${change_usd}{pct}</p>"
            f'<p><a href="{app_url}/">Open dashboard</a></p>'
            "<p style='color:#888;font-size:12px'>This is analysis of your data, "
            "not investment advice.</p>"
        )
    else:
        text = (
            f"{greeting}\n\nВаш капитал: ${current_usd}\n"
            f"Изменение за неделю: {sign}${change_usd}{pct}\n\n"
            f"Подробнее: {app_url}/\n\n"
            "Это аналитика ваших данных, не инвестиционная рекомендация."
        )
        html = (
            f"<p>{greeting}</p><p>Ваш капитал: <b>${current_usd}</b><br>"
            f"Изменение за неделю: {sign}${change_usd}{pct}</p>"
            f'<p><a href="{app_url}/">Открыть дашборд</a></p>'
            "<p style='color:#888;font-size:12px'>Это аналитика ваших данных, "
            "не инвестиционная рекомендация.</p>"
        )
    await get_email_provider().send(EmailMessage(to=to, subject=subject, text=text, html=html))


async def send_inheritance_email(
    *,
    to: str,
    owner_name: str | None,
    owner_email: str,
    current_usd: str | None,
    days: int,
    lang: str = "ru",
) -> None:
    """Dead-man's-switch: notify a trusted contact after prolonged inactivity."""
    en = lang == "en"
    who = owner_name or owner_email
    if en:
        worth = f"Estimated net worth at last snapshot: ${current_usd}\n" if current_usd else ""
        await get_email_provider().send(EmailMessage(
            to=to,
            subject="KAPITAL — trusted-contact notice",
            text=(
                f"Hello.\n\n{who} listed you as a trusted contact in KAPITAL and "
                f"hasn't signed in for {days} days.\n\n{worth}\n"
                "If you know the owner, please reach out. This is an automated notice; "
                "it does not grant direct access to the account.\n"
            ),
            html=(
                f"<p>Hello.</p><p><b>{who}</b> listed you as a trusted contact in "
                f"<b>KAPITAL</b> and hasn't signed in for <b>{days} days.</b></p>"
                f"<p>{('Estimated net worth at last snapshot: $' + current_usd) if current_usd else ''}</p>"
                "<p style='color:#888;font-size:12px'>Automated notice; it does not "
                "grant direct access to the account.</p>"
            ),
        ))
        return
    worth = f"Оценочный капитал на последний снимок: ${current_usd}\n" if current_usd else ""
    await get_email_provider().send(
        EmailMessage(
            to=to,
            subject="КАПИТАЛЬ — доверенное уведомление",
            text=(
                f"Здравствуйте.\n\n{who} указал(а) вас доверенным лицом в КАПИТАЛЬ "
                f"и не заходил(а) в аккаунт уже {days} дн.\n\n{worth}\n"
                "Если вы знаете владельца — свяжитесь с ним. Это автоматическое "
                "уведомление, оно не даёт прямого доступа к аккаунту.\n"
            ),
            html=(
                f"<p>Здравствуйте.</p><p><b>{who}</b> указал(а) вас доверенным лицом "
                f"в <b>КАПИТАЛЬ</b> и не заходил(а) в аккаунт уже <b>{days} дн.</b></p>"
                f"<p>{('Оценочный капитал на последний снимок: $' + current_usd) if current_usd else ''}</p>"
                "<p style='color:#888;font-size:12px'>Автоматическое уведомление; "
                "оно не даёт прямого доступа к аккаунту.</p>"
            ),
        )
    )


async def send_password_reset_email(*, to: str, token: str) -> None:
    link = _link("/reset-password", token)
    await get_email_provider().send(
        EmailMessage(
            to=to,
            subject="КАПИТАЛЬ — сброс пароля",
            text=(
                "Вы запросили сброс пароля. Перейдите по ссылке, чтобы задать "
                f"новый пароль:\n\n{link}\n\nЕсли вы этого не делали — "
                "проигнорируйте письмо, пароль останется прежним."
            ),
            html=(
                "<p>Вы запросили сброс пароля. Перейдите по ссылке, чтобы задать "
                f'новый пароль:</p><p><a href="{link}">Сбросить пароль</a></p>'
                "<p style='color:#888;font-size:12px'>Если вы этого не делали — "
                "проигнорируйте письмо.</p>"
            ),
        )
    )
