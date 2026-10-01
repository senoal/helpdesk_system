import os

from waitress import serve

from servicehub import create_app


app = create_app()


if __name__ == "__main__":
    serve(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        threads=int(os.getenv("WAITRESS_THREADS", "8")),
    )
