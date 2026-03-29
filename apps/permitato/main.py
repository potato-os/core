"""Permitato health check process — minimal socket server for the app supervisor."""

import asyncio
import json
import logging
import os
import signal
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("permitato")

_shutdown = asyncio.Event()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        async for line in reader:
            raw = line.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type", "")
            if msg_type == "health_check":
                response = json.dumps({"type": "health", "status": "ok"}) + "\n"
                writer.write(response.encode())
                await writer.drain()
            elif msg_type == "stop":
                logger.info("Received stop command")
                _shutdown.set()
                break
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def main() -> None:
    socket_path = os.environ.get("POTATO_SOCKET_PATH", "")
    if not socket_path:
        logger.error("POTATO_SOCKET_PATH not set")
        sys.exit(1)

    app_id = os.environ.get("POTATO_APP_ID", "permitato")
    logger.info("Starting %s (socket=%s)", app_id, socket_path)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown.set)

    server = await asyncio.start_unix_server(handle_client, path=socket_path)
    logger.info("%s ready", app_id)

    async with server:
        await _shutdown.wait()

    logger.info("%s shutting down", app_id)

    if os.path.exists(socket_path):
        os.unlink(socket_path)


if __name__ == "__main__":
    asyncio.run(main())
