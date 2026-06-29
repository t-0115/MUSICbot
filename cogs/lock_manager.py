# cogs/lock_manager.py
import asyncio

__all__ = ["message_lock_manager"]


class _LockContext:
    """MessageLockManager を async with で使うためのコンテキストマネージャー"""

    def __init__(self, manager: "MessageLockManager", message_id: int):
        self._manager = manager
        self._message_id = message_id

    async def __aenter__(self):
        await self._manager._acquire(self._message_id)

    async def __aexit__(self, *args):
        self._manager._release(self._message_id)


class MessageLockManager:
    """メッセージIDごとに asyncio.Lock を管理するクラス。
    同一メッセージへの並行操作（参加の同時押し等）を防ぐ。

    使い方::

        async with message_lock_manager.get_context(message_id):
            # ここがクリティカルセクション
            ...
    """

    def __init__(self):
        self._locks: dict[int, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()  # _locks 辞書自体への同時アクセスを防ぐ

    def get_context(self, message_id: int) -> _LockContext:
        """async with で使えるコンテキストマネージャーを返す"""
        return _LockContext(self, message_id)

    async def _acquire(self, message_id: int) -> None:
        async with self._meta_lock:
            if message_id not in self._locks:
                self._locks[message_id] = asyncio.Lock()
        await self._locks[message_id].acquire()

    def _release(self, message_id: int) -> None:
        if message_id in self._locks:
            self._locks[message_id].release()


# Bot 全体で共有するシングルトン
# 各 Cog からは `from .lock_manager import message_lock_manager` でインポートする
message_lock_manager = MessageLockManager()
