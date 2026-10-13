"""
serve.py — Production entry point.

Runs MediAI on gevent's WSGI server (production-grade, handles many
concurrent connections and WebSockets for video calls) instead of the
Flask development server.

    python serve.py
"""

# gevent must patch the standard library before anything else is imported
from gevent import monkey
monkey.patch_all()

import os
os.environ.setdefault("MEDIAI_ASYNC_MODE", "gevent")

import config                      # noqa: E402
from app import app, socketio      # noqa: E402

if __name__ == "__main__":
    config.log.info("MediAI (%s) serving on http://%s:%s", config.ENV, config.HOST, config.PORT)
    socketio.run(app, host=config.HOST, port=config.PORT, debug=False,
                 use_reloader=False, log_output=config.DEBUG)
