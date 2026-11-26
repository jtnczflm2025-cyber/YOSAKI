"""Explicit, loopback-only, network-denied expression lab launcher.

python -m scripts.voice_annotation_lab --enable --data-dir <outside-project>
Frontend: Vite's separate vite.expression-lab.config.ts, port 18889.
"""
import argparse
import asyncio
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable", action="store_true", help="Opt in to synthetic AI only")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()

    def deny_outgoing(event, _arguments):
        if event == "socket.connect":
            raise PermissionError("expression_lab_outbound_network_disabled")

    import uvicorn
    from backend.app.voice.annotation_lab import create_app
    app = create_app(args.data_dir, enabled=args.enable)

    async def serve():
        # Windows builds the event loop's internal loopback wakeup socket pair
        # before this coroutine starts. Block every subsequent outgoing connect.
        sys.addaudithook(deny_outgoing)
        config = uvicorn.Config(app, host="127.0.0.1", port=18888, workers=1, access_log=False)
        await uvicorn.Server(config).serve()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
