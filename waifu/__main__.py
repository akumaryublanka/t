"""
waifu/__main__.py  —  Entry point.
Run with:  python -m waifu
"""
import asyncio
import importlib
import os
import signal
from contextlib import suppress

from aiohttp import web

from waifu import ALL_MODULES, LOGGER


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  KEEP-ALIVE SERVER
#  Render Web Service ke liye port 8080 open karna
#  zaroori hai — bina iske Render process kill karta hai
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def keep_alive():
    async def handle(request):
        return web.Response(
            text="<h2>Waifu Bot is alive!</h2>",
            content_type="text/html"
        )

    async def health(request):
        return web.json_response({"status": "ok", "bot": "WaifuBot"})

    webapp = web.Application(client_max_size=25 * 1024 * 1024)
    from waifu.miniapp_api import register_miniapp_routes
    register_miniapp_routes(webapp)
    webapp.router.add_route("OPTIONS", "/{tail:.*}", lambda request: web.Response(status=204))
    webapp.router.add_get("/", handle)
    webapp.router.add_get("/health", health)

    runner = web.AppRunner(webapp)
    await runner.setup()

    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    LOGGER.info(f"[KeepAlive] HTTP server started on port {port}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MIGRATIONS & POST-INIT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _migrate_indexes() -> None:
    """
    Drop legacy indexes from the old bot schema that conflict with new code.
    Safe to call on every startup — silently skips if they don't exist.
    """
    from waifu import user_collection
    try:
        await user_collection.drop_index("user_id_1")
        LOGGER.info("Migration: dropped stale index users.user_id_1")
    except Exception:
        pass


async def _post_init(application) -> None:
    """Runs once after the Application starts — profile, migrations, indexes, scheduler."""
    from waifu.profile_setup import setup_bot_profile
    from waifu.modules.inlinequery import create_indexes
    from waifu.modules.waifu_drop import start_scheduler
    from waifu.modules.music import start_music

    await setup_bot_profile(application.bot)
    await _migrate_indexes()
    await create_indexes()
    LOGGER.info("DB indexes ensured.")
    start_scheduler(application.bot)
    LOGGER.info("Drop scheduler started.")
    await start_music()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _run_bot() -> None:
    """Start keep-alive server, then run bot via async polling."""
    # ── Step 1: Port PEHLE bind karo ─────────────────
    await keep_alive()

    # ── Step 2: Modules load karo ────────────────────
    LOGGER.info("Loading %d module(s)…", len(ALL_MODULES))
    for name in ALL_MODULES:
        try:
            importlib.import_module(f"waifu.modules.{name}")
            LOGGER.debug("  ✓ %s", name)
        except Exception as exc:
            LOGGER.error("  ✗ %s — %s", name, exc, exc_info=True)
            raise
    LOGGER.info("All modules loaded.")

    # ── Step 3: Bot initialize karo ──────────────────
    from waifu import application
    application.post_init = _post_init

    async def _error_handler(update, context):
        err = context.error
        LOGGER.error(
            "Unhandled exception while processing update %s: %r",
            getattr(update, "update_id", "unknown"),
            err,
            exc_info=(type(err), err, err.__traceback__) if err else None,
        )

    application.add_error_handler(_error_handler)

    # ── Step 4: Async polling start karo ─────────────
    LOGGER.info("Starting bot (async polling)…")
    async with application:
        await application.initialize()
        # run_polling() normally invokes post_init automatically. This app
        # uses a manual async lifecycle, so run the startup hook explicitly.
        await _post_init(application)
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        LOGGER.info("Bot is running!")

        # Stop signal ka wait karo
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGABRT):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)

        await stop_event.wait()

        # Cleanup
        LOGGER.info("Stopping bot…")
        await application.updater.stop()
        await application.stop()
        from waifu.modules.music import stop_music
        await stop_music()


def main() -> None:
    try:
        # Create and manage the event loop explicitly. Python 3.14 no longer
        # creates a default loop for asyncio.get_event_loop() in this context.
        asyncio.run(_run_bot())
    except KeyboardInterrupt:
        LOGGER.info("Bot stopped by user.")


if __name__ == "__main__":
    main()
