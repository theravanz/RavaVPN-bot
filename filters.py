from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from config import get_admin_ids


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        return event.from_user.id in get_admin_ids()
