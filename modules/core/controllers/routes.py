# -----------------------------------------------------------------------------
# sparQ
#
# Description:
#     Core module route handlers and view logic. Implements authentication,
#     user management, and system settings functionality.
#
# Copyright (c) 2025 remarQable LLC
#
# This software is released under an open-source license.
# See the LICENSE file for details.
# -----------------------------------------------------------------------------

import importlib
import os
import signal
import sys
from functools import wraps

from flask import Blueprint
from flask import current_app
from flask import flash
from flask import g
from flask import jsonify
from flask import redirect
from flask import render_template
from flask import request
from flask import session
from flask import url_for
from flask_login import current_user
from flask_login import login_required
from flask_login import login_user
from flask_login import logout_user

from system.db.database import db

from ..models.user import User
from ..models.user_setting import UserSetting

# Create blueprint
blueprint = Blueprint(
    "core_bp", __name__, template_folder="../views/templates", static_folder="../views/assets"
)


def admin_required(f):
    """Decorator to require admin access for a route"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required", "error")
            return redirect(url_for("core_bp.login"))
        return f(*args, **kwargs)

    return decorated_function


@blueprint.before_app_request
def before_request():
    """Make installed modules available to all templates and set current module"""
    g.installed_modules = current_app.config.get("INSTALLED_MODULES", {}).values()

    # Get current module from request path
    path = request.path.split("/")[1] or "core"  # Default to core if root path

    # First try to find the current module
    current_module = next(
        (m for m in g.installed_modules if m.get("name", "").lower() == path.lower()), None
    )

    # If not found, try to find core module
    if not current_module:
        current_module = next(
            (m for m in g.installed_modules if m.get("name", "").lower() == "core"),
            {"name": "Core", "type": "App"}  # Minimal default if core not found
        )

    # Always ensure g.current_module exists
    g.current_module = current_module or {"name": "Core", "type": "App"}


@blueprint.route("/")
@login_required
def index():
    """Redirect root to people dashboard"""
    return redirect(url_for("people_bp.people_home"))


@blueprint.route("/login", methods=["GET", "POST"])
def login():
    # Redirect if user is already logged in
    if current_user.is_authenticated:
        return redirect(url_for("people_bp.people_home"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        remember = bool(request.form.get("remember"))

        user = User.get_by_email(email)
        if user and user.check_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get("next")
            # Ensure the next page is safe and default to people dashboard
            if not next_page or not next_page.startswith("/"):
                next_page = url_for("people_bp.people_home")
            return redirect(next_page)

        flash("Invalid email or password", "error")

    return render_template("login.html")


@blueprint.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("core_bp.login"))


@blueprint.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        first_name = request.form.get("first_name")
        last_name = request.form.get("last_name")

        if User.get_by_email(email):
            flash("Email already registered", "error")
            return render_template("register.html")

        try:
            User.create(email=email, password=password, first_name=first_name, last_name=last_name)
            flash("Registration successful! Please login.", "success")
            return redirect(url_for("core_bp.login"))
        except Exception as e:
            flash("Registration failed", "error")

    return render_template("register.html")


SUPPORTED_LANGUAGES = {"en": "English", "es": "Español", "nl": "Nederlands"}


@blueprint.route("/settings/language", methods=["POST"])
@login_required
def update_language():
    lang = request.form.get("language")
    if lang in SUPPORTED_LANGUAGES:
        session["lang"] = lang
        if current_user.is_authenticated:
            UserSetting.set(current_user.id, "language", lang)
        return jsonify({"success": True})
    return jsonify({"error": "Invalid language"}), 400


@blueprint.route("/settings")
@login_required
def settings():
    """Settings page"""
    return render_template(
        "settings/index.html",
        title="Settings",
        languages=SUPPORTED_LANGUAGES,
        current_language=session.get("lang", "en"),
        module_name="Settings",
        module_icon="fa-solid fa-cog",
        module_home="core_bp.settings",
    )


@blueprint.route("/settings/apps")
@login_required
@admin_required
def manage_apps():
    """Apps management page"""
    modules_dir = "modules"
    modules = []

    for module_name in os.listdir(modules_dir):
        module_path = os.path.join(modules_dir, module_name)

        if os.path.isdir(module_path) and not module_name.startswith("__"):
            try:
                # Load the manifest
                manifest = importlib.import_module(f"modules.{module_name}.__manifest__").manifest

                # Check if module is disabled
                disabled_file = os.path.join(module_path, "__DISABLED__")
                manifest["enabled"] = not os.path.exists(disabled_file)

                modules.append(manifest)
            except Exception as e:
                print(f"Error loading manifest for {module_name}: {e}")

    return render_template(
        "settings/apps.html",
        modules=sorted(modules, key=lambda x: x["name"]),
        module_name="Settings",
        module_icon="fa-solid fa-cog",
        module_home="core_bp.settings",
        installed_modules=g.installed_modules,
    )


@blueprint.route("/api/modules/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_module():
    """Toggle module enabled/disabled state"""
    data = request.get_json()
    module_name = data.get("module")
    enabled = data.get("enabled")

    if not module_name:
        return jsonify({"error": "Module name required"}), 400

    module_path = os.path.join("modules", module_name)
    disabled_file = os.path.join(module_path, "__DISABLED__")

    try:
        if enabled and os.path.exists(disabled_file):
            os.remove(disabled_file)
        elif not enabled and not os.path.exists(disabled_file):
            open(disabled_file, "a").close()

        # After toggling, trigger a restart
        if current_app.debug:
            main_app_file = os.path.abspath(sys.modules["__main__"].__file__)
            print(f"Debug mode: Triggering reload by touching {main_app_file}")
            os.utime(main_app_file, None)

        return jsonify(
            {
                "success": True,
                "message": f"Module {module_name} {'enabled' if enabled else 'disabled'}. Restarting application...",
            }
        )
    except Exception as e:
        print(f"Error toggling module: {e}")
        print(f"Error type: {type(e)}")
        return jsonify({"error": str(e)}), 500


@blueprint.route("/restart")
@login_required
def system_restart():
    """Restart the application"""
    if current_user.is_admin:
        python = sys.executable
        os.execl(python, python, *sys.argv)
    return redirect(url_for("core_bp.index"))


@blueprint.route("/exception")
@login_required
@admin_required
def test_exception():
    """Test route to trigger a 500 error page"""
    # Deliberately raise an exception
    raise Exception("This is a test exception to verify the 500 error page functionality")


# Add these error handlers to the routes.py file
@blueprint.errorhandler(Exception)
def handle_exception(error):
    """Handle all exceptions"""

    return render_template(
        "errors/500.html",
        error=error,
        module_name="Error",
        module_icon="fa-solid fa-exclamation-triangle",
        module_home="core_bp.index",
    ), 500


@blueprint.app_errorhandler(500)
def internal_error(error):
    """Handle internal server errors"""

    return render_template(
        "errors/500.html",
        error=error,
        module_name="Error",
        module_icon="fa-solid fa-exclamation-triangle",
        module_home="core_bp.index",
    ), 500


@blueprint.app_errorhandler(404)
def not_found_error(error):
    """Handle 404 errors"""
    return render_template(
        "errors/404.html",
        error=error,
        module_name="Error",
        module_icon="fa-solid fa-exclamation-triangle",
        module_home="core_bp.index",
    ), 404


@blueprint.route("/language/<lang_code>", methods=["POST"])
@login_required
def change_language(lang_code):
    """Change user's language preference"""
    if lang_code not in current_app.config["LANGUAGES"]:
        return jsonify({"error": "Invalid language code"}), 400

    try:
        if current_user.update_setting("language", lang_code):
            session["language"] = lang_code
            return jsonify({"success": True})
        return jsonify({"error": "Failed to update language setting"}), 500

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
