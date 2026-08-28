"""One-time database restore endpoint -- moves a local desktop timr.db onto
a fresh cloud deploy (Render, etc.) without needing shell/SSH/git access.

Inert by default: 404s unless the TIMR_RESTORE_TOKEN environment variable
is set on the server. Set it temporarily right after a fresh deploy, visit
/setup/restore-db?token=<that value>, upload the .db file exported from the
desktop app, then delete the TIMR_RESTORE_TOKEN environment variable --
there's no reason to leave this reachable day-to-day. Not linked from any
nav menu.
"""
import os
import shutil

from flask import Blueprint, request, abort, render_template_string

import config

bp = Blueprint("restore", __name__, url_prefix="/setup")

_TOKEN_ENV = "TIMR_RESTORE_TOKEN"

_FORM_HTML = """
<!doctype html><html><body style="font-family:sans-serif; max-width:520px; margin:60px auto;">
<h2>TIM RENO &mdash; restore database</h2>
<p>Uploads a .db file to replace the live database at <code>{{ db_path }}</code>.
This OVERWRITES whatever is currently live on this server, so only use it
once, right after a fresh deploy, to load your existing desktop database.</p>
<form method="post" enctype="multipart/form-data">
  <input type="hidden" name="token" value="{{ token }}">
  <p><input type="file" name="dbfile" accept=".db" required></p>
  <p><button type="submit">Upload and restore</button></p>
</form>
{% if message %}<p><b>{{ message }}</b></p>{% endif %}
</body></html>
"""


@bp.route("/restore-db", methods=["GET", "POST"])
def restore_db():
    expected = os.environ.get(_TOKEN_ENV)
    if not expected:
        abort(404)  # inert unless explicitly enabled via env var
    submitted = request.values.get("token")
    if submitted != expected:
        abort(403)

    if request.method == "GET":
        return render_template_string(_FORM_HTML, db_path=config.DATABASE_PATH, token=expected, message=None)

    f = request.files.get("dbfile")
    if not f or not f.filename:
        return render_template_string(
            _FORM_HTML, db_path=config.DATABASE_PATH, token=expected, message="Choose a .db file first."
        ), 400

    tmp_path = config.DATABASE_PATH + ".upload-tmp"
    f.save(tmp_path)
    for suffix in ("", "-wal", "-shm"):
        stale = config.DATABASE_PATH + suffix
        if os.path.exists(stale):
            os.remove(stale)
    shutil.move(tmp_path, config.DATABASE_PATH)

    return render_template_string(
        _FORM_HTML, db_path=config.DATABASE_PATH, token=expected,
        message="Database restored. Reload the app and log in with your usual admin account. "
                "Now remove the TIMR_RESTORE_TOKEN environment variable in Render -- you're done with it.",
    )
