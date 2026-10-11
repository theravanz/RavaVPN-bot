from database import Settings


def get_main_menu_text(s: Settings) -> str:
    locations = "\n".join(f"▫️ {line}" for line in s.locations.splitlines() if line.strip())
    return (
        f"👾 <b>{s.bot_name}</b> — {s.bot_tagline}\n\n"
        f"🌎 <b>Локации в подписке:</b>\n{locations}\n\n"
        f"<b>Выберите действие:</b>"
    )


def get_stub_text(button_name: str) -> str:
    return f"🚧 Раздел <b>{button_name}</b> в разработке.\n\nСкоро здесь появится функционал 😉"

