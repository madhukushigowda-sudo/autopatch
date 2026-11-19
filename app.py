"""Entry point:  python app.py"""
import os

from autopatch import create_app

app = create_app()

if __name__ == "__main__":
    # Bound to localhost only: later stages run project tests in subprocesses,
    # so this server must not be reachable from other machines.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
