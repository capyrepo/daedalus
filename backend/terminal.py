import asyncio
import os
import subprocess
import ptyprocess
from fastapi import WebSocket, WebSocketDisconnect


async def terminal_endpoint(websocket: WebSocket):
    if not websocket.session.get("username"):
        await websocket.close(code=4001)
        return

    await websocket.accept()
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"

    # Create detached session to keep server alive (no-op if already exists),
    # then set mouse on so browser scroll wheel events reach tmux scrollback.
    subprocess.run(["tmux", "new-session", "-d", "-s", "main"], capture_output=True, check=False)
    subprocess.run(["tmux", "set-option", "-g", "mouse", "on"], capture_output=True, check=False)

    proc = ptyprocess.PtyProcessUnicode.spawn(
        ["tmux", "new-session", "-A", "-s", "main"],
        dimensions=(24, 80),
        env=env,
    )
    loop = asyncio.get_event_loop()

    async def pty_to_ws():
        while proc.isalive():
            try:
                data = await loop.run_in_executor(None, proc.read, 4096)
                await websocket.send_text(data)
            except (EOFError, OSError):
                break

    async def ws_to_pty():
        async for msg in websocket.iter_text():
            if msg.startswith("\x00resize:"):
                parts = msg[8:].split(":")
                if len(parts) == 2:
                    try:
                        proc.setwinsize(int(parts[0]), int(parts[1]))
                    except (ValueError, OSError):
                        pass
            else:
                proc.write(msg)

    try:
        await asyncio.gather(pty_to_ws(), ws_to_pty())
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if proc.isalive():
            proc.terminate(force=True)
