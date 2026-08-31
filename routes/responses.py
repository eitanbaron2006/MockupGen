"""One shape for an error the API returns.

Both blueprints answered failures with the same JSON and their own helper to
build it -- `json_error` in one, `error_response` in the other -- which is one
spelling too many for a contract clients depend on.
"""

from flask import jsonify


def json_error(message: str, status: int):
    """A failed request, as the API always reports one."""
    return jsonify({"success": False, "error": message}), status
