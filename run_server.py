"""
WSGI Production Server Runner for MockupGen
Uses Waitress WSGI server with multi-threading for high concurrency and performance.
"""

import os
import sys
import logging
from waitress import serve
from app import create_app

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("MockupGen.Server")


def main():
    host = os.getenv("HOST", os.getenv("FLASK_HOST", "0.0.0.0"))
    port = int(os.getenv("PORT", os.getenv("FLASK_PORT", "5000")))
    threads = int(os.getenv("WAITRESS_THREADS", "8"))

    app = create_app()

    print("=" * 60)
    print("  🎨 MockupGen WSGI Production Server")
    print(f"  🌐 Listening on: http://{host}:{port}")
    print(f"  🔗 Local access: http://localhost:{port}")
    print(f"  ⚡ Concurrency:  {threads} worker threads")
    print("=" * 60)
    print("Press Ctrl+C to stop the server.\n")

    try:
        serve(
            app,
            host=host,
            port=port,
            threads=threads,
            channel_timeout=120,
            cleanup_interval=30,
            max_request_body_size=1073741824,  # 1GB for large mockup images
        )
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
