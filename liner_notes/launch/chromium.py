"""The page as a Chromium app window with its own profile.

Its own --user-data-dir means its own browser process, so Hyprland's placement rules land on the
window instead of being dropped when Chromium hands the window to a browser that's already open.
There is no remote debugging port (anything local could drive the page through it); tests use
Playwright's pipe instead.
"""
BROWSERS = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "brave")


def find(system, preferred=""):
    for b in ([preferred] if preferred else []) + list(BROWSERS):
        if b and system.which(b):
            return b
    return None


def app_argv(browser, profile, url):
    return [browser, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--autoplay-policy=no-user-gesture-required", f"--app={url}"]


def uses_profile(cmdline, profile):
    return f"--user-data-dir={profile}" in cmdline
